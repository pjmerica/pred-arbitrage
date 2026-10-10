"""Rules-text template checks: approve a pair from its two rules texts.

Hand review (data/series_map.json) doesn't scale to the soccer season: on
2026-10-10 the review queue held 70+ unreviewed BTTS / correct-score
families (every European league). Kalshi and Polymarket generate those
markets from fixed templates, so instead of approving a league by name we
check the PAIR's own rules texts for the template clause that fixes the
period (2026-10-10: all 341 soccer BTTS/score pairs carried the same period
clause on both sides — 258 full match, 70 first half, 13 second half).

A pair passes only if BOTH texts state the SAME period and no other. The
outcome gate (utils/proposition.py) has already checked teams, date, period
words and exact-score orientation at match time.
"""

import re

# (period, Kalshi clause, Polymarket clause)
_SOCCER = [
    ("full",
     r"after 90 minutes plus stoppage time \(does not include extra time",
     r"first 90 minutes of regular play plus stoppage time|end of 90 minutes of regulation plus stoppage time"),
    ("1h",
     r"Only goals scored during the 1st Half",
     r"first 45 minutes of regular play plus first-half stoppage time"),
    ("2h",
     r"Only goals scored during the 2nd Half",
     r"second half of regular play plus second-half stoppage time"),
]

# Kalshi series the template applies to: both-teams-to-score (full / 1H /
# 2H) and correct score. NOT first-team-to-score: Kalshi counts extra time
# there and Polymarket doesn't, so those stay hand-reviewed (league phase).
SOCCER_SERIES = re.compile(r"^KX[A-Z0-9]*(?:BTTS|SCORE)$")


def soccer_period(kalshi_text, poly_text):
    """The shared period ('full' / '1h' / '2h') if both rules texts carry
    exactly one standard period clause and it's the same one, else None."""
    k = [p for p, kr, _ in _SOCCER if re.search(kr, str(kalshi_text or ""))]
    q = [p for p, _, pr in _SOCCER if re.search(pr, str(poly_text or ""), re.IGNORECASE)]
    return k[0] if len(k) == 1 and k == q else None
