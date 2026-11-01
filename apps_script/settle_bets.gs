/**
 * Bet_tracker auto-settler.
 * Reads the "Bets" tab and writes ONLY column K (Result) and column L (Closing odds), and only into empty cells.
 * Activity goes to the "Settle log" tab. Secrets live in Script Properties:
 *   FOOTBALL_DATA_TOKEN  - football-data.org API token
 *   GITHUB_TOKEN         - fine-grained, read-only (Contents) token for Andyabb99/odds-logger
 */
var CFG = {
  BETS_TAB: 'Bets',
  LOG_TAB: 'Settle log',
  TZ: 'Europe/Helsinki',
  GITHUB_REPO: 'Andyabb99/odds-logger',
  GITHUB_FILE: 'bets.csv',
  CLOSING_COL: 'close_nordic_odds',   // or 'close_pinnacle_odds'
  MIN_DAYS_AGO: 1,                    // settle only matches at least this many days old
  KICKOFF_BUFFER_MIN: 30,             // closing odds are only taken this long after kickoff (bot stops updating then)
  LOG_MAX_ROWS: 1000
};
var COL = { DATE: 2, MATCH: 3, LEAGUE: 4, MARKET: 5, SOURCE: 10, RESULT: 11, CLOSING: 12 };

// League text in column D -> football-data.org competition code (free tier). Blank league = Premier League.
var LEAGUES = [
  ['premier league', 'PL'], ['championship', 'ELC'], ['la liga', 'PD'], ['primera division', 'PD'],
  ['serie a', 'SA'], ['bundesliga', 'BL1'], ['ligue 1', 'FL1'], ['eredivisie', 'DED'],
  ['primeira liga', 'PPL'], ['champions league', 'CL']
];

// normalised alias -> normalised canonical name (after FC/AFC/CF/SC are stripped)
var ALIASES = {
  'man utd': 'manchester united', 'man united': 'manchester united', 'manchester utd': 'manchester united',
  'man city': 'manchester city', 'spurs': 'tottenham hotspur', 'tottenham': 'tottenham hotspur',
  'wolves': 'wolverhampton wanderers', 'wolverhampton': 'wolverhampton wanderers',
  'newcastle': 'newcastle united', 'newcastle utd': 'newcastle united',
  'west ham': 'west ham united', 'leeds': 'leeds united', 'leicester': 'leicester city',
  'nottm forest': 'nottingham forest', 'nott m forest': 'nottingham forest', 'forest': 'nottingham forest',
  'brighton': 'brighton and hove albion', 'brighton hove albion': 'brighton and hove albion',
  'sheffield utd': 'sheffield united', 'sheffield weds': 'sheffield wednesday', 'west brom': 'west bromwich albion',
  'qpr': 'queens park rangers', 'norwich': 'norwich city', 'ipswich': 'ipswich town', 'luton': 'luton town',
  'inter': 'internazionale milano', 'inter milan': 'internazionale milano', 'internazionale': 'internazionale milano',
  'ac milan': 'milan', 'atletico madrid': 'club atletico de madrid', 'atletico': 'club atletico de madrid',
  'athletic bilbao': 'athletic club', 'real sociedad': 'real sociedad de futbol',
  'bayern munich': 'bayern munchen', 'bayern': 'bayern munchen', 'dortmund': 'borussia dortmund',
  'leverkusen': 'bayer 04 leverkusen', 'bayer leverkusen': 'bayer 04 leverkusen',
  'gladbach': 'borussia monchengladbach', 'rb leipzig': 'rasenballsport leipzig', 'leipzig': 'rasenballsport leipzig',
  'psg': 'paris saint germain', 'paris sg': 'paris saint germain', 'marseille': 'olympique de marseille',
  'lyon': 'olympique lyonnais', 'ajax': 'afc ajax', 'psv': 'psv eindhoven', 'sporting cp': 'sporting clube de portugal',
  'sporting lisbon': 'sporting clube de portugal', 'benfica': 'sl benfica', 'porto': 'fc porto'
};
var GENERIC = ['united', 'city', 'town', 'real', 'club', 'athletic', 'sporting', 'de', 'and'];

