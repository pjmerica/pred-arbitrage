"""
Polymarket full-market scraper — all active events via /events endpoint.

API: https://gamma-api.polymarket.com/events
Paginates all active events, extracts nested markets with category from tags.

Output: data/raw/polymarket_markets.csv
Fields: condition_id, market_id, yes_token_id, no_token_id,
        question, category, end_date,
        implied_prob, best_bid, best_ask, liquidity, volume,
        url, fetched_at

Note: yes_token_id / no_token_id are the ERC-1155 token IDs needed for
CLOB orderbook lookups (clob.polymarket.com/book?token_id=...). These are
78-digit integers — read CSVs with dtype={"yes_token_id": str, "no_token_id": str}
or pandas will silently corrupt them to floats.
"""

import urllib.request
import sys
import urllib.parse
import urllib.error
import json
import time
import pandas as pd
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
from utils.http_headers import DEFAULT_HEADERS

RAW = ROOT / "data" / "raw"
BASE = "https://gamma-api.polymarket.com"
HEADERS = DEFAULT_HEADERS
PAGE_SIZE = 100

# Tags to use as category (first matching tag wins, in priority order)
PRIORITY_TAGS = [
    "sports", "nfl", "nba", "nhl", "mlb", "soccer", "tennis", "golf", "ufc", "mma",
    "politics", "elections", "us-elections", "us-politics",
    "crypto", "finance", "economics", "economy", "stocks", "business",
    "entertainment", "pop-culture", "music", "movies", "tv",
    "science", "technology", "climate", "health", "world",
]


RETRY_CODES = {403, 408, 429, 500, 502, 503, 504}

def get(path, params=None, max_retries=4):
    url = f"{BASE}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    for attempt in range(max_retries):
        req = urllib.request.Request(url, headers=HEADERS)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code in RETRY_CODES and attempt < max_retries - 1:
                wait = 2 ** (attempt + 1)
                print(f"  HTTP {e.code} on {path}, retry {attempt+1}/{max_retries-1} in {wait}s...")
                time.sleep(wait)
                continue
            raise
        except urllib.error.URLError as e:
            if attempt < max_retries - 1:
                wait = 2 ** (attempt + 1)
                print(f"  Network error on {path}: {e}, retry {attempt+1}/{max_retries-1} in {wait}s...")
                time.sleep(wait)
                continue
            raise
    raise RuntimeError(f"Failed after {max_retries} retries: {url}")


def extract_category(tags):
    """Pick the best category label from an event's tags list."""
    if not tags:
        return ""
    labels = [t.get("label", "") for t in tags if isinstance(t, dict)]
    slugs  = [t.get("slug",  "") for t in tags if isinstance(t, dict)]

    # Check slugs against priority list
    for priority in PRIORITY_TAGS:
        for slug in slugs:
            if priority in slug.lower():
                # Return the human-readable label for that slug
                for t in tags:
                    if isinstance(t, dict) and t.get("slug", "") == slug:
                        return t.get("label", slug.title())

    # Fallback: return the first short label (not a year prediction like "2025 Predictions")
    for label in labels:
        if label and "prediction" not in label.lower() and len(label) < 30:
            return label
    return labels[0] if labels else ""


