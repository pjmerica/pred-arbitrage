"""
Kalshi full-market scraper — all categories.

Uses two API paths:
  1. /v1/series/ — iterates all series, filters election-related ones by ticker pattern.
     This catches HOUSE{ST}{D}, SENATEPARTY{ST}, GOVPARTY{ST} etc. with proper race_ids.
  2. /v1/events/?status=open — catches all other (non-election) open markets.

Output: data/raw/kalshi_markets.csv
Fields: ticker, event_ticker, series_ticker, title, category, tags,
        yes_bid, yes_ask, implied_prob, open_interest, volume,
        close_date, settle_date, race_id, url, fetched_at
"""

import urllib.request
import urllib.parse
import json
import time
import re
import pandas as pd
from pathlib import Path
from datetime import datetime, timezone

RAW = Path(__file__).parent.parent / "data" / "raw"
BASE = "https://api.elections.kalshi.com/v1"
HEADERS = {"User-Agent": "Mozilla/5.0 (research/pred-arb)", "Accept": "application/json"}
PAGE_SIZE = 200

STATE_ABBREVS = {
    "AL","AK","AZ","AR","CA","CO","CT","DE","FL","GA","HI","ID","IL","IN","IA","KS",
    "KY","LA","ME","MD","MA","MI","MN","MS","MO","MT","NE","NV","NH","NJ","NM","NY",
    "NC","ND","OH","OK","OR","PA","RI","SC","SD","TN","TX","UT","VT","VA","WA","WV",
    "WI","WY",
}

# Election series ticker patterns
ELECTION_KEYWORDS = [
    "senate", "governor", "house", "midterm", "2026",
    "senateparty", "govparty",
]


def get(path, params=None):
    url = f"{BASE}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def infer_race_id_from_ticker(ticker: str) -> str | None:
    """Infer race_id from a Kalshi series ticker like HOUSECA47, SENATEPARTYPA."""
    t = ticker.upper()

    # HOUSE{ST}{D} e.g. HOUSECA47, HOUSENH2
    m = re.match(r"HOUSE([A-Z]{2})(\d+)$", t)
    if m and m.group(1) in STATE_ABBREVS:
        return f"2026-H-{m.group(1)}-{m.group(2).zfill(2)}"

    # SENATEPARTY{ST} e.g. SENATEPARTYCA
    m = re.match(r"SENATEPARTY[-_]?([A-Z]{2})(?:[A-Z0-9]*)?$", t)
    if m and m.group(1) in STATE_ABBREVS:
        return f"2026-SEN-{m.group(1)}"

    # SENATE{ST} e.g. SENATEPA, SENATENH
    m = re.match(r"SENATE[-_]?([A-Z]{2})(?:[A-Z0-9]*)?$", t)
    if m and m.group(1) in STATE_ABBREVS:
        return f"2026-SEN-{m.group(1)}"

    # KXSENATE{ST} e.g. KXSENATEMSR
    m = re.match(r"KXSENATE([A-Z]{2})([A-Z]?)$", t)
    if m and m.group(1) in STATE_ABBREVS:
        return f"2026-SEN-{m.group(1)}"

    # GOVPARTY{ST} e.g. GOVPARTYCA
    m = re.match(r"GOVPARTY([A-Z]{2})(?:[A-Z0-9]*)?$", t)
    if m and m.group(1) in STATE_ABBREVS:
        return f"2026-GOV-{m.group(1)}"

    # KXGOV{ST} e.g. KXGOVOHNOMD
    m = re.match(r"KXGOV([A-Z]{2})[A-Z0-9]+$", t)
    if m and m.group(1) in STATE_ABBREVS:
        return f"2026-GOV-{m.group(1)}"

    return None


def fetch_election_series():
    """Paginate /v1/series/ and return election-related ones."""
    all_series = []
    cursor = None
    while True:
        params = {"limit": 100}
        if cursor:
            params["cursor"] = cursor
        try:
            data = get("/series/", params)
        except Exception as e:
            print(f"  Error fetching series: {e}")
            break
        batch = data.get("series", [])
        if not batch:
            break
        for s in batch:
            ticker = s.get("ticker", "").lower()
            title  = s.get("title",  "").lower()
            if any(k in ticker or k in title for k in ELECTION_KEYWORDS):
                all_series.append(s)
        cursor = data.get("cursor")
        if not cursor or len(batch) < 100:
            break
    return all_series