// ---------- entry points ----------

/** Daily job: results, then closing odds. */
function settleBets() { run_(false); }

/** Same as settleBets but writes nothing to the sheet; shows what it WOULD do (View > Logs / Execution log). */
function settleBetsDryRun() { run_(true); }

/** Creates the daily ~09:00 Helsinki trigger (replaces any existing one for settleBets). */
function installTrigger() {
  ScriptApp.getProjectTriggers().forEach(function (t) {
    if (t.getHandlerFunction() === 'settleBets') ScriptApp.deleteTrigger(t);
  });
  ScriptApp.newTrigger('settleBets').timeBased().everyDays(1).atHour(9).inTimezone(CFG.TZ).create();
}

/** Checks both tokens work. Run once after setting Script Properties. */
function checkSetup() {
  var p = PropertiesService.getScriptProperties();
  var fd = UrlFetchApp.fetch('https://api.football-data.org/v4/competitions/PL',
    { headers: { 'X-Auth-Token': p.getProperty('FOOTBALL_DATA_TOKEN') || '' }, muteHttpExceptions: true });
  Logger.log('football-data.org: HTTP ' + fd.getResponseCode() + (fd.getResponseCode() === 200 ? ' OK' : ' ' + fd.getContentText()));
  var gh = githubFetch_('');
  Logger.log('GitHub ' + CFG.GITHUB_FILE + ': HTTP ' + gh.getResponseCode() + (gh.getResponseCode() === 200 ? ' OK' : ' ' + gh.getContentText()));
  var sheet = SpreadsheetApp.getActive().getSheetByName(CFG.BETS_TAB);
  Logger.log('Tab "' + CFG.BETS_TAB + '": ' + (sheet ? 'found, ' + sheet.getLastRow() + ' rows' : 'NOT FOUND'));
}

// ---------- orchestration ----------

function run_(dry) {
  var lock = LockService.getScriptLock();
  if (!lock.tryLock(30000)) return;
  var ctx = { dry: dry, log: [], ssTz: SpreadsheetApp.getActive().getSpreadsheetTimeZone() };
  try {
    var sheet = SpreadsheetApp.getActive().getSheetByName(CFG.BETS_TAB);
    if (!sheet) throw new Error('Tab "' + CFG.BETS_TAB + '" not found');
    var n = sheet.getLastRow() - 1;
    var rows = n < 1 ? [] : sheet.getRange(2, 1, n, COL.CLOSING).getValues().map(function (v, i) { return { r: i + 2, v: v }; });
    ctx.sheet = sheet;
    var steps = [['results', settleResults_], ['closing odds', fillClosing_]];
    steps.forEach(function (s) {
      try { s[1](rows, ctx); } catch (e) { note_(ctx, null, '', s[0], '', 'ERROR: ' + e.message); }
    });
  } catch (e) {
    note_(ctx, null, '', 'run', '', 'ERROR: ' + e.message);
  } finally {
    flushLog_(ctx);
    lock.releaseLock();
  }
}

// ---------- step 1: results ----------

