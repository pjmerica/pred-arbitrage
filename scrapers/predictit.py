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
import json
import pandas as pd
from pathlib import Path
from datetime import datetime, timezone

RAW = Path(__file__).parent.parent / "data" / "raw"
URL = "https://www.predictit.org/api/marketdata/all/"
HEADERS = {"User-Agent": "Mozilla/5.0 (research/pred-arb)", "Accept": "application/json"}


def fetch():
    req = urllib.request.Request(URL, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read()).get("markets", [])


def parse_contract(market, contract):
    last = contract.get("lastTradePrice")
    buy_yes = contract.get("bestBuyYesCost")
    sell_yes = contract.get("bestSellYesCost")

    if buy_yes is not None and sell_yes is not None:
        implied_prob = (buy_yes + sell_yes) / 2
    elif last is not None:
        implied_prob = last
    else:
        implied_prob = None

    market_id = market.get("id")
    return {
        "market_id": market_id,
        "market_name": market.get("name", ""),
        "contract_id": contract.get("id"),
        "contract_name": contract.get("name", ""),
        "implied_prob": round(implied_prob, 4) if implied_prob is not None else None,
        "best_buy_yes": buy_yes,
        "best_sell_yes": sell_yes,
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
    df = df[df["implied_prob"].notna() & (df["implied_prob"] > 0) & (df["implied_prob"] < 1)]

    out = RAW / "predictit_markets.csv"
    df.to_csv(out, index=False)
    print(f"Saved {len(df)} contracts to {out}")
    print(f"Across {df['market_id'].nunique()} markets")


if __name__ == "__main__":
    run()
