"""Classify US-election market titles by what they actually resolve on.

Shared by scripts/matcher.py (match_political) and scripts/elections.py so
both election paths accept the same markets as "party wins the race".

Allowlist, not denylist: a title counts as a party-win market only if it
matches PARTY_WIN_RE *and* has none of the DERIVATIVE_RE markers. Before
this (2026-09-23) both paths accepted any title mentioning a party, and
once Polymarket listed margin-of-victory buckets for every race
("Will the Democratic Party candidate win the AZ-02 House election by
0%-3%?") those — being the highest-liquidity market per race — became the
Dem-win leg, producing 15 fake guaranteed arbs and links to the wrong prop.
"""

import re

# Markets about HOW a race is won (margin, closeness, round) rather than WHO
# wins. Never interchangeable with a plain party-win market.
DERIVATIVE_RE = re.compile(
    r"\bby\s+\d+(?:\.\d+)?\s*%"            # "win ... by 0%-3%", "by 45% or more"
    r"|\d+(?:\.\d+)?\s*%\s*(?:-|–|to)\s*\d"  # bare "0%-3%" bucket
    r"|\bmargin\b|\bwithin\b|\bclosest\b|\bflip(?:s|ped)?\b"
    r"|\bturnout\b|\bvote share\b|\bpopular vote\b|\bfirst round\b|\brunoff\b"
    r"|\bdifference between\b|\bhow many\b"
    # Combos / comparisons: "Democrats sweep the Senate and Governor",
    # "perform best among these tossup races".
    r"|\bsweep\b|\bcombo\b|\bamong\b|\bperform\b|\bboth\b"
    # Sub-state results: "which counties will Steve Hilton win? — Orange".
    r"|\bcount(?:y|ies)\b|\bdistricts?\b\s+will"
    # 2026-09-25: other offices share the state + "governor"/"senate"
    # words and got the governor/Senate race id ("Will the Democratic
    # Party candidate win the 2026 Vermont Lieutenant Governor election?"
    # -> 2026-GOV-VT; state legislature -> SEN/H).
    r"|\blieutenant\b|\blt\.?\s+gov|\battorney general\b|\bsecretary of state\b"
    r"|\bstate\s+(?:senate|house|legislature|assembly)\b|\bmayor"
    # Multi-race lists: "Will Democrats win the Texas, Michigan, and Maine
    # Senate seats?" was a plain SEN-MI Dem-win leg.
    r"|\b(?:seats|races|elections|governorships)\b",
    re.IGNORECASE,
)

# "Will [the|a] Democrat(ic|s) [Party] [candidate] win the ..." — covers
# Polymarket ("Will the Democratic Party win the AZ-02 House seat?", "Will
# the Democrats win the Maine Senate race in 2026?") and Kalshi raw titles
# ("Will Democratic win the House race for WI-1?", "Will a Republican win
# ...", "Will the Republican party win the governorship in ...").
PARTY_WIN_RE = re.compile(
    r"^\s*will\s+(?:the\s+|a\s+)?(democrat(?:ic|ics|s)?|republicans?)"
    r"(?:\s+party)?(?:\s+candidate)?\s+win\s+the\b",
    re.IGNORECASE,
)


_PARTY_WORD_RE = re.compile(r"\b(?:democrat\w*|republican\w*|gop)\b", re.IGNORECASE)

# Full state names, longest first so "west virginia" is one match rather
# than also "virginia".
_STATES = sorted([
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado",
    "connecticut", "delaware", "florida", "georgia", "hawaii", "idaho",
    "illinois", "indiana", "iowa", "kansas", "kentucky", "louisiana", "maine",
    "maryland", "massachusetts", "michigan", "minnesota", "mississippi",
    "missouri", "montana", "nebraska", "nevada", "new hampshire", "new jersey",
    "new mexico", "new york", "north carolina", "north dakota", "ohio",
    "oklahoma", "oregon", "pennsylvania", "rhode island", "south carolina",
    "south dakota", "tennessee", "texas", "utah", "vermont", "virginia",
    "washington", "west virginia", "wisconsin", "wyoming"], key=len, reverse=True)
_STATE_RE = re.compile(r"\b(" + "|".join(_STATES) + r")\b")


def is_derivative(title) -> bool:
    return isinstance(title, str) and bool(DERIVATIVE_RE.search(title))


def party_win_side(title) -> str | None:
    """'dem' / 'rep' if the title is a plain party-wins-the-race market,
    else None (derivative, candidate, primary, or unrecognised)."""
    if not isinstance(title, str) or is_derivative(title):
        return None
    m = PARTY_WIN_RE.match(title)
    if not m:
        return None
    # Exactly one party mention. "Will Democrats win the Alaska Governor
    # election and Republicans win the Alaska Senate election?" starts
    # like a party-win title but is a two-race combo (fake 43% arb).
    if len(_PARTY_WORD_RE.findall(title)) != 1:
        return None
    # A single race names at most one state (2026-09-25).
    if len(set(_STATE_RE.findall(title.lower()))) > 1:
        return None
    return "dem" if m.group(1).lower().startswith("d") else "rep"
