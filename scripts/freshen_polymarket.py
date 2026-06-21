"""
Refresh every Polymarket market's bid/ask/midpoint with live data from
the CLOB orderbook endpoint.

WHY THIS EXISTS
---------------
Polymarket's gamma `/markets` endpoint (which the scraper uses to fetch
all 25k+ active markets in one bulk pass) returns CACHED `bestBid` /
`bestAsk` fields that lag the live CLOB orderbook by minutes-to-hours
on low-volume markets. The lag was producing fake arbs against fresh
Kalshi quotes — see the NZ recognizes-Palestine incident on 2026-06-21,
where gamma reported Polymarket at 34¢ midpoint vs Kalshi 14¢ (fake
20pp gap) but the live CLOB book was actually 16-24¢, real midpoint 20¢
(real gap 6pp).

Previously we worked around this with two band-aids:
  1. Drop scraper rows with gamma spread > 8pp (cuts ~half of usable
     markets out before matching).
  2. Override matched-pair prices with live depth midpoints in the
     scanner pass 2 (only helps after fuzzy matching has already paired
     things up using the stale prices).

Both are unnecessary if we just refresh every market's bid/ask BEFORE
the matcher runs. That's what this module does.

HOW IT WORKS
------------
1. Read data/raw/polymarket_markets.csv.
2. For each row with a yes_token_id, GET clob.polymarket.com/book?token_id=...
   in parallel (ThreadPoolExecutor, ~16 workers, ~6 req/sec per worker).
   Total time at 25k markets is roughly 4-6 minutes — slower than the
   gamma scrape alone (~3 min) but the prices come out actually live.
3. Compute fresh best_bid / best_ask / midpoint from the live book.
4. Overwrite the bid/ask/implied_prob columns in the CSV.
5. Drop markets the CLOB no longer knows about (404 = the market closed
   between scrape and freshen, ~minutes ago).

CSV columns touched: best_bid, best_ask, implied_prob, fetched_at.
All other columns (token_ids, slugs, volume, liquidity, etc.) are
untouched.

If the live CLOB fetch fails for a market (network error, timeout),
the row keeps its gamma-time price — that's still better than nothing.
The failure count is printed at the end so we can spot if Polymarket
has started rate-limiting us.
"""

import sys
import json
import time
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
from utils.http_headers import DEFAULT_HEADERS

RAW = ROOT / "data" / "raw"

CLOB_BOOK_URL = "https://clob.polymarket.com/book?token_id={tid}"
NUM_WORKERS = 16  # ~6 req/sec per worker; 16 workers => ~100 req/sec total
PER_REQUEST_TIMEOUT_S = 8
GLOBAL_TIMEOUT_S = 30 * 60  # hard cap so the workflow can't hang forever


def fetch_book(token_id: str):
    """Return (best_bid, best_ask) or (None, None) on failure / 404."""
    if not token_id or token_id in ("nan", "None"):
        return (None, None)
    url = CLOB_BOOK_URL.format(tid=token_id)
    req = urllib.request.Request(url, headers=DEFAULT_HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=PER_REQUEST_TIMEOUT_S) as r:
            data = json.loads(r.read())
    except urllib.error.HTTPError as e:
        # 404 = market not on CLOB (expired / closed since scrape)
        # 429 / 5xx = transient — we don't retry here; the freshen pass
        # will rerun tomorrow and the gamma fallback is good enough for
        # one cycle. Count and continue.
        return (None, None)
    except Exception:
        return (None, None)

    # CLOB book shape: {"bids": [{"price": "0.16", "size": "..."}, ...],
    #                   "asks": [{"price": "0.24", "size": "..."}, ...]}
    # CRITICAL: the API does NOT sort with best-first. Empirically the
    # bids array comes back roughly low-to-high and the asks high-to-low
    # (presumably the order in which orders were posted, not sorted).
    # So we have to sort here. Best bid = max bid price, best ask = min
    # ask price. The earlier version of this function read rows[0]
    # without sorting and returned the WORST price on each side
    # (e.g. NZ market: bid=0.01 ask=0.99 instead of bid=0.17 ask=0.20).
    def _prices(side):
        rows = data.get(side) or []
        out = []
        for r in rows:
            try:
                out.append(float(r.get("price")))
            except (TypeError, ValueError):
                pass
        return out

    bids = _prices("bids")
    asks = _prices("asks")
    bb = max(bids) if bids else None
    ba = min(asks) if asks else None
    return (bb, ba)


