"""
Kalshi full-market scraper — all categories.

API: https://api.elections.kalshi.com/v1/events/
Paginates all open events, extracts markets with prices.

Output: data/raw/kalshi_markets.csv
Fields: ticker, event_ticker, series_ticker, title, category, tags,
        yes_bid, yes_ask, implied_prob, open_interest, volume,
        close_date, settle_date, url, fetched_at
"""

import urllib.request
import urllib.parse
import json
import time
import pandas as pd
from pathlib import Path
from datetime import datetime, timezone

RAW = Path(__file__).parent.parent / "data" / "raw"
BASE = "https://api.elections.kalshi.com/v1"
HEADERS = {"User-Agent": "Mozilla/5.0 (research/pred-arb)", "Accept": "application/json"}
PAGE_SIZE = 200


def get(path, params=None):
    url = f"{BASE}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def fetch_all_events():
    """Paginate all open events from Kalshi."""
    all_events = []
    cursor = None
    page = 0
    while True:
        params = {"limit": PAGE_SIZE, "status": "open"}
        if cursor:
            params["cursor"] = cursor
        try:
            data = get("/events/", params)
        except Exception as e:
            print(f"  Error fetching page {page}: {e}")
            break
        events = data.get("events", [])
        all_events.extend(events)
        cursor = data.get("cursor")
        page += 1
        if page % 5 == 0:
            print(f"  Fetched {len(all_events)} events so far...")
        if not cursor or len(events) < PAGE_SIZE:
            break
        time.sleep(0.1)
    return all_events


def parse_market(event, market):
    yes_bid = market.get("yes_bid", 0) or 0
    yes_ask = market.get("yes_ask", 0) or 0
    last = market.get("last_price", 0) or 0

    # Implied prob: midpoint of bid/ask if spread exists, else last price (in cents -> 0-1)
    if yes_bid > 0 and yes_ask > 0:
        implied_prob = (yes_bid + yes_ask) / 2 / 100
    elif last > 0:
        implied_prob = last / 100
    else:
        implied_prob = None

    ticker = market.get("ticker_name", "")
    series_ticker = event.get("series_ticker", "")
    close_date = market.get("close_date") or market.get("expiration_date", "")

    return {
        "ticker": ticker,
        "event_ticker": event.get("ticker", ""),
        "series_ticker": series_ticker,
        "title": market.get("title", event.get("title", "")),
        "subtitle": market.get("sub_title", ""),
        "category": event.get("category", ""),
        "tags": "|".join(t for t in (event.get("tags") or []) if isinstance(t, str)),
        "yes_bid": yes_bid / 100 if yes_bid else None,
        "yes_ask": yes_ask / 100 if yes_ask else None,
        "implied_prob": round(implied_prob, 4) if implied_prob is not None else None,
        "open_interest": market.get("open_interest", 0),
        "volume": market.get("volume", 0),
        "close_date": str(close_date)[:10] if close_date else "",
        "url": f"https://kalshi.com/markets/{series_ticker}",
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


def run():
    RAW.mkdir(parents=True, exist_ok=True)
    print("Fetching all Kalshi events...")
    events = fetch_all_events()
    print(f"  Total events: {len(events)}")

    rows = []
    for event in events:
        for market in event.get("markets", []):
            rows.append(parse_market(event, market))

    df = pd.DataFrame(rows)
    df = df[df["implied_prob"].notna()]

    out = RAW / "kalshi_markets.csv"
    df.to_csv(out, index=False)
    print(f"Saved {len(df)} markets to {out}")

    print("\nBy category:")
    print(df.groupby("category")["ticker"].count().sort_values(ascending=False).to_string())


if __name__ == "__main__":
    run()
