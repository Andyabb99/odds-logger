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
