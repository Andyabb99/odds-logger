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
