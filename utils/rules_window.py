"""Resolution window (start, end) parsed from a market's rules text.

Why: two markets can ask the same question over DIFFERENT windows.
Kalshi "hurricane landfall in Hawaii" counts landfalls "during the 2026
hurricane season" (ends Nov 30); Polymarket counts "between market creation
and December 31, 2026". A YES+NO basket is only a hedge if the YES leg's
window CONTAINS the NO leg's: then any event that pays the NO side's
counterparty... more simply — whenever the NO leg loses (event happened in
its window) the YES leg wins (same event is inside the YES window too).
Buying YES on the NARROWER window loses both legs when the event lands in
the gap (Hawaii basket, 2026-09-24: YES Kalshi + NO Polymarket).

Only explicit dates are used; unknown bounds ("after issuance", "between
market creation and ...") are treated as unknown and never flag.
"""

import re
from datetime import date, timedelta

_MON = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
_DATE = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{1,2}),?\s+(20\d\d)"


def _d(m) -> date | None:
    try:
        return date(int(m.group(3)), _MON[m.group(1)[:3]], int(m.group(2)))
    except (ValueError, KeyError):
        return None


def window(text) -> tuple[date | None, date | None]:
    """(start, end) of the resolution window, None where not explicit."""
    t = str(text or "").lower()
    start = end = None
    # Start: "between <date> and", "starting <date>", "from <date>"
    m = re.search(r"\b(?:between|starting|beginning|from)\s+" + _DATE, t)
    if m:
        start = _d(m)
    # End: "and|before|by|through|until|no later than <date>" — take the LAST
    ends = [_d(m) for m in re.finditer(
        r"\b(?:and|before|by|through|until|no later than|prior to)\s+(?:\d{1,2}:\d{2}\s*(?:am|pm)?\s*(?:et|pt|utc)?\s+on\s+)?" + _DATE, t)]
    ends = [e for e in ends if e]
    if ends:
        end = ends[-1]
    # Seasonal windows: "during the 2026 (Atlantic|Pacific) hurricane season"
    # — NOAA season ends Nov 30 (Atlantic and Central/Eastern Pacific).
    m = re.search(r"during the (20\d\d)\s+(?:atlantic\s+|pacific\s+|central pacific\s+)?hurricane season", t)
    if m:
        season_end = date(int(m.group(1)), 11, 30)
        end = season_end if end is None else min(end, season_end)
    return start, end


def yes_window_contains_no(yes_text, no_text, slack_days=1,
                           yes_end_fallback=None, no_end_fallback=None, today=None) -> tuple[bool, str]:
    """(ok, why). ok=False when the YES leg's window is provably narrower
    than the NO leg's (the basket can lose both legs).

    *_end_fallback: a hard end date (a Kalshi market's close) used only when
    that leg's rules text states none. The caller passes it for match types
    that skip the human family review (2026-09-25: Kalshi "LA-05 Republican
    nominee?" has no date and closes Nov 2027; Polymarket resolves "Other"
    if no nominee by Nov 3, 2026, and the May 16 primary was still
    unresolved in late September — YES Polymarket + NO Kalshi loses both
    legs if the nominee is named after Nov 3)."""
    ys, ye = window(yes_text)
    ns, ne = window(no_text)
    ye = ye or yes_end_fallback
    ne = ne or no_end_fallback
    slack = timedelta(days=slack_days)
    if ye and ne and ye + slack < ne:
        return False, f"YES window ends {ye} before NO window ends {ne}"
    # A later YES start only matters while that gap is still ahead: once
    # it's past, an event inside it would already have settled (or priced
    # at ~0/100) the NO leg, which the settled-one-side / implausible-return
    # checks catch. (2026-09-25: Polymarket "Will Bitcoin dip to $75,000 in
    # September?" counts from its Sep 16 creation, Kalshi from Sep 1.)
    if ys and ns and ys > ns + slack and ys > (today or date.today()):
        return False, f"YES window starts {ys} after NO window starts {ns}"
    return True, ""
