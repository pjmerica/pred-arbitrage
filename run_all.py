"""Run all scrapers, matcher, and arb scanner with orderbook depth.

Modes:
  py run_all.py            full refresh: scrape every market, re-match,
                           price (~32,700 API requests, ~15 min). CI: 2x/day.
  py run_all.py --reprice  fast re-price (2026-09-25): keep the last full
                           run's matched pairs, re-fetch ONLY their order
                           books and PredictIt's quotes, recompute the board
                           (~1,200 requests, ~6 min). CI: every 2h (reprice.yml). Catches
                           arbs that open/close between full scrapes without
                           re-pricing all ~31k Polymarket markets.
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent

steps = [
    ("Kalshi scraper",        [sys.executable, "scrapers/kalshi.py"]),
    ("Polymarket scraper",    [sys.executable, "scrapers/polymarket.py"]),
    # Replace Polymarket's gamma-cached bid/ask with live CLOB orderbook
    # prices for EVERY market. Gamma snapshots lag the live book by
    # minutes-to-hours on low-volume markets; the matcher would otherwise
    # pair stale gamma quotes against fresh Kalshi quotes and produce
    # fake arbs. ~4-6 min runtime with 16 worker threads. Added 2026-06-21
    # after the NZ recognize-Palestine fake-20pp-arb incident.
    ("Polymarket live freshen", [sys.executable, "scripts/freshen_polymarket.py"]),
    ("PredictIt scraper",     [sys.executable, "scrapers/predictit.py"]),
    # House incumbents — feeds utils/races.py with updated open-seat info
    # for the elections module. Optional: races.py falls back to a static
    # HOUSE_KNOWN_OPEN list if the JSON isn't present.
    ("House incumbents",      [sys.executable, "scrapers/house_incumbents.py"]),
    ("Matcher",               [sys.executable, "scripts/matcher.py"]),
    # 2026 US-election arb pairs — ported from polling-agg-2026 (sibling
    # repo). Writes data/processed/election_pairs.csv which arb_scanner
    # appends to its own fuzzy-matcher output. Runs alongside the
    # existing matcher rather than replacing it; duplicates are
    # acceptable today and can be deduplicated later.
    ("Elections (US 2026)",   [sys.executable, "scripts/elections.py"]),
    ("Arb scanner (pass 1)",  [sys.executable, "scripts/arb_scanner.py"]),
    ("Fetch orderbook depth", [sys.executable, "scripts/fetch_depth.py"]),
    ("Arb scanner (pass 2)",  [sys.executable, "scripts/arb_scanner.py"]),
]

REPRICE_STEPS = [
    ("PredictIt scraper",     [sys.executable, "scrapers/predictit.py"]),   # 1 request
    ("Arb scanner (pass 1)",  [sys.executable, "scripts/arb_scanner.py"]),  # emits depth_targets.csv
    ("Fetch orderbook depth", [sys.executable, "scripts/fetch_depth.py"]),  # matched markets only
    ("Arb scanner (pass 2)",  [sys.executable, "scripts/arb_scanner.py"]),
]

import os
if "--reprice" in sys.argv:
    steps = REPRICE_STEPS
    # Tells arb_scanner the Kalshi/Polymarket CSVs are the last FULL run's
    # (pair list + display prices); the basket math uses the live books.
    os.environ["PRED_ARB_REPRICE"] = "1"

for name, cmd in steps:
    print(f"\n{'='*60}\n{name}\n{'='*60}", flush=True)
    result = subprocess.run(cmd, cwd=str(ROOT))
    if result.returncode != 0:
        print(f"ERROR: {name} failed with exit code {result.returncode}", flush=True)
        sys.exit(result.returncode)

print("\nAll done.", flush=True)
