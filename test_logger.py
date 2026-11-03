import logger

p = logger.fair_probs({"H": 2.0, "D": 4.0, "A": 4.0})
assert abs(sum(p.values()) - 1) < 1e-9 and abs(p["H"] - 0.5) < 1e-9
ev = {"home_team": "A", "away_team": "B", "bookmakers": [
    {"key": "pinnacle", "markets": [
        {"key": "h2h", "outcomes": [{"name": "A", "price": 2.0}, {"name": "Draw", "price": 4.0}, {"name": "B", "price": 4.0}]},
        {"key": "totals", "outcomes": [{"name": "Over", "price": 1.9, "point": 2.5}, {"name": "Under", "price": 1.9, "point": 2.5},
                                       {"name": "Over", "price": 1.5, "point": 1.5}, {"name": "Under", "price": 2.6, "point": 1.5}]}]},
    {"key": "nordicbet", "markets": [
        {"key": "h2h", "outcomes": [{"name": "A", "price": 2.1}, {"name": "Draw", "price": 3.9}, {"name": "B", "price": 4.0}]},
        {"key": "totals", "outcomes": [{"name": "Over", "price": 2.1, "point": 2.5}, {"name": "Under", "price": 1.8, "point": 2.5}]}]}]}
r = logger.find_edges(ev, 0.03)
assert {x["market"] for x in r} == {"1X2 Home", "Over 2.5"}, r
print("ok")

# extra totals lines: only lines both books price get edges; NordicBet-only / Pinnacle-only lines are ignored
import copy
ev2 = copy.deepcopy(ev)
pin = [b for b in ev2["bookmakers"] if b["key"] == "pinnacle"][0]["markets"][1]["outcomes"]
nor = [b for b in ev2["bookmakers"] if b["key"] == "nordicbet"][0]["markets"][1]["outcomes"]
nor += [{"name": "Over", "price": 1.6, "point": 1.5}, {"name": "Under", "price": 2.4, "point": 1.5},   # both books
        {"name": "Over", "price": 3.0, "point": 3.5}, {"name": "Under", "price": 1.3, "point": 3.5},   # NordicBet only
        {"name": "Over", "price": 1.9, "point": 2.25}, {"name": "Under", "price": 1.9, "point": 2.25}]  # quarter line
pin += [{"name": "Over", "price": 2.5, "point": 4.5}, {"name": "Under", "price": 1.5, "point": 4.5}]    # Pinnacle only
mk = {x["market"]: x for x in logger.all_rows(ev2)}
assert {"Over 1.5", "Under 1.5"} <= set(mk) and mk["Over 1.5"]["edge"] is not None
assert not any(k in mk for k in ("Over 3.5", "Under 3.5", "Over 4.5", "Over 2.25")), mk.keys()
print("ok lines")

# digest from a snapshot holding both 1X2 and totals: every priced line shows, 2.5 always, totals count for value/lean
import csv, os, tempfile
ev3 = copy.deepcopy(ev)
nor3 = [b for b in ev3["bookmakers"] if b["key"] == "nordicbet"][0]["markets"][1]["outcomes"]
nor3[0]["price"] = 2.2  # Over 2.5 beats the 1X2 Home edge
nor3 += [{"name": "Over", "price": 1.6, "point": 1.5}, {"name": "Under", "price": 2.4, "point": 1.5}]
rows3 = logger.all_rows(ev3)
assert max((r for r in rows3 if r["edge"] is not None), key=lambda r: r["edge"])["market"] == "Over 2.5"  # lean pick sees totals
tmp = tempfile.mkdtemp()
snap = os.path.join(tmp, "snap.csv")
with open(snap, "w", newline="") as f:
    w = csv.DictWriter(f, logger.SNAP_COLS)
    w.writeheader()
    for r in rows3:
        w.writerow(dict(time="2026-11-01T15:30:00+00:00", event_id="e1", match="A - B", kickoff="2026-11-01T16:30:00Z",
                        mins_to_kickoff=60, market=r["market"], nordic_odds=r["nordic_odds"] or "",
                        pinnacle_odds=r["pinnacle_odds"], fair_prob=r["fair_prob"],
                        edge="" if r["edge"] is None else r["edge"]))
logger.SNAP_FILE = snap
sent = []
logger.notify = lambda text, dry: sent.append(text) or True
assert logger.send_test_digest()
text = "\n".join(sent)
for want in ("Home", "Draw", "Away", "Over 2.5", "Under 2.5", "Over 1.5", "Under 1.5", "Value bets (3%+)", "Over 2.5 @2.20"):
    assert want.replace("Over 2.5 @", "Over 2.5 @") in text or want in text, (want, text)
