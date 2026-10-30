"""Paper-only value-bet logger. Never places bets; only logs to a Google Form."""
import csv, json, os, sys, urllib.parse, urllib.request
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
    for key, point, n in (("h2h", None, 3), ("totals", 2.5, 2)):
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


def find_edges(event, min_edge):
    return [r for r in all_rows(event) if r["edge"] is not None and r["edge"] >= min_edge]


def local_date(kickoff):
    try:
        from zoneinfo import ZoneInfo
        return parse_t(kickoff).astimezone(ZoneInfo("Europe/Helsinki")).strftime("%Y-%m-%d")
    except Exception:
        return parse_t(kickoff).strftime("%Y-%m-%d")


def post_form(home, away, kickoff, market, odds, test=False):
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
        f["source"]: "Model (paper)",
    }
    req = urllib.request.Request(url, urllib.parse.urlencode(data).encode(), method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.status < 400


SNAP_FILE, BETS_FILE = "snapshots.csv", "bets.csv"
SNAP_COLS = ["time", "event_id", "match", "kickoff", "mins_to_kickoff", "market",
             "nordic_odds", "pinnacle_odds", "fair_prob", "edge"]
BET_COLS = ["key", "logged_at", "match", "kickoff", "market", "open_nordic_odds", "open_pinnacle_odds",
            "open_fair_prob", "open_edge", "close_time", "close_nordic_odds", "close_pinnacle_odds",
            "close_fair_prob", "close_edge"]


def append_snap(rows):
    new = not os.path.exists(SNAP_FILE)
    with open(SNAP_FILE, "a", newline="") as f:
        w = csv.DictWriter(f, SNAP_COLS)
        if new:
            w.writeheader()
        w.writerows(rows)


def write_bets(bets):
    with open(BETS_FILE, "w", newline="") as f:
        w = csv.DictWriter(f, BET_COLS, extrasaction="ignore")
        w.writeheader()
        w.writerows(bets)


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

    snaps = []
    for ev in odds:
        mins = round((parse_t(ev["commence_time"]) - t).total_seconds() / 60)
        name = f"{ev['home_team']} - {ev['away_team']}"
        for r in all_rows(ev):
            key = f"{ev['id']}|{r['market']}"
            edge = None if r["edge"] is None else round(r["edge"], 5)
            snaps.append(dict(time=t.isoformat(), event_id=ev["id"], kickoff=ev["commence_time"],
                              match=name, mins_to_kickoff=mins, market=r["market"],
                              nordic_odds=r["nordic_odds"], pinnacle_odds=r["pinnacle_odds"],
                              fair_prob=round(r["fair_prob"], 5), edge=edge))
            # logged bets keep their closing odds updated; the last pre-kickoff snapshot wins
            for bet in st["bets"]:
                if bet["key"] == key:
                    bet.update(close_time=t.isoformat(), close_nordic_odds=r["nordic_odds"],
                               close_pinnacle_odds=r["pinnacle_odds"],
                               close_fair_prob=round(r["fair_prob"], 5), close_edge=edge)
            if r["edge"] is None or r["edge"] < CFG["min_edge"] or key in st["logged"]:
                continue
            print("PAPER BET:", key, r)
            if dry or post_form(ev["home_team"], ev["away_team"], ev["commence_time"],
                                r["market"], r["nordic_odds"]):
                st["logged"].append(key)  # mark only after a successful log
                st["bets"].append(dict(key=key, logged_at=t.isoformat(), match=name,
                                       kickoff=ev["commence_time"], market=r["market"],
                                       open_nordic_odds=r["nordic_odds"],
                                       open_pinnacle_odds=r["pinnacle_odds"],
                                       open_fair_prob=round(r["fair_prob"], 5), open_edge=edge))
            else:
                print("Form POST failed; will retry next run.")
    if not dry:
        append_snap(snaps)
        write_bets(st["bets"])
    save(st, dry)


if __name__ == "__main__":
    if "--test-form" in sys.argv:
        ok = post_form("TEST", "TEST", now().isoformat(), "Over 2.5", 1.0, test=True)
        print("TEST form submission:", "OK" if ok else "FAILED")
    else:
        main()
