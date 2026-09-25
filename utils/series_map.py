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


def status(families, kalshi_series, pm_event_slug, today=None):
    """('approved'|'rejected'|'unreviewed', entry-or-None)."""
    today = today or date.today().isoformat()
    for e in families:
        if e.get("kalshi_series") == kalshi_series and e["_rx"].search(str(pm_event_slug or "")):
            if e.get("status") == "approved" and e.get("review_by") and today > e["review_by"]:
                return "unreviewed", e          # approval lapsed — re-read the rules
            return e.get("status", "unreviewed"), e
    return "unreviewed", None


def slug_family(slug):
    """Group key for the review queue: game slugs collapse to
    '<league>-*-<date>', trailing numeric ids are dropped."""
    slug = str(slug or "")
    m = re.match(r"^([a-z0-9]+)-.*?(\d{4}-\d{2}-\d{2})", slug)
    if m:
        return f"{m.group(1)}-*-<date>"
    return re.sub(r"-\d{6,}$", "", slug)
