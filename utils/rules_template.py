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
SOCCER_SERIES = re.compile(r"^KX[A-Z0-9]*(?:BTTS|SCORE|GAME|TOTAL|SPREAD)$")  # + winner / total / spread 2026-10-12


def soccer_period(kalshi_text, poly_text):
    """The shared period ('full' / '1h' / '2h') if both rules texts carry
    exactly one standard period clause and it's the same one, else None."""
    k = [p for p, kr, _ in _SOCCER if re.search(kr, str(kalshi_text or ""))]
    q = [p for p, _, pr in _SOCCER if re.search(pr, str(poly_text or ""), re.IGNORECASE)]
    return k[0] if len(k) == 1 and k == q else None


# ── template match against reviewed families (2026-10-12) ─────────────────
# Kalshi and Polymarket stamp most markets out of fixed rules templates: a
# new league's BTTS, a new Netflix rank, a new UFC division's title holder
# differ from an already-reviewed family only in names, dates and numbers.
# So a new family whose BOTH rules texts, with those replaced by
# placeholders, match BOTH texts of a human-approved family is treated as
# reviewed. Leave-one-out on the 93 hand-reviewed families
# (tests/data/reviewed_families.json): 43 of 80 approved families matched,
# 0 of 13 rejected ones. Two blocks run first:
#   - extra time counted on one side and excluded on the other
#     (first-to-score: safe in league games, not in cup knockouts, which the
#     template can't tell apart) — always left for a person;
#   - a phrase seen only in rejected families present on one side only.

TEMPLATE_MIN_SIMILARITY = 0.90

_MON = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?"
_ET_IN = re.compile(r"any extra time|including (?:any )?extra time|extra time periods|overtime periods", re.IGNORECASE)
_ET_OUT = re.compile(r"extra time[^.]{0,60}(?:excluded|not included|does not include)|does not include extra time|first 90 minutes", re.IGNORECASE)
HARD_ASYMMETRIC = {
    "may include / not limited to": r"may include|not limited to|could include",
    "co-host / primary host": r"\bco-?host|primary host",
    "singles / EPs": r"\bsingles?\b|\bEPs?\b",
    "executive action": r"executive action",
    "consent decree / disgorgement / plea": r"consent decree|disgorgement|plea agreement",
    "geographic scope": r"conterminous|municipal boundar",
    "intention": r"\bintention",
}


def normalize_rules(text, *questions):
    """Rules text with the market's names (from its questions), dates,
    numbers and URLs replaced by placeholders, lowercased, punctuation
    stripped."""
    t = " " + str(text or "") + " "
    names = set()
    for q in questions:
        q = str(q or "")
        if "—" in q:
            names.add(q.rsplit("—", 1)[1].strip())
        names.update(re.findall(r"\b[A-Z][\w'’.\-]*(?:\s+(?:[A-Z][\w'’.\-]*|de|da|del|la|van|von))*", q))
    for n in sorted(names, key=len, reverse=True):
        if len(n) >= 3:
            t = t.replace(n, " <E> ")
    t = t.lower()
    t = re.sub(rf"\b{_MON}\s+\d{{1,2}}(?:st|nd|rd|th)?,?\s*(?:20\d\d)?", " <D> ", t)
    t = re.sub(r"\b20\d\d-\d\d-\d\d\b", " <D> ", t)
    t = re.sub(r"\$?\d[\d,]*(?:\.\d+)?%?", " <N> ", t)
    t = re.sub(r"https?://\S+", " <U> ", t)
    t = re.sub(r"(?:\s*<e>\s*)+", " <e> ", t)
    t = re.sub(r"[^\w<>]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


# Competitions (as Kalshi's rules name them: "... professional {X} soccer
# game ...") where no match ever goes to extra time: domestic round-robin
# leagues with no playoffs (their cups are separate Kalshi series). For
# these the extra-time difference between Kalshi first-to-score (whole game
# incl. extra time) and Polymarket (90 min) can't change the outcome.
# Checked 2026-10-12 against all 33 Kalshi *FTTS series. NOT here: MLS and
# Liga MX (playoffs), every cup / super cup, Champions / Europa / Conference
# League, Nations League, World Cup and qualifiers.
NO_EXTRA_TIME_COMPETITIONS = {"EPL", "La Liga", "Serie A", "Bundesliga", "Ligue 1", "Brasileiro Serie A"}
_COMPETITION = re.compile(r"professional (.+?) soccer game")


def kalshi_competition(kalshi_text):
    m = _COMPETITION.search(str(kalshi_text or ""))
    return m.group(1).strip() if m else None


def template_blockers(kalshi_text, poly_text):
    """Reasons this pair must not be template-approved (empty = none)."""
    k, p = str(kalshi_text or ""), str(poly_text or "")
    out = []
    if (_ET_IN.search(k) and _ET_OUT.search(p)) or (_ET_IN.search(p) and _ET_OUT.search(k)):
        if kalshi_competition(k) not in NO_EXTRA_TIME_COMPETITIONS:
            out.append("extra time counted on one side only")
    out += [n for n, rx in HARD_ASYMMETRIC.items()
            if bool(re.search(rx, k, re.I)) != bool(re.search(rx, p, re.I))]
    return out


def load_template_refs(paths):
    """Approved reference families: [{family, kn, pn}] from JSON files of
    {family, status, kalshi_q, poly_q, kalshi_rules, poly_rules}."""
    import json
    refs, seen = [], set()
    for p in paths:
        try:
            rows = json.loads(open(p, encoding="utf8").read())
        except (OSError, ValueError):
            continue
        for o in rows:
            if o.get("status") != "approved" or o.get("family") in seen:
                continue
            seen.add(o.get("family"))
            refs.append({"family": o["family"],
                         "kn": normalize_rules(o.get("kalshi_rules"), o.get("kalshi_q"), o.get("poly_q")),
                         "pn": normalize_rules(o.get("poly_rules"), o.get("kalshi_q"), o.get("poly_q"))})
    return refs


def template_match(kalshi_text, poly_text, kalshi_q, poly_q, refs, exclude_family=None):
    """(reference family, similarity) when both texts match one approved
    reference at TEMPLATE_MIN_SIMILARITY or better and nothing blocks it,
    else (None, best similarity)."""
    from rapidfuzz import fuzz
    if template_blockers(kalshi_text, poly_text):
        return None, 0.0
    kn = normalize_rules(kalshi_text, kalshi_q, poly_q)
    pn = normalize_rules(poly_text, kalshi_q, poly_q)
    best, best_s = None, 0.0
    for r in refs:
        if r["family"] == exclude_family:
            continue
        s = min(fuzz.ratio(kn, r["kn"]), fuzz.ratio(pn, r["pn"])) / 100
        if s > best_s:
            best, best_s = r["family"], s
    return (best, best_s) if best_s >= TEMPLATE_MIN_SIMILARITY else (None, best_s)
