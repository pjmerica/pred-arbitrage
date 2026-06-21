"""
Fetch orderbook depth for markets that landed in matched pairs.

For each unique (platform, market_id) used by the arb scanner, query the
platform's orderbook endpoint and compute:
  - best_bid_size, best_ask_size  (top of book, in contracts/shares)
  - depth_at_1pp                  (size available within 1pp of best price)
  - max_size_for_edge_3pp         (size you could buy before you'd pay
                                    >= 3pp worse than best ask)

Endpoints:
  Kalshi:     GET /trade-api/v2/markets/{ticker}/orderbook?depth=50
              orderbook_fp.yes_dollars / no_dollars: list of [price_str, size_str]
              Both stacks are RESTING BUY ORDERS, not asks:
                yes_dollars[k] = someone wants to BUY YES at $price (size contracts)
                no_dollars[k]  = someone wants to BUY NO  at $price (size contracts)
              => best YES bid  = MAX price in yes_dollars
                 best YES ask  = 1 - MAX price in no_dollars   (since buying NO@p
                                                                 = selling YES@(1-p))

  Polymarket: GET https://clob.polymarket.com/book?token_id={tid}
              bids: ascending price (best bid = last entry, highest price)
              asks: descending price (best ask = last entry, lowest price)
              Each entry: {"price": "0.42", "size": "1234.5"}

Inputs (read from data/processed/depth_targets.csv produced by arb_scanner):
  platform, market_id  -- where market_id is the kalshi ticker
                          or polymarket yes_token_id

Output: data/raw/orderbook_depth.csv
"""

import sys
import json
import time
import urllib.request
import urllib.parse
import pandas as pd
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
from utils.http_headers import DEFAULT_HEADERS

RAW = ROOT / "data" / "raw"
PROCESSED = ROOT / "data" / "processed"

KALSHI_OB_URL = "https://api.elections.kalshi.com/trade-api/v2/markets/{ticker}/orderbook?depth=50"
POLY_OB_URL = "https://clob.polymarket.com/book?token_id={tid}"
HEADERS = DEFAULT_HEADERS

EDGE_PP = 0.03   # "max size before paying 3pp worse than best ask"
NEAR_PP = 0.01   # "depth within 1pp of best price"


def _http_json(url: str, timeout: int = 15) -> dict | None:
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except Exception:
        return None


def _kalshi_yes_book(ticker: str) -> dict:
    """
    Returns {best_bid, best_ask, best_bid_size, best_ask_size,
             depth_bid_at_1pp, depth_ask_at_1pp, max_buy_size_at_3pp_edge}
    All in YES-share terms (price 0..1, size in contracts).
    """
    out = {"best_bid": None, "best_ask": None,
           "best_bid_size": None, "best_ask_size": None,
           "depth_bid_at_1pp": None, "depth_ask_at_1pp": None,
           "max_buy_size_at_3pp_edge": None}
    if not ticker or pd.isna(ticker):
        return out
    data = _http_json(KALSHI_OB_URL.format(ticker=urllib.parse.quote(str(ticker))))
    if not data:
        return out
    ob = data.get("orderbook_fp") or data.get("orderbook") or {}
    yes_bids_raw = ob.get("yes_dollars") or ob.get("yes") or []   # YES BUY orders
    no_bids_raw  = ob.get("no_dollars")  or ob.get("no")  or []   # NO BUY orders

    yes_bids = sorted([(float(p), float(s)) for p, s in yes_bids_raw], key=lambda x: x[0])
    no_bids  = sorted([(float(p), float(s)) for p, s in no_bids_raw],  key=lambda x: x[0])

    if yes_bids:
        out["best_bid"] = yes_bids[-1][0]
        out["best_bid_size"] = yes_bids[-1][1]
    if no_bids:
        best_no_bid = no_bids[-1][0]
        out["best_ask"] = round(1.0 - best_no_bid, 4)
        out["best_ask_size"] = no_bids[-1][1]

    if out["best_bid"] is not None:
        floor = out["best_bid"] - NEAR_PP
        out["depth_bid_at_1pp"] = round(sum(s for p, s in yes_bids if p >= floor - 1e-9), 2)

    if out["best_ask"] is not None:
        no_bid_floor_near = (1 - out["best_ask"]) - NEAR_PP
        out["depth_ask_at_1pp"] = round(sum(s for p, s in no_bids if p >= no_bid_floor_near - 1e-9), 2)
        no_bid_floor_edge = (1 - out["best_ask"]) - EDGE_PP
        out["max_buy_size_at_3pp_edge"] = round(sum(s for p, s in no_bids if p >= no_bid_floor_edge - 1e-9), 2)

    return out


