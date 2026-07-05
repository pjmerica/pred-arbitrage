"""
PredictIt full-market scraper — all active markets.

API: https://www.predictit.org/api/marketdata/all/
Returns all active markets with nested contracts.

Output: data/raw/predictit_markets.csv
Fields: market_id, market_name, contract_id, contract_name,
        implied_prob, best_buy_yes, best_sell_yes,
        url, fetched_at
"""

import urllib.request
import urllib.error
import sys
import json
import time
import pandas as pd
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
from utils.http_headers import DEFAULT_HEADERS

RAW = ROOT / "data" / "raw"
URL = "https://www.predictit.org/api/marketdata/all/"
HEADERS = DEFAULT_HEADERS


RETRY_CODES = {403, 408, 429, 500, 502, 503, 504}

def fetch(max_retries=4):
    for attempt in range(max_retries):
        req = urllib.request.Request(URL, headers=HEADERS)
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.loads(r.read()).get("markets", [])
        except urllib.error.HTTPError as e:
            if e.code in RETRY_CODES and attempt < max_retries - 1:
                wait = 2 ** (attempt + 1)
                print(f"  PredictIt HTTP {e.code}, retry {attempt+1}/{max_retries-1} in {wait}s...")
                time.sleep(wait)
                continue
            raise
        except urllib.error.URLError as e:
            if attempt < max_retries - 1:
                wait = 2 ** (attempt + 1)
                print(f"  PredictIt network error: {e}, retry {attempt+1}/{max_retries-1} in {wait}s...")
                time.sleep(wait)
                continue
            raise
    raise RuntimeError(f"PredictIt fetch failed after {max_retries} retries")


def parse_contract(market, contract):
    last = contract.get("lastTradePrice")
    buy_yes = contract.get("bestBuyYesCost")    # price to BUY YES (= ask)
    sell_yes = contract.get("bestSellYesCost")  # price to SELL YES (= bid)

    # PredictIt reports the full price range ($0.01–$0.99) as bid/ask when
    # a contract has no real two-sided market — e.g. a stale single-sided
    # standing order, or no orders at all on one side. Naively averaging
    # them gives a meaningless midpoint (~$0.50) that the scanner will
    # then "arb" against another platform's tight quote.
    #
    # Treat a spread > 30pp as a broken book and refuse to publish a price.
    # ALSO: one-sided quotes are almost always stale single orders on
    # PredictIt — require both bid AND ask. Example: PA-07 primary had
    # buy=$0.92 sell=None and last=$0.01 for one contract; the lone $0.92
    # ask is a stale order and was getting paired against Kalshi to
    # produce a fake 91pp arb.
    # Spread > 15pp on PredictIt is still effectively a broken book — the
    # bestBuyYes/bestSellYes ARE the best bid/ask, not depth quotes. A
    # 29pp spread isn't "deep market," it's "no one wants to trade here."
    # Midpoint of a wide book produces fake arbs against tight quotes.
    implied_prob = None
    if (buy_yes is not None and sell_yes is not None
            and buy_yes > 0 and sell_yes > 0
            and (buy_yes - sell_yes) <= 0.15):
        implied_prob = (buy_yes + sell_yes) / 2

    market_id = market.get("id")
    return {
        "market_id": market_id,
        "market_name": market.get("name", ""),
        "contract_id": contract.get("id"),
        "contract_name": contract.get("name", ""),
        "implied_prob": round(implied_prob, 4) if implied_prob is not None else None,
        "best_buy_yes": buy_yes,
        "best_sell_yes": sell_yes,
        # Real NO-side quotes (2026-07-05): PredictIt YES and NO are
        # separately quoted books; bestBuyNo is a real fillable NO ask.
        # These replace the arb scanner's synthetic ±5pp spread hack.
        "best_buy_no": contract.get("bestBuyNoCost"),
        "best_sell_no": contract.get("bestSellNoCost"),
        "last_trade_price": last,
        "status": market.get("status", ""),
        "url": f"https://www.predictit.org/markets/detail/{market_id}",
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


def run():
    RAW.mkdir(parents=True, exist_ok=True)
    print("Fetching all PredictIt markets...")
    markets = fetch()
    print(f"  Total markets: {len(markets)}")

    rows = []
    for market in markets:
        for contract in market.get("contracts", []):
            rows.append(parse_contract(market, contract))

    df = pd.DataFrame(rows)
    out = RAW / "predictit_markets.csv"

    # If PredictIt returned nothing, FAIL THE RUN so the workflow doesn't
    # push partial docs/ over yesterday's good dashboard.
    if df.empty or "implied_prob" not in df.columns:
        raise SystemExit("PredictIt returned no usable rows. Aborting to keep the last good dashboard.")

    df = df[df["implied_prob"].notna() & (df["implied_prob"] > 0) & (df["implied_prob"] < 1)]
    df.to_csv(out, index=False)
    print(f"Saved {len(df)} contracts to {out}")
    print(f"Across {df['market_id'].nunique()} markets")


if __name__ == "__main__":
    run()