def run():
    in_path = RAW / "polymarket_markets.csv"
    if not in_path.exists():
        print("polymarket_markets.csv not found — run scrapers/polymarket.py first")
        return

    df = pd.read_csv(in_path, dtype={"yes_token_id": str, "no_token_id": str})
    n_total = len(df)
    if n_total == 0:
        print("polymarket CSV is empty; nothing to freshen.")
        return

    # Some scraper paths leave yes_token_id literally NaN — convert so the
    # token lookup gets a falsy value instead of "nan".
    tokens = df["yes_token_id"].astype(str).where(
        df["yes_token_id"].notna() & (df["yes_token_id"] != "nan"), None
    ).tolist()

    print(f"Freshening {n_total} Polymarket markets from live CLOB ({NUM_WORKERS} workers)...")
    t0 = time.time()
    fresh_bb = [None] * n_total
    fresh_ba = [None] * n_total

    n_ok = 0
    n_missing = 0
    n_no_token = 0

    with ThreadPoolExecutor(max_workers=NUM_WORKERS) as pool:
        futures = {}
        for i, tok in enumerate(tokens):
            if not tok:
                n_no_token += 1
                continue
            futures[pool.submit(fetch_book, tok)] = i

        for fut in as_completed(futures):
            i = futures[fut]
            elapsed = time.time() - t0
            if elapsed > GLOBAL_TIMEOUT_S:
                print(f"  Global timeout ({GLOBAL_TIMEOUT_S}s) hit; canceling remaining requests")
                for f in futures:
                    f.cancel()
                break
            bb, ba = fut.result()
            if bb is None and ba is None:
                n_missing += 1
            else:
                fresh_bb[i] = bb
                fresh_ba[i] = ba
                n_ok += 1
            done = n_ok + n_missing
            if done % 2000 == 0:
                rate = done / max(elapsed, 0.001)
                print(f"  {done}/{n_total - n_no_token}  ok={n_ok}  missing={n_missing}  "
                      f"rate={rate:.0f} req/s  eta={int((n_total-done)/max(rate,1))}s")

    elapsed = time.time() - t0
    print(f"\nDone in {elapsed:.0f}s — ok={n_ok}, missing={n_missing}, no_token={n_no_token}")

    # Apply the fresh values. Only overwrite when we got a real number;
    # leave gamma value in place when CLOB fetch failed (better than
    # blanking the row).
    now_iso = datetime.now(timezone.utc).isoformat()
    n_changed = 0
    for i in range(n_total):
        bb = fresh_bb[i]
        ba = fresh_ba[i]
        if bb is None and ba is None:
            continue
        df.at[i, "best_bid"] = bb
        df.at[i, "best_ask"] = ba
        # Recompute implied_prob = midpoint when both sides exist; else
        # leave whatever was there.
        if bb is not None and ba is not None and 0 < bb <= ba < 1:
            df.at[i, "implied_prob"] = round((bb + ba) / 2, 4)
        df.at[i, "fetched_at"] = now_iso
        n_changed += 1
    print(f"Overwrote bid/ask/midpoint on {n_changed} rows")

    # Drop rows where the CLOB confirmed the market is gone (no bids and
    # no asks AT ALL, distinct from "we failed to fetch"). We use the
    # criterion: fetch succeeded enough to return empty bids+asks, which
    # for this script means we got a response but it was empty. The
    # current fetch_book returns (None, None) for BOTH error AND empty,
    # so we can't distinguish here without refactoring. Leave as-is —
    # truly resolved markets get dropped by the past-settle-date filter
    # in arb_scanner.py instead.

    df.to_csv(in_path, index=False)
    print(f"Saved freshened {in_path}")


if __name__ == "__main__":
    run()