def _polymarket_yes_book(token_id: str) -> dict:
    out = {"best_bid": None, "best_ask": None,
           "best_bid_size": None, "best_ask_size": None,
           "depth_bid_at_1pp": None, "depth_ask_at_1pp": None,
           "max_buy_size_at_3pp_edge": None}
    if not token_id or pd.isna(token_id):
        return out
    data = _http_json(POLY_OB_URL.format(tid=str(token_id)))
    if not data:
        return out
    bids = [(float(b["price"]), float(b["size"])) for b in data.get("bids", [])]
    asks = [(float(a["price"]), float(a["size"])) for a in data.get("asks", [])]
    bids.sort(key=lambda x: x[0])
    asks.sort(key=lambda x: x[0])

    if bids:
        out["best_bid"] = bids[-1][0]
        out["best_bid_size"] = bids[-1][1]
    if asks:
        out["best_ask"] = asks[0][0]
        out["best_ask_size"] = asks[0][1]

    if out["best_ask"] is not None:
        cap_ask = out["best_ask"] + NEAR_PP
        out["depth_ask_at_1pp"] = round(sum(s for p, s in asks if p <= cap_ask + 1e-9), 2)
        cap_edge = out["best_ask"] + EDGE_PP
        out["max_buy_size_at_3pp_edge"] = round(sum(s for p, s in asks if p <= cap_edge + 1e-9), 2)
    if out["best_bid"] is not None:
        floor_bid = out["best_bid"] - NEAR_PP
        out["depth_bid_at_1pp"] = round(sum(s for p, s in bids if p >= floor_bid - 1e-9), 2)

    return out


def fetch_one(platform: str, market_id: str) -> dict:
    if platform == "kalshi":
        d = _kalshi_yes_book(market_id)
    elif platform == "polymarket":
        d = _polymarket_yes_book(market_id)
    else:
        d = {}
    d["platform"] = platform
    d["market_id"] = market_id
    d["fetched_at"] = datetime.now(timezone.utc).isoformat()
    return d


def run(targets_csv: Path = None, out_csv: Path = None, delay: float = 0.1):
    targets_csv = targets_csv or (PROCESSED / "depth_targets.csv")
    out_csv = out_csv or (RAW / "orderbook_depth.csv")
    if not targets_csv.exists():
        print(f"No targets file at {targets_csv}. Run arb_scanner.py first to emit it.")
        return

    targets = pd.read_csv(targets_csv, dtype={"market_id": str})
    targets = targets[targets["platform"].isin(["kalshi", "polymarket"])].copy()
    targets = targets.dropna(subset=["market_id"]).drop_duplicates(subset=["platform", "market_id"])
    print(f"Fetching depth for {len(targets)} markets...")

    rows = []
    for i, (_, r) in enumerate(targets.iterrows()):
        rows.append(fetch_one(r["platform"], str(r["market_id"])))
        if (i + 1) % 25 == 0:
            print(f"  {i+1}/{len(targets)} fetched")
        time.sleep(delay)

    df = pd.DataFrame(rows)
    df["market_id"] = df["market_id"].astype(str)
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_csv, index=False)
    print(f"Wrote {len(df)} depth rows to {out_csv}")
    if not df.empty:
        ok = df["best_ask"].notna().sum()
        print(f"  Got top-of-book for {ok}/{len(df)}")


if __name__ == "__main__":
    run()