def fetch_all_events():
    """Paginate active events from Polymarket /events?offset=N.

    History (chronological, painful):

    1. We used `?offset=N` happily for months. Polymarket added a hard
       cap at offset=2000 sometime around mid-June 2026. Anything past
       returns HTTP 422 with body
       "offset too large, use /events/keyset for deeper pagination".

    2. Switched to /events/keyset. Probed 2026-06-21 evening: the
       endpoint returns a `next_cursor` but PASSING IT BACK DOESN'T
       ADVANCE — every page after the first returns the exact same 100
       events. Tested with cursor / next_cursor / page_token / after /
       pagination_cursor as the parameter name; none worked. Bug on
       Polymarket's side (or undocumented param name). Net result: with
       keyset we'd silently scrape the same 100 events forever.

    3. Reverted to /events?offset=N capped at 2000. We get up to ~2000
       active events with this; the universe of really-active markets
       is around that anyway after the dead-prop filter. Polymarket's
       full universe of ~55k markets lives at clob.polymarket.com/markets
       (which DOES paginate properly), but that endpoint has a different
       field shape — rewrite tracked in AUDIT.md as the proper fix.

    Defensive: skip persistently-failing pages but stop on 3 consecutive
    skips or after the 2000-offset cap.
    """
    all_events = []
    seen_event_ids = set()
    offset = 0
    page = 0
    consecutive_failures = 0
    OFFSET_CAP = 2000  # Polymarket hard cap; > 2000 returns HTTP 422
    MAX_RETRIES_PER_PAGE = 3
    while offset < OFFSET_CAP:
        params = {
            "limit": PAGE_SIZE,
            "active": "true",
            "closed": "false",
            "offset": offset,
        }
        data = None
        for attempt in range(MAX_RETRIES_PER_PAGE):
            try:
                data = get("/events", params)
                break
            except Exception as e:
                if attempt < MAX_RETRIES_PER_PAGE - 1:
                    wait = 2 * (attempt + 1)
                    print(f"  Page (offset={offset}) failed ({e}); retrying in {wait}s...")
                    time.sleep(wait)
                else:
                    print(f"  Page (offset={offset}) failed after {MAX_RETRIES_PER_PAGE} attempts ({e}); skipping")
        if data is None:
            consecutive_failures += 1
            if consecutive_failures >= 3:
                print(f"  3 consecutive failed pages — stopping at offset {offset}")
                break
            offset += PAGE_SIZE
            page += 1
            continue

        consecutive_failures = 0
        events = data if isinstance(data, list) else []
        if not events:
            break
        new_events = [e for e in events if e.get("id") not in seen_event_ids]
        for e in new_events:
            seen_event_ids.add(e.get("id"))
        all_events.extend(new_events)
        page += 1
        if page % 5 == 0:
            print(f"  Fetched {len(all_events)} events so far (offset {offset})...")
        if len(events) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
        time.sleep(0.05)
    if offset >= OFFSET_CAP:
        print(f"  Hit OFFSET_CAP={OFFSET_CAP} (Polymarket's hard limit). Switch to clob.polymarket.com/markets to get the rest — see AUDIT.md.")
    return all_events


