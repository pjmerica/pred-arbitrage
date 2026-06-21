"""Run all scrapers, matcher, and arb scanner with orderbook depth."""
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

for name, cmd in steps:
    print(f"\n{'='*60}\n{name}\n{'='*60}", flush=True)
    result = subprocess.run(cmd, cwd=str(ROOT))
    if result.returncode != 0:
        print(f"ERROR: {name} failed with exit code {result.returncode}", flush=True)
        sys.exit(result.returncode)

print("\nAll done.", flush=True)