function settleResults_(rows, ctx) {
  var cutoff = dayNum_(todayStr_()) - CFG.MIN_DAYS_AGO;
  var byComp = {};
  rows.forEach(function (row) {
    if (String(row.v[COL.RESULT - 1]).trim() !== '') return;
    var date = parseDate_(row.v[COL.DATE - 1], ctx.ssTz);
    if (!date || dayNum_(date) > cutoff) return;
    var teams = splitMatch_(row.v[COL.MATCH - 1]);
    if (!teams) return;
    if (!settle_(row.v[COL.MARKET - 1], 1, 0)) return;  // unsupported market: leave blank
    var code = compCode_(row.v[COL.LEAGUE - 1]);
    if (!code) { note_(ctx, row, '', 'K', '', 'league not supported by football-data free tier'); return; }
    (byComp[code] = byComp[code] || []).push({ row: row, date: date, teams: teams });
  });

  var token = PropertiesService.getScriptProperties().getProperty('FOOTBALL_DATA_TOKEN');
  if (Object.keys(byComp).length && !token) throw new Error('FOOTBALL_DATA_TOKEN not set');
  var calls = 0;
  Object.keys(byComp).forEach(function (code) {
    var items = byComp[code];
    var nums = items.map(function (i) { return dayNum_(i.date); });
    var fixtures = [];
    for (var d = Math.min.apply(null, nums) - 1; d <= Math.max.apply(null, nums) + 1; d += 10) {
      if (calls++) Utilities.sleep(6500);  // free tier: 10 requests/minute
      var to = Math.min(d + 9, Math.max.apply(null, nums) + 1);
      fixtures = fixtures.concat(fetchFixtures_(code, strDay_(d), strDay_(to), token));
    }
    fixtures = fixtures.map(prepFixture_);
    items.forEach(function (it) {
      var row = it.row, fx = findFixture_(fixtures, it.teams[0], it.teams[1], it.date);
      var market = row.v[COL.MARKET - 1];
      if (!fx) return note_(ctx, row, market, 'K', '', 'no matching fixture found');
      if (fx.status === 'CANCELLED') return writeCell_(ctx, row, COL.RESULT, 'K', 'P', 'match cancelled');
      if (fx.status !== 'FINISHED' || fx.h === null) return note_(ctx, row, market, 'K', '', 'not finished (' + fx.status + ')');
      var res = settle_(market, fx.h, fx.a);
      writeCell_(ctx, row, COL.RESULT, 'K', res, fx.h + '-' + fx.a);
    });
  });
}

function fetchFixtures_(code, from, to, token) {
  var url = 'https://api.football-data.org/v4/competitions/' + code + '/matches?dateFrom=' + from + '&dateTo=' + to;
  for (var attempt = 0; attempt < 2; attempt++) {
    var res = UrlFetchApp.fetch(url, { headers: { 'X-Auth-Token': token }, muteHttpExceptions: true });
    if (res.getResponseCode() === 429) { Utilities.sleep(61000); continue; }
    if (res.getResponseCode() !== 200) throw new Error('football-data ' + code + ' HTTP ' + res.getResponseCode() + ': ' + res.getContentText().slice(0, 150));
    return JSON.parse(res.getContentText()).matches || [];
  }
  throw new Error('football-data rate limit');
}

function prepFixture_(m) {
  var s = m.score || {};
  var ft = (s.duration && s.duration !== 'REGULAR' && s.regularTime) ? s.regularTime : (s.fullTime || {});
  var names = function (t) { return [t.name, t.shortName].filter(String).map(canon_); };
  return {
    status: m.status, h: ft.home == null ? null : ft.home, a: ft.away == null ? null : ft.away,
    date: Utilities.formatDate(new Date(m.utcDate), CFG.TZ, 'yyyy-MM-dd'),
    home: names(m.homeTeam), away: names(m.awayTeam)
  };
}

/** Returns W/L for supported markets, null otherwise. */
function settle_(market, h, a) {
  var m = String(market).trim().toLowerCase().replace(/\s+/g, ' ');
  switch (m) {
    case '1x2 home': return h > a ? 'W' : 'L';
    case '1x2 draw': return h === a ? 'W' : 'L';
    case '1x2 away': return a > h ? 'W' : 'L';
  }
  var t = /^(over|under) (\d+(?:\.\d+)?)$/.exec(m);  // any line: "Over 1.5", "Under 3.5", ...
  if (t) {
    var line = parseFloat(t[2]), goals = h + a;
    if (goals === line) return 'P';  // whole-number line landing exactly = push (same code as cancelled)
    return (t[1] === 'over' ? goals > line : goals < line) ? 'W' : 'L';
  }
  return null;
}

// ---------- step 2: closing odds ----------

