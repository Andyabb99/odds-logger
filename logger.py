"""Paper-only value-bet logger. Never places bets; only logs to a Google Form."""
import csv, html, json, os, sys, urllib.error, urllib.parse, urllib.request
from datetime import datetime, timedelta, timezone

API = "https://api.the-odds-api.com/v4/sports"
SPORT = "soccer_epl"
BOOKS = "nordicbet,pinnacle"
CREDITS_PER_ODDS_CALL = 2  # 2 markets x 1 bookmaker group
STATE_FILE = "state.json"
CFG = json.load(open("config.json"))


def now():
    return datetime.now(timezone.utc)


def parse_t(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def get(path, **params):
    params["apiKey"] = os.environ["ODDS_API_KEY"]
    url = f"{API}/{SPORT}/{path}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.load(r), r.headers


def fair_probs(prices):
    """prices: {outcome: decimal odds} -> proportionally de-margined probabilities."""
    inv = {k: 1 / v for k, v in prices.items()}
    total = sum(inv.values())
    return {k: v / total for k, v in inv.items()}


def market_prices(event, book, key, point=None):
    for b in event.get("bookmakers", []):
        if b["key"] != book:
            continue
        for m in b["markets"]:
            if m["key"] == key:
                outs = [o for o in m["outcomes"] if point is None or o.get("point") == point]
                return {o["name"]: o["price"] for o in outs}
    return {}


def label(event, key, outcome, point):
    if key == "totals":
        return f"{outcome} {point}"
    if outcome == event["home_team"]:
        return "1X2 Home"
    if outcome == event["away_team"]:
        return "1X2 Away"
    return "1X2 Draw"


def all_rows(event):
    """Every outcome Pinnacle prices fully, with NordicBet odds/edge where available."""
    rows = []
    # Totals: 2.5 always, plus any other whole/half line (1.5, 3.5, ...) that NordicBet also prices.
    # The API returns every listed line for the same credits. Quarter lines are skipped (half-push).
    lines = {o.get("point") for b in event.get("bookmakers", []) if b["key"] == "nordicbet"
             for m in b["markets"] if m["key"] == "totals" for o in m["outcomes"]}
    pts = [2.5] + sorted(p for p in lines if p is not None and p != 2.5 and (p * 2) % 1 == 0)
    for key, point, n in (("h2h", None, 3),) + tuple(("totals", p, 2) for p in pts):
        pin = market_prices(event, "pinnacle", key, point)
        nor = market_prices(event, "nordicbet", key, point)
        if len(pin) != n:
            continue
        for outcome, p in fair_probs(pin).items():
            nord = nor.get(outcome)
            rows.append(dict(market=label(event, key, outcome, point), nordic_odds=nord,
                             pinnacle_odds=pin[outcome], fair_prob=p,
                             edge=(nord * p - 1) if nord else None))
    return rows


def totals_gap_rows(event, t, mins):
    """Raw totals lines (all points, quarter lines too) from both books when either lacks a full Over/Under 2.5 pair.
    A book with no totals at all gets one row with empty point/outcome/price."""
    raw = {}
    for book in ("pinnacle", "nordicbet"):
        raw[book] = [o for b in event.get("bookmakers", []) if b["key"] == book
                     for m in b["markets"] if m["key"] == "totals" for o in m["outcomes"]]
    missing = [b for b, outs in raw.items()
               if {o["name"] for o in outs if o.get("point") == 2.5} != {"Over", "Under"}]
    if not missing:
        return []
    base = dict(time=t.isoformat(), event_id=event["id"], kickoff=event["commence_time"], mins_to_kickoff=mins,
                match=f"{event['home_team']} - {event['away_team']}", missing="+".join(missing))
    return [dict(base, book=b, point=o.get("point"), outcome=o["name"], price=o["price"])
            for b, outs in raw.items() for o in outs] +            [dict(base, book=b) for b, outs in raw.items() if not outs]


def find_edges(event, min_edge):
    return [r for r in all_rows(event) if r["edge"] is not None and r["edge"] >= min_edge]


def local_date(kickoff):
    try:
        from zoneinfo import ZoneInfo
        return parse_t(kickoff).astimezone(ZoneInfo("Europe/Helsinki")).strftime("%Y-%m-%d")
    except Exception:
        return parse_t(kickoff).strftime("%Y-%m-%d")


def post_form(home, away, kickoff, market, odds, test=False, source="Model (paper)"):
    url = os.environ["FORM_URL"]  # .../formResponse
    f = CFG["form_fields"]
    data = {
        f["date"] + "_year": local_date(kickoff)[:4],
        f["date"] + "_month": str(int(local_date(kickoff)[5:7])),
        f["date"] + "_day": str(int(local_date(kickoff)[8:10])),
        f["match"]: "TEST - DELETE THIS ROW" if test else f"{home} - {away}",
        f["league"]: "TEST" if test else "Premier League",
        f["market"]: market,
        f["bet_type"]: "Single",
        f["odds"]: f"{odds:.2f}",
        f["stake"]: "1",
        f["bookmaker"]: "NordicBet",
        f["source"]: source,
    }
    req = urllib.request.Request(url, urllib.parse.urlencode(data).encode(), method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.status < 400


SNAP_FILE, BETS_FILE, GAPS_FILE = "snapshots.csv", "bets.csv", "totals_gaps.csv"
GAP_COLS = ["time", "event_id", "match", "kickoff", "mins_to_kickoff", "missing", "book", "point", "outcome", "price"]
SNAP_COLS = ["time", "event_id", "match", "kickoff", "mins_to_kickoff", "market",
             "nordic_odds", "pinnacle_odds", "fair_prob", "edge"]
BET_COLS = ["key", "logged_at", "match", "kickoff", "market", "open_nordic_odds", "open_pinnacle_odds",
            "open_fair_prob", "open_edge", "close_time", "close_nordic_odds", "close_pinnacle_odds",
            "close_fair_prob", "close_edge", "type"]


# ---------- Telegram digest / alerts ----------
TG_LIMIT = 3800  # Telegram caps messages at 4096 chars


def tg_send(text, attempts=2):
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        print("Telegram not configured (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID); skipping.")
        return False
    data = urllib.parse.urlencode(dict(chat_id=chat, text=text, parse_mode="HTML",
                                       disable_web_page_preview="true")).encode()
    for _ in range(attempts):
        try:
            with urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage", data, timeout=30) as r:
                return r.status < 400
        except urllib.error.HTTPError as e:  # never print the URL: it contains the bot token
            print("Telegram send failed:", e.code, e.read().decode(errors="replace")[:200])
        except Exception as e:
            print("Telegram send failed:", type(e).__name__)
    return False


def notify(text, dry):
    if dry:
        print("[telegram dry-run]\n" + text)
        return True
    return tg_send(text)


def helsinki(dt):
    try:
        from zoneinfo import ZoneInfo
        return dt.astimezone(ZoneInfo("Europe/Helsinki"))
    except Exception:  # no tz database: EEST from last Sun Mar 01:00 UTC to last Sun Oct 01:00 UTC
        def last_sun(m):
            d = datetime(dt.year, m, 31, 1, tzinfo=timezone.utc)
            return d - timedelta(days=(d.weekday() + 1) % 7)
        return dt.astimezone(timezone(timedelta(hours=3 if last_sun(3) <= dt < last_sun(10) else 2)))


def hhmm(kickoff):
    return helsinki(parse_t(kickoff)).strftime("%H:%M")


def short(market):
    return {"1X2 Home": "H", "1X2 Draw": "D", "1X2 Away": "A"}.get(market) or \
        market.replace("Over ", "O").replace("Under ", "U")


def first_odds_today(day):
    """{(event_id, market): first NordicBet odds seen on this Helsinki day} from snapshots.csv."""
    out = {}
    if os.path.exists(SNAP_FILE):
        for r in csv.DictReader(open(SNAP_FILE, newline="")):
            if r["nordic_odds"] and local_date(r["time"]) == day:
                out.setdefault((r["event_id"], r["market"]), float(r["nordic_odds"]))
    return out


def arrow(odds, first):
    if first is None or abs(odds - first) < 0.005:
        return ""
    return f" {'▲' if odds > first else '▼'}{abs(odds - first):.2f}"


def match_block(m, first):
    """m: dict(id, name, kickoff, rows). Star = best edge (diamond if even the best is negative)."""
    rows = m["rows"]
    fair = " · ".join(f"{short(r['market'])} {r['fair_prob'] * 100:.0f}%" for r in rows)
    best = max((r for r in rows if r["nordic_odds"]), key=lambda r: r["edge"], default=None)
    out = [f"<b>{html.escape(m['name'])}</b> · {hhmm(m['kickoff'])}", f"Pinnacle fair: {fair}"]
    for r in rows:
        name = r["market"].replace("1X2 ", "")
        fo = f"{1 / r['fair_prob']:.2f}"
        if not r["nordic_odds"]:
            out.append(f"▫️ {name}: no NordicBet price · fair {fo}")
            continue
        body = (f"{name} {r['nordic_odds']:.2f} · fair {fo} · {r['edge'] * 100:+.1f}%"
                f"{arrow(r['nordic_odds'], first.get((m['id'], r['market'])))}")
        if r is best:
            out.append(f"{'⭐' if r['edge'] >= 0 else '🔹'} <b>{body}</b>")
        else:
            out.append(f"▫️ {body}")
    return "\n".join(out)


def parlay_text(matches):
    """Best leg per match with edge >= 0 (legs from one match are mutually exclusive or correlated)."""
    legs = []
    for m in matches:
        ok = [r for r in m["rows"] if r["edge"] is not None and r["edge"] >= 0]
        if ok:
            legs.append((m, max(ok, key=lambda r: r["edge"])))
    if not legs:
        return "no qualifying legs"
    if len(legs) == 1:
        return "only 1 qualifying leg - no parlay"
    odds = prob = 1.0
    for _, r in legs:
        odds *= r["nordic_odds"]
        prob *= r["fair_prob"]
    lines = [f"• {html.escape(m['name'])}: {r['market'].replace('1X2 ', '')} @{r['nordic_odds']:.2f} "
             f"({r['edge'] * 100:+.1f}%)" for m, r in legs]
    lines.append(f"Combined @{odds:.2f} · fair {1 / prob:.2f} · <b>edge {(odds * prob - 1) * 100:+.1f}%</b>")
    return "\n".join(lines)


def build_digest(matches, first, min_edge, title):
    """Messages for one kickoff slot (split on match boundaries if too long)."""
    vb = [f"• {html.escape(m['name'])}: {r['market'].replace('1X2 ', '')} @{r['nordic_odds']:.2f} "
          f"(fair {1 / r['fair_prob']:.2f}, <b>{r['edge'] * 100:+.1f}%</b>)"
          for m in matches for r in m["rows"] if r["edge"] is not None and r["edge"] >= min_edge]
    tail = (f"<b>Value bets ({min_edge * 100:.0f}%+)</b>\n" + ("\n".join(vb) if vb else "none") +
            f"\n\n<b>Value-only parlay</b> (best leg per match, edge ≥ 0)\n{parlay_text(matches)}")
    msgs, cur = [], title
    for b in [match_block(m, first) for m in matches] + [tail]:
        if len(cur) + len(b) + 2 > TG_LIMIT and cur != title:
            msgs.append(cur)
            cur = title + " (cont.)"
        cur += "\n\n" + b
    return msgs + [cur]


def alert_text(m, r, mins):
    return (f"🚨 <b>Value bet</b> · {html.escape(m['name'])}\n"
            f"{r['market']} · NordicBet <b>{r['nordic_odds']:.2f}</b> · fair {1 / r['fair_prob']:.2f} · "
            f"edge <b>{r['edge'] * 100:+.1f}%</b>\n"
            f"Kickoff in {mins} min ({hhmm(m['kickoff'])})\n"
            f"Only bet if the odds are still this or better.")


def send_test_digest(dry=False):
    """Sample digest from the latest poll in snapshots.csv. Touches neither the form nor state.json."""
    if not os.path.exists(SNAP_FILE):
        sys.exit("snapshots.csv not found")
    rows = list(csv.DictReader(open(SNAP_FILE, newline="")))
    last = rows[-1]["time"]
    by_ev = {}
    for r in rows:
        if r["time"] == last:
            m = by_ev.setdefault(r["event_id"], dict(id=r["event_id"], name=r["match"],
                                                     kickoff=r["kickoff"], rows=[]))
            m["rows"].append(dict(market=r["market"], fair_prob=float(r["fair_prob"]),
                                  nordic_odds=float(r["nordic_odds"]) if r["nordic_odds"] else None,
                                  edge=float(r["edge"]) if r["edge"] else None))
    slots = {}
    for m in by_ev.values():
        slots.setdefault(m["kickoff"], []).append(m)
    first = first_odds_today(local_date(last))
    sent = 0
    for ko, ms in sorted(slots.items())[:3]:
        title = (f"🧪 <b>TEST digest</b> · KO {hhmm(ko)} (Helsinki)\n"
                 f"sample from snapshot {helsinki(parse_t(last)):%d.%m. %H:%M}")
        for text in build_digest(ms, first, CFG["min_edge"], title):
            sent += notify(text, dry)
    print(f"TEST telegram: {sent} message(s) sent")
    return sent > 0


def append_snap(rows):
    new = not os.path.exists(SNAP_FILE)
    with open(SNAP_FILE, "a", newline="") as f:
        w = csv.DictWriter(f, SNAP_COLS)
        if new:
            w.writeheader()
        w.writerows(rows)


def append_gaps(rows):
    if not rows:
        return
    new = not os.path.exists(GAPS_FILE)
    with open(GAPS_FILE, "a", newline="") as f:
        w = csv.DictWriter(f, GAP_COLS)
        if new:
            w.writeheader()
        w.writerows(rows)


def write_bets(bets):
    with open(BETS_FILE, "w", newline="") as f:
        w = csv.DictWriter(f, BET_COLS, extrasaction="ignore")
        w.writeheader()
        w.writerows({"type": "value", **b} for b in bets)


def load_state():
    try:
        return json.load(open(STATE_FILE))
    except FileNotFoundError:
        return {"logged": [], "events": [], "events_at": None, "month": None,
                "calls": 0, "last_poll": None, "remaining": None, "bets": []}


def save(st, dry):
    if not dry:
        json.dump(st, open(STATE_FILE, "w"), indent=1)


def main():
    dry = os.environ.get("DRY_RUN") == "1"
    st = load_state()
    st.setdefault("bets", [])
    st.setdefault("sent", [])  # alert|<key>, lean|<event>, digest|<kickoff>: never repeat these
    t = now()
    month = t.strftime("%Y-%m")
    if st["month"] != month:
        st.update(month=month, calls=0)

    # Schedule cache: /events costs no credits; refreshed every few hours.
    if not st["events_at"] or t - parse_t(st["events_at"]) > timedelta(hours=CFG["schedule_refresh_hours"]):
        ev, _ = get("events")
        st["events"] = [{"id": e["id"], "t": e["commence_time"]} for e in ev]
        st["events_at"] = t.isoformat()

    window = [e for e in st["events"]
              if t < parse_t(e["t"]) <= t + timedelta(hours=CFG["window_hours"])]
    if not window:
        print("No kickoff within window; skipping (0 credits).")
        return save(st, dry)

    if st["last_poll"] and t - parse_t(st["last_poll"]) < timedelta(minutes=CFG["min_poll_gap_minutes"] - 1):
        print("Throttled by min_poll_gap_minutes.")
        return save(st, dry)
    if (st["calls"] + 1) * CREDITS_PER_ODDS_CALL > CFG["monthly_credit_cap"] or \
            (st["remaining"] is not None and st["remaining"] <= CFG["reserve_credits"]):
        print("Credit budget exhausted; skipping.")
        return save(st, dry)

    odds, hdr = get("odds", bookmakers=BOOKS, markets="h2h,totals", oddsFormat="decimal",
                    eventIds=",".join(e["id"] for e in window))
    st["calls"] += 1
    st["last_poll"] = t.isoformat()
    rem = hdr.get("x-requests-remaining")
    st["remaining"] = int(float(rem)) if rem is not None else st["remaining"]
    print(f"credits remaining: {st['remaining']}")

    snaps, matches, gaps = [], [], []
    first = first_odds_today(local_date(t.isoformat()))  # first NordicBet odds per match/market today
    for ev in odds:
        mins = round((parse_t(ev["commence_time"]) - t).total_seconds() / 60)
        name = f"{ev['home_team']} - {ev['away_team']}"
        rows = all_rows(ev)
        gaps += totals_gap_rows(ev, t, mins)
        m = dict(id=ev["id"], name=name, kickoff=ev["commence_time"], rows=rows)
        matches.append(m)
        for r in rows:
            key = f"{ev['id']}|{r['market']}"
            edge = None if r["edge"] is None else round(r["edge"], 5)
            snaps.append(dict(time=t.isoformat(), event_id=ev["id"], kickoff=ev["commence_time"],
                              match=name, mins_to_kickoff=mins, market=r["market"],
                              nordic_odds=r["nordic_odds"], pinnacle_odds=r["pinnacle_odds"],
                              fair_prob=round(r["fair_prob"], 5), edge=edge))
            # logged bets (value and lean) keep their closing odds updated; the last pre-kickoff snapshot wins
            for bet in st["bets"]:
                if bet["key"].removesuffix("|lean") == key:
                    bet.update(close_time=t.isoformat(), close_nordic_odds=r["nordic_odds"],
                               close_pinnacle_odds=r["pinnacle_odds"],
                               close_fair_prob=round(r["fair_prob"], 5), close_edge=edge)
            if r["edge"] is None or r["edge"] < CFG["min_edge"] or key in st["logged"]:
                continue
            print("PAPER BET:", key, r)
            if dry or post_form(ev["home_team"], ev["away_team"], ev["commence_time"],
                                r["market"], r["nordic_odds"]):
                st["logged"].append(key)  # mark only after a successful log
                st["bets"].append(dict(key=key, type="value", logged_at=t.isoformat(), match=name,
                                       kickoff=ev["commence_time"], market=r["market"],
                                       open_nordic_odds=r["nordic_odds"],
                                       open_pinnacle_odds=r["pinnacle_odds"],
                                       open_fair_prob=round(r["fair_prob"], 5), open_edge=edge))
                save(st, dry)  # persist right after the POST so a later crash can't cause a re-log
                if f"alert|{key}" not in st["sent"] and notify(alert_text(m, r, mins), dry):
                    st["sent"].append(f"alert|{key}")
                    save(st, dry)
            else:
                print("Form POST failed; will retry next run.")

        # lean bet: first poll within lean_minutes of kickoff (polls are >= min_poll_gap apart, so it is the last)
        lean_key = f"lean|{ev['id']}"
        secs = (parse_t(ev["commence_time"]) - t).total_seconds()
        cands = [r for r in rows if r["edge"] is not None]
        if cands and secs <= CFG["lean_minutes"] * 60 and lean_key not in st["sent"]:
            best = max(cands, key=lambda r: r["edge"])
            key = f"{ev['id']}|{best['market']}"
            if key in st["logged"]:
                print("Lean skipped (already logged as value bet):", key)
                st["sent"].append(lean_key)
            else:
                print("LEAN BET:", key, best)
                if dry or post_form(ev["home_team"], ev["away_team"], ev["commence_time"],
                                    best["market"], best["nordic_odds"], source="Model (lean)"):
                    st["sent"].append(lean_key)
                    edge = round(best["edge"], 5)
                    st["bets"].append(dict(key=key + "|lean", type="lean", logged_at=t.isoformat(), match=name,
                                           kickoff=ev["commence_time"], market=best["market"],
                                           open_nordic_odds=best["nordic_odds"],
                                           open_pinnacle_odds=best["pinnacle_odds"],
                                           open_fair_prob=round(best["fair_prob"], 5), open_edge=edge,
                                           close_time=t.isoformat(), close_nordic_odds=best["nordic_odds"],
                                           close_pinnacle_odds=best["pinnacle_odds"],
                                           close_fair_prob=round(best["fair_prob"], 5), close_edge=edge))
                else:
                    print("Lean form POST failed; will retry next run.")
            save(st, dry)

    # digest: one message per kickoff slot, on the first poll 45-75 min before kickoff
    lo, hi = CFG["digest_window_minutes"]
    slots = {}
    for m in matches:
        secs = (parse_t(m["kickoff"]) - t).total_seconds()
        if lo * 60 <= secs <= hi * 60 and m["rows"] and f"digest|{m['kickoff']}" not in st["sent"]:
            slots.setdefault(m["kickoff"], []).append(m)
    for ko, ms in sorted(slots.items()):
        mins = round((parse_t(ko) - t).total_seconds() / 60)
        title = f"📅 <b>Matchday digest</b> · KO {hhmm(ko)} (Helsinki) · in {mins} min"
        if any([notify(text, dry) for text in build_digest(ms, first, CFG["min_edge"], title)]):
            st["sent"].append(f"digest|{ko}")
            save(st, dry)

    if not dry:
        append_snap(snaps)
        append_gaps(gaps)
        write_bets(st["bets"])
    save(st, dry)


if __name__ == "__main__":
    if "--test-telegram" in sys.argv:
        sys.exit(0 if send_test_digest() else 1)
    elif "--test-form" in sys.argv:
        ok = post_form("TEST", "TEST", now().isoformat(), "Over 2.5", 1.0, test=True)
        print("TEST form submission:", "OK" if ok else "FAILED")
    else:
        main()