def fetch_events_for_series(series_ticker: str):
    try:
        data = get("/events/", {"series_ticker": series_ticker, "limit": 100})
        return data.get("events", [])
    except Exception as e:
        print(f"  Warning: failed {series_ticker}: {e}")
        return []


def fetch_all_open_events():
    """Paginate all open events (non-election categories)."""
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
            print(f"  Error fetching open events page {page}: {e}")
            break
        events = data.get("events", [])
        all_events.extend(events)
        cursor = data.get("cursor")
        page += 1
        if page % 5 == 0:
            print(f"  Fetched {len(all_events)} open events so far...")
        if not cursor or len(events) < PAGE_SIZE:
            break
        time.sleep(0.1)
    return all_events


def parse_market(event, market, series_ticker="", series_title="", race_id=None):
    yes_bid = market.get("yes_bid", 0) or 0
    yes_ask = market.get("yes_ask", 0) or 0
    last    = market.get("last_price", 0) or 0

    if yes_bid > 0 and yes_ask > 0:
        implied_prob = (yes_bid + yes_ask) / 2 / 100
    elif last > 0:
        implied_prob = last / 100
    else:
        implied_prob = None

    ticker       = market.get("ticker_name", market.get("ticker", ""))
    event_ticker = event.get("ticker", "")
    if not series_ticker:
        series_ticker = event.get("series_ticker", "")
    close_date   = market.get("close_date") or market.get("expiration_date", "")

    # Infer race_id from series ticker if not pre-computed
    if race_id is None and series_ticker:
        race_id = infer_race_id_from_ticker(series_ticker)

    tags = event.get("tags") or []
    tags_str = "|".join(t for t in tags if isinstance(t, str))

    return {
        "ticker":        ticker,
        "event_ticker":  event_ticker,
        "series_ticker": series_ticker,
        "title":         market.get("title", event.get("title", "")),
        "subtitle":      market.get("sub_title", ""),
        "category":      event.get("category", ""),
        "tags":          tags_str,
        "race_id":       race_id,
        "yes_bid":       yes_bid / 100 if yes_bid else None,
        "yes_ask":       yes_ask / 100 if yes_ask else None,
        "implied_prob":  round(implied_prob, 4) if implied_prob is not None else None,
        "open_interest": market.get("open_interest", 0),
        "volume":        market.get("volume", 0),
        "close_date":    str(close_date)[:10] if close_date else "",
        "url":           f"https://kalshi.com/markets/{series_ticker}" if series_ticker else "",
        "fetched_at":    datetime.now(timezone.utc).isoformat(),
    }


def run():
    RAW.mkdir(parents=True, exist_ok=True)
    rows = []
    seen_tickers = set()

    # ── Phase 1: election series (House, Senate, Governor) ──────────────────
    print("Fetching Kalshi election series...")
    series_list = fetch_election_series()
    print(f"  Found {len(series_list)} election-related series")

    for i, series in enumerate(series_list):
        sticker = series["ticker"]
        stitle  = series.get("title", "")
        race_id = infer_race_id_from_ticker(sticker)
        events  = fetch_events_for_series(sticker)
        for event in events:
            for market in event.get("markets", []):
                row = parse_market(event, market, sticker, stitle, race_id)
                key = row["ticker"] or row["event_ticker"]
                if key not in seen_tickers:
                    seen_tickers.add(key)
                    rows.append(row)
        if (i + 1) % 50 == 0:
            print(f"  Series progress: {i+1}/{len(series_list)}, {len(rows)} markets")
        time.sleep(0.15)

    election_count = len(rows)
    print(f"  Election markets: {election_count}")

    # ── Phase 2: all other open events ──────────────────────────────────────
    print("\nFetching all open Kalshi events (non-election categories)...")
    open_events = fetch_all_open_events()
    print(f"  Total open events: {len(open_events)}")

    for event in open_events:
        series_ticker = event.get("series_ticker", "")
        for market in event.get("markets", []):
            row = parse_market(event, market, series_ticker)
            key = row["ticker"] or row["event_ticker"]
            if key not in seen_tickers:
                seen_tickers.add(key)
                rows.append(row)

    df = pd.DataFrame(rows)
    df = df[df["implied_prob"].notna()]

    out = RAW / "kalshi_markets.csv"
    df.to_csv(out, index=False)
    print(f"\nSaved {len(df)} markets to {out}")

    race_matched = df["race_id"].notna().sum()
    print(f"Race_id matched: {race_matched}/{len(df)}")
    print("\nBy category:")
    print(df.groupby("category")["ticker"].count().sort_values(ascending=False).to_string())


if __name__ == "__main__":
    run()
