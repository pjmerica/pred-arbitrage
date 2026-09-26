"""Write the small CSVs the 2-hourly re-price needs (2026-09-26).

The full refresh used to commit data/raw/kalshi_markets.csv (36 MB) and
polymarket_markets.csv (27 MB) twice a day so the re-price could read them.
The repo pack reached 666 MB, and GitHub rejects any single file over
100 MB. The re-price only looks up the markets in this run's matched and
election pairs (fees, NO tokens, series/slug, close dates, freshness), so we
commit just those rows to data/reprice/ (a few hundred KB) and keep the full
scrapes out of git. run_all.py --reprice copies them back into data/raw/
when the full CSVs are absent (a fresh CI checkout).

Run as the last step of a full refresh, after arb_scanner pass 2.
"""
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).parent.parent
RAW = ROOT / "data" / "raw"
PROCESSED = ROOT / "data" / "processed"
OUT = ROOT / "data" / "reprice"


def _ids():
    ids = set()
    for name in ("matched_pairs.csv", "election_pairs.csv"):
        p = PROCESSED / name
        if not p.exists():
            continue
        df = pd.read_csv(p, dtype=str, low_memory=False)
        for c in df.columns:
            if c.startswith("market_id") or c.endswith("_ticker") or c.endswith("_token") or c == "market_no_id_a" or c == "market_no_id_b":
                ids.update(v for v in df[c].dropna().astype(str) if v)
    return ids


def run():
    ids = _ids()
    if not ids:
        raise SystemExit("trim: no matched/election pairs found; refusing to write empty re-price inputs")
    OUT.mkdir(parents=True, exist_ok=True)
    for name, keys in (("kalshi_markets.csv", ["ticker"]),
                       ("polymarket_markets.csv", ["yes_token_id", "no_token_id"])):
        src = RAW / name
        if not src.exists():
            raise SystemExit(f"trim: {src} missing")
        df = pd.read_csv(src, dtype=str, keep_default_na=False, low_memory=False)
        keep = pd.Series(False, index=df.index)
        for k in keys:
            if k in df.columns:
                keep |= df[k].isin(ids)
        out = df[keep]
        out.to_csv(OUT / name, index=False)
        print(f"  {name}: kept {len(out)} of {len(df)} rows -> {OUT / name}")


if __name__ == "__main__":
    run()
