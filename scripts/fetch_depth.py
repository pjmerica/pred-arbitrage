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
import urllib.error
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
    return _kalshi_stats(data.get("orderbook_fp") or data.get("orderbook") or {})


def _kalshi_stats(ob: dict) -> dict:
    """Top-of-book / depth stats from a Kalshi orderbook_fp dict."""
    out = {"best_bid": None, "best_ask": None,
           "best_bid_size": None, "best_ask_size": None,
           "depth_bid_at_1pp": None, "depth_ask_at_1pp": None,
           "max_buy_size_at_3pp_edge": None}
    ob = ob or {}
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
        # Buying NO on Kalshi fills against resting YES bids, so NO-side
        # depth is the YES-bid stack (the arb scanner needs this for
        # baskets whose Kalshi leg is NO — most of them).
        out["max_no_buy_size_at_3pp_edge"] = round(
            sum(s for p, s in yes_bids if p >= out["best_bid"] - EDGE_PP - 1e-9), 2)

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
    return _poly_stats(data)


def _poly_stats(data: dict) -> dict:
    """Top-of-book / depth stats from a Polymarket CLOB book dict."""
    out = {"best_bid": None, "best_ask": None,
           "best_bid_size": None, "best_ask_size": None,
           "depth_bid_at_1pp": None, "depth_ask_at_1pp": None,
           "max_buy_size_at_3pp_edge": None}
    data = data or {}
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


KALSHI_BOOKS_URL = "https://api.elections.kalshi.com/trade-api/v2/markets/orderbooks"
POLY_BOOKS_URL = "https://clob.polymarket.com/books"


def _kalshi_books_batch(tickers):
    """{ticker: orderbook_fp} for up to 100 tickers in ONE request
    (GET /markets/orderbooks?tickers=a&tickers=b...; verified 2026-10-12:
    100 in, 100 out, same levels as /markets/{t}/orderbook). None on failure."""
    url = KALSHI_BOOKS_URL + "?" + urllib.parse.urlencode([("tickers", t) for t in tickers])
    for attempt in range(4):
        req = urllib.request.Request(url, headers=HEADERS)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                d = json.loads(r.read())
            return {o.get("ticker"): (o.get("orderbook_fp") or o.get("orderbook") or {}) for o in d.get("orderbooks", [])}
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504):
                time.sleep(2 * (attempt + 1)); continue
            return None
        except Exception:
            time.sleep(1)
    return None


def _poly_books_batch(tokens):
    """{token: book} for up to 500 tokens in ONE request (POST /books).
    Tokens without a book are absent. None on failure."""
    body = json.dumps([{"token_id": t} for t in tokens]).encode()
    for attempt in range(4):
        req = urllib.request.Request(POLY_BOOKS_URL, data=body, method="POST",
                                     headers={**HEADERS, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                books = json.loads(r.read())
            return {str(b.get("asset_id")): b for b in books or []}
        except Exception:
            time.sleep(2 * (attempt + 1))
    return None


def _batched(ids, size, fetch_batch, fetch_single):
    """{id: stats} — batch requests, per-id fallback for failed chunks."""
    out, ids = {}, list(dict.fromkeys(ids))
    for i in range(0, len(ids), size):
        chunk = ids[i:i + size]
        got = fetch_batch(chunk)
        if got is None:
            for t in chunk:
                out[t] = fetch_single(t)
        else:
            out.update({t: got.get(t) for t in chunk})
        time.sleep(0.1)
    return out


def run(targets_csv: Path = None, out_csv: Path = None, delay: float = 0.1):
    targets_csv = targets_csv or (PROCESSED / "depth_targets.csv")
    out_csv = out_csv or (RAW / "orderbook_depth.csv")
    if not targets_csv.exists():
        # Fail loudly (see arb_scanner): a missing target list means pass 1
        # didn't run, and exiting 0 hid that from CI.
        raise SystemExit(f"No targets file at {targets_csv}. Run arb_scanner.py first to emit it.")

    targets = pd.read_csv(targets_csv, dtype={"market_id": str, "no_market_id": str})
    targets = targets[targets["platform"].isin(["kalshi", "polymarket"])].copy()
    targets = targets.dropna(subset=["market_id"]).drop_duplicates(subset=["platform", "market_id"])
    # Backwards compat: older depth_targets.csv won't have the no_market_id column.
    if "no_market_id" not in targets.columns:
        targets["no_market_id"] = ""
    targets["no_market_id"] = targets["no_market_id"].fillna("").astype(str)
    print(f"Fetching depth for {len(targets)} markets...")

    # Batched (2026-10-12): the soccer fixture matcher grew the pair list
    # ~10x (9,824 books); one request per book took 43 min. Kalshi 100 /
    # request, Polymarket 500 / request.
    kal = targets.loc[targets["platform"] == "kalshi", "market_id"].astype(str).tolist()
    pol = targets.loc[targets["platform"] == "polymarket", "market_id"].astype(str).tolist()
    pol_no = [x for x in targets.loc[targets["platform"] == "polymarket", "no_market_id"].astype(str)
              if x and x != "nan"]
    k_books = _batched(kal, 100, _kalshi_books_batch, lambda t: None)
    p_books = _batched(pol + pol_no, 500, _poly_books_batch, lambda t: None)
    now = datetime.now(timezone.utc).isoformat()

    def _row(plat, mid, side, book, single):
        d = (_kalshi_stats(book) if plat == "kalshi" else _poly_stats(book)) if book is not None else single(mid)
        d.update({"platform": plat, "market_id": mid, "fetched_at": now, "side": side})
        return d

    rows = []
    n_no_fetched = 0
    for _, r in targets.iterrows():
        plat, mid = r["platform"], str(r["market_id"])
        if plat == "kalshi":
            rows.append(_row(plat, mid, "yes", k_books.get(mid), _kalshi_yes_book))
        else:
            rows.append(_row(plat, mid, "yes", p_books.get(mid), _polymarket_yes_book))
        # Polymarket YES and NO are separate tokens with their own books
        # (inferring no_ask = 1 - yes_bid produced spurious arbs, 2026-06-21).
        no_mid = r.get("no_market_id", "")
        if plat == "polymarket" and no_mid and no_mid != "nan":
            no_row = _row("polymarket", no_mid, "no", p_books.get(no_mid), _polymarket_yes_book)
            no_row["yes_market_id"] = mid
            rows.append(no_row)
            n_no_fetched += 1

    df = pd.DataFrame(rows)
    df["market_id"] = df["market_id"].astype(str)
    if "yes_market_id" not in df.columns:
        df["yes_market_id"] = ""
    df["yes_market_id"] = df["yes_market_id"].fillna("").astype(str)
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_csv, index=False)
    print(f"Wrote {len(df)} depth rows to {out_csv} (incl. {n_no_fetched} NO books)")
    if not df.empty:
        ok = df["best_ask"].notna().sum()
        print(f"  Got top-of-book for {ok}/{len(df)}")


if __name__ == "__main__":
    run()
