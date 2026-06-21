"""
Scrutinize >30pp pairs by fetching each market's resolution rules and
comparing them. Big gaps that survive other filters are usually one of:

  (a) Real arb — both rules describe the same outcome; markets disagree.
  (b) Criteria divergence — rules sound similar but actually trigger on
      different events (e.g. Kalshi requires a "formal signed agreement"
      while Polymarket accepts "publicly announced framework").

For each candidate we fetch the official rule text and compute a
similarity score. Pairs with very dissimilar rules (criteria_score < 50)
get filtered out; borderline pairs (50-75) get tagged with criteria_warn.

Also honors data/processed/excluded_pairs.json, a manual list of
(platform_a, market_id_a, platform_b, market_id_b) tuples that have been
human-verified as criteria mismatches and should always be dropped.

Output: writes scrutiny_cache.json next to depth_targets.csv so we don't
re-fetch the same rules every run.
"""

import json
import re
import time
import sys
import urllib.request
import urllib.parse
from difflib import SequenceMatcher
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
from utils.http_headers import DEFAULT_HEADERS

PROCESSED = ROOT / "data" / "processed"
CACHE_PATH = PROCESSED / "scrutiny_cache.json"
EXCLUDE_PATH = PROCESSED / "excluded_pairs.json"

HEADERS = DEFAULT_HEADERS

# Cache rules for 7 days — they rarely change.
CACHE_TTL_SEC = 7 * 24 * 3600

# Pairs below this score are dropped from arb_data.
HARD_THRESHOLD = 50
# Pairs in [HARD_THRESHOLD, SOFT_THRESHOLD] keep the row but tag a warning.
SOFT_THRESHOLD = 75


def _load_cache():
    if CACHE_PATH.exists():
        try:
            return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_cache(cache):
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(cache, indent=2), encoding="utf-8")


def _load_excludes():
    if EXCLUDE_PATH.exists():
        try:
            return json.loads(EXCLUDE_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return []


def _http_json(url, timeout=15):
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except Exception:
        return None


def fetch_kalshi_rules(market_id):
    d = _http_json(f"https://api.elections.kalshi.com/trade-api/v2/markets/{urllib.parse.quote(str(market_id))}")
    if not d:
        return None
    m = d.get("market", {})
    parts = [m.get("title") or "", m.get("rules_primary") or "", m.get("rules_secondary") or ""]
    return " ".join(p for p in parts if p).strip()


def fetch_polymarket_rules(token_id):
    # Search by token. The CLOB doesn't return rules, but gamma's /markets does.
    # We don't have a direct token→market endpoint, so search by clobTokenIds.
    d = _http_json(f"https://gamma-api.polymarket.com/markets?clob_token_ids={token_id}")
    if not d:
        return None
    items = d if isinstance(d, list) else d.get("data", [])
    if not items:
        return None
    m = items[0]
    parts = [m.get("question") or "", m.get("description") or ""]
    return " ".join(p for p in parts if p).strip()


def _normalize(text):
    if not text:
        return ""
    t = text.lower()
    t = re.sub(r"\s+", " ", t)
    t = re.sub(r"[^a-z0-9 ]+", " ", t)
    return t.strip()


def similarity(a, b):
    """0-100 SequenceMatcher ratio over normalized text."""
    a, b = _normalize(a), _normalize(b)
    if not a or not b:
        return 0
    return int(SequenceMatcher(None, a, b).ratio() * 100)


def get_rules(platform, market_id, cache):
    key = f"{platform}:{market_id}"
    now = time.time()
    entry = cache.get(key)
    if entry and (now - entry.get("fetched_at", 0)) < CACHE_TTL_SEC:
        return entry.get("text")
    if platform == "kalshi":
        text = fetch_kalshi_rules(market_id)
    elif platform == "polymarket":
        text = fetch_polymarket_rules(market_id)
    else:
        text = None
    cache[key] = {"text": text, "fetched_at": now}
    return text


def scrutinize(pairs, threshold_pp=30):
    """
    Given an iterable of pair dicts (with platform_a/b, market_id_a/b,
    raw_gap_pp), return a dict {(market_id_a, market_id_b): result} where
    result has {criteria_score, action ('drop' or 'warn' or 'ok')}.

    Only inspects pairs with raw_gap_pp > threshold_pp; small gaps don't
    warrant the fetch cost.
    """
    cache = _load_cache()
    excludes = {(e["platform_a"], str(e["market_id_a"]), e["platform_b"], str(e["market_id_b"]))
                for e in _load_excludes()}
    out = {}
    inspected = 0
    for p in pairs:
        pa = p.get("platform_a"); pb = p.get("platform_b")
        ma = p.get("market_id_a"); mb = p.get("market_id_b")
        gap = p.get("raw_gap_pp") or 0
        key = (str(ma), str(mb))
        if pa in ("predictit",) or pb in ("predictit",):
            continue  # predictit rule fetch needs a different endpoint
        if ma is None or mb is None:
            continue

        # Manual exclude
        if (pa, str(ma), pb, str(mb)) in excludes or (pb, str(mb), pa, str(ma)) in excludes:
            out[key] = {"criteria_score": 0, "action": "drop", "reason": "manual_exclude"}
            continue

        if gap <= threshold_pp:
            continue

        ra = get_rules(pa, ma, cache)
        rb = get_rules(pb, mb, cache)
        inspected += 1
        if not ra or not rb:
            out[key] = {"criteria_score": None, "action": "warn", "reason": "rules_unavailable"}
            continue
        score = similarity(ra, rb)
        if score < HARD_THRESHOLD:
            out[key] = {"criteria_score": score, "action": "drop", "reason": "criteria_mismatch"}
        elif score < SOFT_THRESHOLD:
            out[key] = {"criteria_score": score, "action": "warn", "reason": "criteria_warn"}
        else:
            out[key] = {"criteria_score": score, "action": "ok"}
        time.sleep(0.05)
    _save_cache(cache)
    if inspected:
        print(f"  Scrutinized {inspected} pairs with raw_gap > {threshold_pp}pp")
    return out


if __name__ == "__main__":
    # Smoke test: scrutinize one hard-coded pair (the Iran example).
    pair = {
        "platform_a": "kalshi", "market_id_a": "KXUSAIRANAGREEMENT-27",
        "platform_b": "polymarket",
        "market_id_b": "102936224134271070189104847090829839924697394514566827387181305960175107677216",
        "raw_gap_pp": 9.5,
    }
    r = scrutinize([pair], threshold_pp=0)
    print(r)
