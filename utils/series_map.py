"""Curated Kalshi-series <-> Polymarket-event families (data/series_map.json).

A title-similarity (fuzzy) pair is only as good as the question on each
side. Reviewing a whole FAMILY once — reading both rules texts side by
side — lets every future pair from that family be trusted (approved) or
dropped (rejected). Families nobody has reviewed stay 'unverified' and are
listed in data/processed/series_review.csv so a person can review them.
"""

import json
import re
from datetime import date
from pathlib import Path

MAP_PATH = Path(__file__).parent.parent / "data" / "series_map.json"

# Trailing id Polymarket appends to repeating event slugs. Two formats seen:
# "...-winner-20260708173711844" (digits) and, since ~2026-10-06,
# "...-this-week-20261006t230000000z" (an ISO-ish timestamp). The second
# wasn't stripped, so six approved Netflix families silently fell back to
# "unreviewed" (2026-10-10).
_SLUG_ID = re.compile(r"-(?:\d{6,}|\d{8}t\d{6,}z)$")


def strip_slug_id(slug):
    return _SLUG_ID.sub("", str(slug or ""))


def load(path=MAP_PATH):
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    fams = []
    for e in doc.get("families", []):
        try:
            e = dict(e, _rx=re.compile(e["polymarket_event_slug_regex"]))
        except (KeyError, re.error):
            continue
        fams.append(e)
    return fams


def market_kind(pm_market_slug):
    """Kind of a dated game market: the market slug after its YYYY-MM-DD,
    digits collapsed ('mls-lag-col-2026-09-26-first-to-score-home' ->
    'first-to-score-home', '...-exact-score-3-2' -> 'exact-score-N-N').
    None for undated slugs (awards, elections...)."""
    m = re.search(r"\d{4}-\d{2}-\d{2}-(.+)$", str(pm_market_slug or ""))
    return re.sub(r"\d+", "N", m.group(1)) if m else None


def status(families, kalshi_series, pm_event_slug, today=None, pm_market_slug=None):
    """('approved'|'rejected'|'unreviewed', entry-or-None).

    An approval with `polymarket_market_kinds` covers only those kinds of
    market inside the matching events (2026-09-26): approving a family is
    matched on the EVENT slug, and Polymarket's per-game "more markets"
    events keep gaining market kinds. A kind nobody reviewed goes back to
    the review queue instead of inheriting the approval."""
    today = today or date.today().isoformat()
    # Polymarket appends numeric ids to many slugs
    # ("big-brother-season-28-winner-20260708173711844"); match the pattern
    # against the id-stripped slug too, so anchored patterns still apply.
    raw = str(pm_event_slug or "")
    candidates = (raw, strip_slug_id(raw))
    for e in families:
        if e.get("kalshi_series") == kalshi_series and any(e["_rx"].search(c) for c in candidates):
            if e.get("status") == "approved" and e.get("review_by") and today > e["review_by"]:
                return "unreviewed", e          # approval lapsed — re-read the rules
            kinds = e.get("polymarket_market_kinds")
            kind = market_kind(pm_market_slug)
            if e.get("status") == "approved" and kinds and kind is not None and kind not in kinds:
                return "unreviewed", e          # a market kind the review didn't cover
            return e.get("status", "unreviewed"), e
    return "unreviewed", None


def slug_family(slug):
    """Group key for the review queue: game slugs collapse to
    '<league>-*-<date>', trailing numeric ids are dropped."""
    slug = str(slug or "")
    m = re.match(r"^([a-z0-9]+)-.*?(\d{4}-\d{2}-\d{2})", slug)
    if m:
        return f"{m.group(1)}-*-<date>"
    return strip_slug_id(slug)
