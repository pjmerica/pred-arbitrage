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
import urllib.parse
import json
import time
import pandas as pd
from pathlib import Path
from datetime import datetime, timezone

RAW = Path(__file__).parent.parent / "data" / "raw"
BASE = "https://gamma-api.polymarket.com"
HEADERS = {"User-Agent": "Mozilla/5.0 (research/pred-arb)", "Accept": "application/json"}
PAGE_SIZE = 100

# Tags to use as category (first matching tag wins, in priority order)
PRIORITY_TAGS = [
    "sports", "nfl", "nba", "nhl", "mlb", "soccer", "tennis", "golf", "ufc", "mma",
    "politics", "elections", "us-elections", "us-politics",
    "crypto", "finance", "economics", "economy", "stocks", "business",
    "entertainment", "pop-culture", "music", "movies", "tv",
    "science", "technology", "climate", "health", "world",
]


def get(path, params=None):
    url = f"{BASE}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


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
    """Paginate all active events from Polymarket."""
    all_events = []
    offset = 0
    page = 0
    while True:
        try:
            batch = get("/events", {
                "limit": PAGE_SIZE,
                "active": "true",
                "closed": "false",
                "offset": offset,
            })
        except Exception as e:
            print(f"  Error at offset {offset}: {e}")
            break
        if not batch:
            break
        all_events.extend(batch)
        page += 1
        if page % 20 == 0:
            print(f"  Fetched {len(all_events)} events so far...")
        if len(batch) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
        time.sleep(0.05)
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
    # other platforms' tight quotes and produces fake arbs (e.g. a lone
    # $0.86 sell order sitting on a dead market).
    if bb is not None and ba is not None and 0 < bb <= ba < 1:
        # Wide spread (>10pp) means the midpoint is fictional; only the ask
        # is actually fillable. Use ask to be conservative.
        if (ba - bb) > 0.10:
            implied_prob = ba
        else:
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