def parse_market(event, market):
    outcomes = market.get("outcomes", "[]")
    prices   = market.get("outcomePrices", "[]")

    # outcomePrices can be a JSON string or a list
    if isinstance(outcomes, str):
        try:
            outcomes = json.loads(outcomes)
        except Exception:
            outcomes = []
    if isinstance(prices, str):
        try:
            prices = json.loads(prices)
        except Exception:
            prices = []

    # Pick implied_prob with this priority (freshest first):
    #   1. midpoint of bestBid + bestAsk           (live orderbook)
    #   2. bestAsk alone                            (only ask side)
    #   3. bestBid alone                            (only bid side)
    #   4. outcomePrices "Yes"                      (gamma snapshot, can be stale)
    #   5. lastTradePrice                           (worst — can be days stale)
    # Earlier code went straight to outcomePrices then lastTradePrice; for
    # low-volume markets gamma's snapshot is wildly out of sync with the live
    # CLOB book, producing fake arbs against Kalshi.
    implied_prob = None
    bb = market.get("bestBid")
    ba = market.get("bestAsk")
    try:
        bb = float(bb) if bb is not None else None
    except (TypeError, ValueError):
        bb = None
    try:
        ba = float(ba) if ba is not None else None
    except (TypeError, ValueError):
        ba = None
    # Require BOTH a bid AND an ask. A one-sided quote (only ba or only bb)
    # is a stale standing order, not a real market — using it pairs against
    # other platforms' tight quotes and produces fake arbs.
    #
    # Wide-spread rows are KEPT at scrape time. Earlier code (2026-06-21
    # morning) dropped rows whose gamma spread was >8pp on the theory that
    # wide gamma is usually stale. That was overcautious: the scanner
    # already refreshes prices from the live CLOB via fetch_depth for
    # every matched pair (depth-time override), and the depth-derived
    # spread filter drops pairs whose LIVE spread is >25pp. So a wide
    # gamma snapshot that turns out to be stale will (a) get the right
    # price restamped at depth time, or (b) get dropped at depth time
    # if it's truly wide. Pre-emptively cutting at the scraper layer
    # just prevents the matcher from finding pairs it would have
    # restamped correctly.
    if bb is not None and ba is not None and 0 < bb <= ba < 1:
        implied_prob = round((bb + ba) / 2, 4)

    if implied_prob is None and outcomes and prices and len(outcomes) == len(prices):
        pairs = {}
        for o, p in zip(outcomes, prices):
            try:
                pairs[str(o)] = float(p)
            except (ValueError, TypeError):
                pass
        if "Yes" in pairs:
            implied_prob = pairs["Yes"]
        elif len(pairs) == 2:
            for k, v in pairs.items():
                if k.lower() != "no":
                    implied_prob = v
                    break

    if implied_prob is None:
        last = market.get("lastTradePrice")
        if last is not None:
            try:
                implied_prob = float(last)
            except (ValueError, TypeError):
                pass

    condition_id = market.get("conditionId", "")

    # clobTokenIds: JSON-encoded string '["yes_id","no_id"]' or a real list.
    # Outcome order in `outcomes` aligns with token order. These are 78-digit
    # ERC-1155 IDs needed for CLOB orderbook lookups.
    raw_tokens = market.get("clobTokenIds")
    yes_token = no_token = None
    try:
        tokens = json.loads(raw_tokens) if isinstance(raw_tokens, str) else raw_tokens
        if isinstance(tokens, list) and len(tokens) >= 2 and outcomes and len(outcomes) >= 2:
            for o, tid in zip(outcomes, tokens):
                if str(o).lower() == "yes":
                    yes_token = tid
                elif str(o).lower() == "no":
                    no_token = tid
            if yes_token is None and no_token is None:
                yes_token, no_token = tokens[0], tokens[1]
    except (ValueError, TypeError):
        pass

    category = extract_category(event.get("tags") or [])
    event_slug = event.get("slug", "") or ""
    market_slug = market.get("slug", "") or ""
    url_slug = event_slug or market_slug
    url = f"https://polymarket.com/event/{url_slug}" if url_slug else None

    return {
        "condition_id": condition_id,
        "market_id": market.get("id", ""),
        "yes_token_id": yes_token,
        "no_token_id": no_token,
        "question": market.get("question", event.get("title", "")),
        "category": category,
        "end_date": str(market.get("endDate", event.get("endDate", "")))[:10],
        "implied_prob": round(implied_prob, 4) if implied_prob is not None else None,
        "best_bid": market.get("bestBid"),
        "best_ask": market.get("bestAsk"),
        "liquidity": event.get("liquidity"),
        "volume": market.get("volume"),
        "event_slug": event_slug,
        "market_slug": market_slug,
        "url": url,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


def run():
    RAW.mkdir(parents=True, exist_ok=True)
    print("Fetching all Polymarket active events...")
    events = fetch_all_events()
    print(f"  Total events fetched: {len(events)}")

    rows = []
    for event in events:
        markets = event.get("markets", [])
        if markets:
            for m in markets:
                rows.append(parse_market(event, m))
        else:
            # Event with no nested markets — treat event itself as a market
            rows.append(parse_market(event, event))

    df = pd.DataFrame(rows)
    out = RAW / "polymarket_markets.csv"

    # If Polymarket returned nothing, FAIL THE RUN so the workflow doesn't
    # push partial docs/ over yesterday's good dashboard.
    if df.empty or "implied_prob" not in df.columns:
        raise SystemExit("Polymarket returned no usable rows. Aborting to keep the last good dashboard.")

    df = df[df["implied_prob"].notna() & (df["implied_prob"] > 0) & (df["implied_prob"] < 1)]
    df = df.drop_duplicates(subset=["condition_id"])

    # Drop dead props that the platform still flags active=true. Polymarket's
    # active/closed flags are unreliable — a probe on 2026-06-21 showed
    # ~22% of "active" events had endDate already in the past. Filter on
    # the date itself so a market that resolved 6 months ago doesn't
    # show up as tradeable. Rows with no end_date are kept (don't drop on
    # missing data; the downstream past-settle-date filter in arb_scanner
    # catches whatever leaks through).
    today_iso = datetime.now(timezone.utc).date().isoformat()
    before = len(df)
    df = df[~((df["end_date"].notna()) & (df["end_date"].astype(str).str[:10] < today_iso)
              & (df["end_date"].astype(str).str.match(r"^\d{4}-\d{2}-\d{2}", na=False)))]
    if before != len(df):
        print(f"  Dropped {before - len(df)} markets with end_date in the past")

    # Drop unrealistic markets:
    #   1. Liquidity < $200 — basically no real trading
    #   2. Spread (bestAsk - bestBid) > 30pp — only one-sided standing
    #      orders, no two-sided market. Pairing these against active
    #      Kalshi markets produces fake 80%+ "guaranteed" arbs against
    #      a $0.97 sell order that nothing would actually fill (e.g.
    #      Boston Red Sox manager, Crunchyroll Anime Awards).
    liq = pd.to_numeric(df["liquidity"], errors="coerce").fillna(0)
    bb = pd.to_numeric(df.get("best_bid"), errors="coerce")
    ba = pd.to_numeric(df.get("best_ask"), errors="coerce")
    has_two_sided = bb.notna() & ba.notna() & ((ba - bb) <= 0.20)
    before = len(df)
    df = df[(liq >= 200) & has_two_sided]
    print(f"  Dropped {before - len(df)} markets (liquidity<$200 or spread>30pp)")

    df.to_csv(out, index=False)
    print(f"Saved {len(df)} markets to {out}")

    print("\nTop categories:")
    print(df["category"].value_counts().head(20).to_string())


if __name__ == "__main__":
    run()
