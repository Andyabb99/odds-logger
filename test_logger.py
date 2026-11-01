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