function fillClosing_(rows, ctx) {
  var pending = rows.filter(function (row) {
    return String(row.v[COL.CLOSING - 1]).trim() === '' && parseDate_(row.v[COL.DATE - 1], ctx.ssTz) && splitMatch_(row.v[COL.MATCH - 1]);
  });
  if (!pending.length) return;
  var res = githubFetch_('');
  if (res.getResponseCode() !== 200) throw new Error('GitHub HTTP ' + res.getResponseCode() + ': ' + res.getContentText().slice(0, 150));
  var csv = Utilities.parseCsv(res.getContentText());
  var head = csv[0], ix = {};
  head.forEach(function (h, i) { ix[h] = i; });
  ['match', 'kickoff', 'market', CFG.CLOSING_COL].forEach(function (c) {
    if (!(c in ix)) throw new Error('bets.csv has no column "' + c + '"');
  });
  var entries = [];
  csv.slice(1).forEach(function (r) {
    var teams = splitMatch_(r[ix.match]), close = parseFloat(r[ix[CFG.CLOSING_COL]]), ko = new Date(r[ix.kickoff]);
    if (!teams || !(close > 1) || isNaN(ko)) return;
    entries.push({
      home: canon_(teams[0]), away: canon_(teams[1]), market: normMarket_(r[ix.market]), close: close, ko: ko,
      date: Utilities.formatDate(ko, CFG.TZ, 'yyyy-MM-dd')
    });
  });

  pending.forEach(function (row) {
    var market = row.v[COL.MARKET - 1], teams = splitMatch_(row.v[COL.MATCH - 1]);
    var date = parseDate_(row.v[COL.DATE - 1], ctx.ssTz);
    var cands = entries.filter(function (e) { return e.market === normMarket_(market); });
    var e = pick_(cands, teams[0], teams[1], date, function (x) { return { home: [x.home], away: [x.away], date: x.date }; });
    if (!e) return;  // no bot bet for this match/market: leave blank
    if (Date.now() < e.ko.getTime() + CFG.KICKOFF_BUFFER_MIN * 60000) return note_(ctx, row, market, 'L', '', 'closing odds not final yet (match not started)');
    var who = /me \(real\)/i.test(String(row.v[COL.SOURCE - 1])) ? 'bot bet match for real bet' : 'bot bet';
    writeCell_(ctx, row, COL.CLOSING, 'L', e.close, who + ', ' + CFG.CLOSING_COL);
  });
}

function githubFetch_(path) {
  var token = PropertiesService.getScriptProperties().getProperty('GITHUB_TOKEN');
  if (!token) throw new Error('GITHUB_TOKEN not set');
  return UrlFetchApp.fetch('https://api.github.com/repos/' + CFG.GITHUB_REPO + '/contents/' + CFG.GITHUB_FILE + path, {
    headers: { Authorization: 'Bearer ' + token, Accept: 'application/vnd.github.raw+json', 'X-GitHub-Api-Version': '2022-11-28' },
    muteHttpExceptions: true
  });
}

// ---------- matching ----------

function norm_(s) {
  return String(s).toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '')
    .replace(/&/g, ' and ').replace(/[^a-z0-9 ]+/g, ' ').replace(/\s+/g, ' ').trim();
}

function canon_(s) {
  var n = norm_(s).split(' ').filter(function (t) { return ['fc', 'afc', 'cf', 'sc'].indexOf(t) < 0; }).join(' ');
  return ALIASES[n] || n;
}

function normMarket_(m) { return String(m).trim().toLowerCase().replace(/\s+/g, ' '); }

function sameTeam_(a, b) {
  if (a === b) return true;
  var ta = a.split(' '), tb = b.split(' ');
  var small = ta.length <= tb.length ? ta : tb, big = ta.length <= tb.length ? tb : ta;
  var core = small.filter(function (t) { return GENERIC.indexOf(t) < 0; });
  return core.length > 0 && core.join('').length >= 4 && small.every(function (t) { return big.indexOf(t) >= 0; });
}

/** Best candidate whose home AND away match within +-1 day of `date`; exact name matches rank higher; ties = ambiguous = null. */
function pick_(items, home, away, date, view) {
  var h = canon_(home), a = canon_(away), best = null, bestScore = -Infinity, tie = false;
  items.forEach(function (it) {
    var v = view(it), dd = Math.abs(dayNum_(v.date) - dayNum_(date));
    if (dd > 1) return;
    var hm = v.home.filter(function (n) { return sameTeam_(h, n); }), am = v.away.filter(function (n) { return sameTeam_(a, n); });
    if (!hm.length || !am.length) return;
    var score = (hm.indexOf(h) >= 0 ? 5 : 0) + (am.indexOf(a) >= 0 ? 5 : 0) - dd;
    if (score > bestScore) { best = it; bestScore = score; tie = false; } else if (score === bestScore) tie = true;
  });
  return tie ? null : best;
}