# 2.5 line priced by Pinnacle but not NordicBet -> still shown, flagged
ev4 = copy.deepcopy(ev)
[b for b in ev4["bookmakers"] if b["key"] == "nordicbet"][0]["markets"].pop(1)
blk = logger.match_block(dict(id="e1", name="A - B", kickoff="2026-11-01T16:30:00Z", rows=logger.all_rows(ev4)), {})
assert blk.count("no NordicBet price") == 2 and "Over 2.5" in blk and "Under 2.5" in blk, blk
print("ok digest")

# totals gap log: raw lines from both books (quarter lines too) when either lacks a full 2.5 pair
from datetime import datetime, timezone
T = datetime(2026, 11, 1, 15, 30, tzinfo=timezone.utc)
ev5 = copy.deepcopy(ev2)
ev5.update(id="e5", commence_time="2026-11-01T16:30:00Z")
assert logger.totals_gap_rows(ev5, T, 60) == []  # complete pair at 2.5 in both books
pin5 = [b for b in ev5["bookmakers"] if b["key"] == "pinnacle"][0]["markets"][1]["outcomes"]
pin5[:] = [o for o in pin5 if o["point"] != 2.5] + [{"name": "Over", "price": 1.8, "point": 2.75}]
g = logger.totals_gap_rows(ev5, T, 60)
assert {r["missing"] for r in g} == {"pinnacle"}
assert {(r["book"], r["point"]) for r in g} >= {("pinnacle", 2.75), ("nordicbet", 2.25), ("nordicbet", 2.5)}, g
[b for b in ev5["bookmakers"] if b["key"] == "nordicbet"][0]["markets"].pop(1)
g = logger.totals_gap_rows(ev5, T, 60)
assert {r["missing"] for r in g} == {"pinnacle+nordicbet"} and any(r["book"] == "nordicbet" and r.get("point") is None for r in g)
print("ok gaps")

# ---------- lean timing, late fallback, backfill (run in a scratch dir; form + telegram stubbed) ----------
import json, shutil
from datetime import timedelta
shutil.copy("config.json", tmp)
here = os.getcwd()
os.chdir(tmp)
logger.SNAP_FILE = "snapshots.csv"
logger.notify = lambda text, dry: True
posts = []
logger.post_form = lambda home, away, ko, market, odds, test=False, source="": posts.append((home, away, ko, market, odds, source)) or True


def read_bets():
    return list(csv.DictReader(open("bets.csv", newline="")))


# all edges negative: Away is the least negative -> lean must still be logged
KO = "2026-11-10T20:00:00Z"
ev6 = copy.deepcopy(ev)
ev6.update(id="e6", commence_time=KO)
for b in ev6["bookmakers"]:
    if b["key"] == "nordicbet":
        b["markets"][0]["outcomes"] = [{"name": "A", "price": 1.9}, {"name": "Draw", "price": 3.8}, {"name": "B", "price": 3.9}]
        b["markets"][1]["outcomes"] = [{"name": "Over", "price": 1.8, "point": 2.5}, {"name": "Under", "price": 1.8, "point": 2.5}]
assert all(r["edge"] < 0 for r in logger.all_rows(ev6))
assert logger.pick_lean(logger.all_rows(ev6))["market"] == "1X2 Away"


def poll(mins_before, event):
    T = datetime(2026, 11, 10, 20, 0, tzinfo=timezone.utc) - timedelta(minutes=mins_before)
    logger.now = lambda: T
    logger.get = lambda path, **kw: ([{"id": "e6", "commence_time": KO}], {}) if path == "events" \
        else ([event], {"x-requests-remaining": "400"})
    logger.main()


poll(95, ev6)                       # before the digest window: no lean yet
assert posts == [] and read_bets() == []
poll(60, ev6)                       # digest poll: lean logged although every edge is negative
assert len(posts) == 1 and posts[0][3] == "1X2 Away" and posts[0][5] == "Model (lean)", posts
b = read_bets()
assert len(b) == 1 and b[0]["type"] == "lean" and b[0]["late"] == "" and b[0]["open_edge"] and float(b[0]["open_edge"]) < 0
ev6b = copy.deepcopy(ev6)           # Pinnacle moves before the close: Away fair prob changes
for bk in ev6b["bookmakers"]:
    if bk["key"] == "pinnacle":
        bk["markets"][0]["outcomes"] = [{"name": "A", "price": 1.9}, {"name": "Draw", "price": 3.9}, {"name": "B", "price": 4.6}]
poll(30, ev6b)                      # later poll: no second lean, closing odds follow the last poll
assert len(posts) == 1 and len(read_bets()) == 1
b = read_bets()[0]
want = 1 / logger.fair_probs({"A": 1.9, "Draw": 3.9, "B": 4.6})["B"]
assert abs(float(b["close_fair_odds"]) - want) < 1e-3 and b["close_pinnacle_odds"] == "4.6", b
assert abs(float(b["open_fair_prob"]) - 0.25) < 1e-6 and b["open_pinnacle_odds"] == "4.0"
print("ok lean timing")

