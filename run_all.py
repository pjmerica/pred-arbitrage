"""Run all scrapers, matcher, and arb scanner with orderbook depth."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent

steps = [
    ("Kalshi scraper",        [sys.executable, "scrapers/kalshi.py"]),
    ("Polymarket scraper",    [sys.executable, "scrapers/polymarket.py"]),
    ("PredictIt scraper",     [sys.executable, "scrapers/predictit.py"]),
    ("Matcher",               [sys.executable, "scripts/matcher.py"]),
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