function findFixture_(fixtures, home, away, date) {
  return pick_(fixtures, home, away, date, function (f) { return f; });
}

function splitMatch_(s) {
  var p = String(s).split(/\s+[-–—]\s+/);
  return p.length === 2 && p[0].trim() && p[1].trim() ? [p[0].trim(), p[1].trim()] : null;
}

function compCode_(league) {
  var l = norm_(league);
  if (!l) return 'PL';
  for (var i = 0; i < LEAGUES.length; i++) if (l.indexOf(LEAGUES[i][0]) >= 0) return LEAGUES[i][1];
  return null;
}

// ---------- dates ----------

function pad_(n) { return ('0' + n).slice(-2); }
function todayStr_() { return Utilities.formatDate(new Date(), CFG.TZ, 'yyyy-MM-dd'); }
function dayNum_(s) { var p = s.split('-'); return Math.round(Date.UTC(+p[0], +p[1] - 1, +p[2]) / 86400000); }
function strDay_(n) { return new Date(n * 86400000).toISOString().slice(0, 10); }

function parseDate_(v, tz) {
  if (v instanceof Date) return Utilities.formatDate(v, tz, 'yyyy-MM-dd');
  var s = String(v).trim(), m;
  if ((m = s.match(/^(\d{4})-(\d{1,2})-(\d{1,2})/))) return m[1] + '-' + pad_(m[2]) + '-' + pad_(m[3]);
  if ((m = s.match(/^(\d{1,2})[.\/](\d{1,2})[.\/](\d{4})/))) return m[3] + '-' + pad_(m[2]) + '-' + pad_(m[1]);
  return null;
}

// ---------- writing + logging (K and L only, never overwrite) ----------

function writeCell_(ctx, row, col, colName, value, why) {
  var cell = ctx.sheet.getRange(row.r, col);
  var market = row.v[COL.MARKET - 1];
  if (!cell.isBlank()) return note_(ctx, row, market, colName, '', 'skipped, cell already filled');
  if (!ctx.dry) cell.setValue(value);
  note_(ctx, row, market, colName, value, why);
}

function note_(ctx, row, market, field, value, why) {
  ctx.log.push([new Date(), row ? row.r : '', row ? row.v[COL.MATCH - 1] : '', market, field, value, (ctx.dry ? '[DRY RUN] ' : '') + why]);
}

function flushLog_(ctx) {
  var written = ctx.log.filter(function (l) { return l[5] !== '' && !/^ERROR|skipped/.test(l[6].replace('[DRY RUN] ', '')); }).length;
  var errors = ctx.log.filter(function (l) { return /ERROR/.test(l[6]); }).length;
  ctx.log.push([new Date(), '', '', '', 'SUMMARY', '', (ctx.dry ? '[DRY RUN] ' : '') + written + ' cell(s) ' + (ctx.dry ? 'would be ' : '') + 'written, ' + errors + ' error(s)']);
  if (ctx.dry) { ctx.log.forEach(function (l) { Logger.log(l.slice(1).join(' | ')); }); return; }
  var ss = SpreadsheetApp.getActive(), sh = ss.getSheetByName(CFG.LOG_TAB);
  if (!sh) {
    sh = ss.insertSheet(CFG.LOG_TAB);
    sh.appendRow(['Time', 'Row', 'Match', 'Market', 'Field', 'Value', 'Note']);
    sh.setFrozenRows(1);
  }
  sh.getRange(sh.getLastRow() + 1, 1, ctx.log.length, 7).setValues(ctx.log);
  var extra = sh.getLastRow() - 1 - CFG.LOG_MAX_ROWS;
  if (extra > 0) sh.deleteRows(2, extra);
}
