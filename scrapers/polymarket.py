"""
Polymarket full-market scraper — all active markets.

API: https://gamma-api.polymarket.com/markets
Paginates all active markets.

Output: data/raw/polymarket_markets.csv
Fields: condition_id, market_id, question, category, end_date,
        implied_prob, best_bid, best_ask, liquidity, volume,
        url, fetched_at
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


def get(path, params=None):
    url = f"{BASE}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def fetch_all_markets():
    all_markets = []
    offset = 0
    page = 0
    while True:
        try:
            batch = get("/markets", {"limit": PAGE_SIZE, "active": "true", "closed": "false", "offset": offset})
        except Exception as e:
            print(f"  Error at offset {offset}: {e}")
            break
        if not batch:
            break
        all_markets.extend(batch)
        page += 1
        if page % 10 == 0:
            print(f"  Fetched {len(all_markets)} markets so far...")
        if len(batch) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
        time.sleep(0.05)
    return all_markets


def parse_market(m):
    outcomes = m.get("outcomes", [])
    prices = m.get("outcomePrices", [])

    # Build outcome->price map
    implied_prob = None
    if outcomes and prices and len(outcomes) == len(prices):
        pairs = {}
        for o, p in zip(outcomes, prices):
            try:
                pairs[o] = float(p)
            except (ValueError, TypeError):
                pass
        if "Yes" in pairs:
            implied_prob = pairs["Yes"]
        elif len(pairs) == 2:
            for k, v in pairs.items():
                if k.lower() != "no":
                    implied_prob = v
                    break

    # Fallback to lastTradePrice
    if implied_prob is None:
        last = m.get("lastTradePrice")
        if last is not None:
            try:
                implied_prob = float(last)
            except (ValueError, TypeError):
                pass

    condition_id = m.get("conditionId", "")

    return {
        "condition_id": condition_id,
        "market_id": m.get("id"),
        "question": m.get("question", ""),
        "category": m.get("category", "") or "",
        "end_date": str(m.get("endDate", ""))[:10],
        "implied_prob": round(implied_prob, 4) if implied_prob is not None else None,
        "best_bid": m.get("bestBid"),
        "best_ask": m.get("bestAsk"),
        "liquidity": m.get("liquidity"),
        "volume": m.get("volume"),
        "url": f"https://polymarket.com/event/{condition_id}" if condition_id else None,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


def run():
    RAW.mkdir(parents=True, exist_ok=True)
    print("Fetching all Polymarket active markets...")
    markets = fetch_all_markets()
    print(f"  Total markets fetched: {len(markets)}")

    rows = [parse_market(m) for m in markets]
    df = pd.DataFrame(rows)
    df = df[df["implied_prob"].notna() & (df["implied_prob"] > 0) & (df["implied_prob"] < 1)]
    df = df.drop_duplicates(subset=["condition_id"])

    out = RAW / "polymarket_markets.csv"
    df.to_csv(out, index=False)
    print(f"Saved {len(df)} markets to {out}")

    if "category" in df.columns:
        top = df["category"].value_counts().head(15)
        print("\nTop categories:")
        print(top.to_string())


if __name__ == "__main__":
    run()
