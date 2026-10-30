"""Estimate monthly credit use from the free /events schedule (costs 0 credits).
Usage: ODDS_API_KEY=... python estimate.py [gap_minutes=30]"""
import sys
from datetime import timedelta
import logger

gap = int(sys.argv[1]) if len(sys.argv) > 1 else 30
ev, _ = logger.get("events")
ks = sorted(logger.parse_t(e["commence_time"]) for e in ev)
if not ks:
    sys.exit("no upcoming events")
t = ks[0] - timedelta(hours=3)
t = t.replace(minute=t.minute // 15 * 15, second=0, microsecond=0)
polls, last = 0, None
while t <= ks[-1]:
    if any(t < k <= t + timedelta(hours=3) for k in ks) and (last is None or (t - last).total_seconds() >= gap * 60):
        polls += 1
        last = t
    t += timedelta(minutes=15)
days = max((ks[-1] - ks[0]).days, 1)
print(f"{len(ks)} fixtures over {days}d; polls={polls}; credits={polls * 2}; "
      f"~{polls * 2 * 30 / days:.0f}/month at {gap}-min spacing")