# fallback: match kicked off without a lean row -> next run logs it from the latest pre-kickoff snapshot, marked late
posts.clear()
for f in ("snapshots.csv", "state.json", "bets.csv"):
    os.remove(f)


def write_snaps(rows):
    with open("snapshots.csv", "w", newline="") as f:
        w = csv.DictWriter(f, logger.SNAP_COLS)
        w.writeheader()
        w.writerows(rows)


def snap_rows(eid, name, ko, mins, odds):
    """odds: {market: (nordic, pinnacle, fair, edge)}"""
    t = logger.parse_t(ko) - timedelta(minutes=mins)
    return [dict(time=t.isoformat(), event_id=eid, match=name, kickoff=ko, mins_to_kickoff=mins, market=m,
                 nordic_odds=v[0], pinnacle_odds=v[1], fair_prob=v[2], edge=v[3]) for m, v in odds.items()]


KO7 = "2026-11-11T20:00:00Z"
write_snaps(snap_rows("e7", "C - D", KO7, 60, {"1X2 Home": (2.0, 2.0, 0.48, -0.04), "1X2 Away": (3.0, 3.1, 0.31, -0.07)}) +
            snap_rows("e7", "C - D", KO7, 20, {"1X2 Home": (2.0, 2.0, 0.49, -0.02), "1X2 Away": (3.0, 3.1, 0.30, -0.10)}))
T = datetime(2026, 11, 11, 20, 5, tzinfo=timezone.utc)
logger.now = lambda: T
logger.get = lambda path, **kw: ([], {})   # no kickoff in the window: fallback must still run
logger.main()
assert len(posts) == 1 and posts[0][:4] == ("C", "D", KO7, "1X2 Home"), posts
b = read_bets()
assert len(b) == 1 and b[0]["late"] == "late" and b[0]["open_fair_prob"] == "0.49" and b[0]["close_fair_odds"] == str(round(1 / 0.49, 4)), b
logger.main()                       # next run: no duplicate
assert len(posts) == 1 and len(read_bets()) == 1
print("ok late fallback")

# backfill: nearest snapshot to 60 min picks, last snapshot closes, existing lean rows are left alone
posts.clear()
KO8, KO9, KO_OLD = "2026-11-08T15:00:00Z", "2026-11-09T15:00:00Z", "2026-10-30T15:00:00Z"
rows = []
rows += snap_rows("e8", "E - F", KO8, 90, {"1X2 Home": (2.0, 2.0, 0.40, -0.20), "Over 2.5": (2.0, 1.9, 0.52, 0.04)})
rows += snap_rows("e8", "E - F", KO8, 55, {"1X2 Home": (2.0, 2.0, 0.45, -0.10), "Over 2.5": (1.8, 1.9, 0.50, -0.10)})   # nearest to 60
rows += snap_rows("e8", "E - F", KO8, 15, {"1X2 Home": (2.0, 2.0, 0.50, 0.00), "Over 2.5": (1.8, 1.9, 0.54, -0.03)})
rows += snap_rows("e9", "G - H", KO9, 62, {"1X2 Draw": (3.5, 3.4, 0.30, -0.05)})
rows += snap_rows("e9", "G - H", KO9, 14, {"1X2 Draw": (3.5, 3.3, 0.31, -0.08)})
rows += snap_rows("eold", "I - J", KO_OLD, 60, {"1X2 Home": (2.0, 2.0, 0.5, 0.0)})      # before backfill_since
write_snaps(rows)
st = {"logged": [], "sent": ["lean|e9"], "bets": [
    dict(key="e9|1X2 Draw|lean", type="lean", match="G - H", close_fair_prob=0.28334)]}   # existing lean, no close_fair_odds
logger.now = lambda: datetime(2026, 11, 12, tzinfo=timezone.utc)
logger.migrate_bets(st)
assert st["bets"][0]["close_fair_odds"] == round(1 / 0.28334, 4)
logger.backfill(st, False)
assert len(posts) == 1, posts       # e8 only: e9 already has a lean row, eold predates the cutoff
assert posts[0][:3] == ("E", "F", KO8) and posts[0][3] == "1X2 Home", posts
nb = st["bets"][1]
assert nb["market"] == "1X2 Home" and nb["late"] == "backfill" and nb["open_fair_prob"] == 0.45
assert nb["logged_at"].startswith("2026-11-08T14:05") and nb["close_fair_odds"] == 2.0 and nb["close_pinnacle_odds"] == 2.0
assert logger.local_date(posts[0][2]) == "2026-11-08"  # form gets the match date, not today
logger.backfill(st, False)          # idempotent
assert len(posts) == 1 and len(st["bets"]) == 2
os.chdir(here)
print("ok backfill")
