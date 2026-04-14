"""Run all scrapers, matcher, and arb scanner in sequence."""
import subprocess, sys

steps = [
    ("Kalshi scraper",     [sys.executable, "scrapers/kalshi.py"]),
    ("Polymarket scraper", [sys.executable, "scrapers/polymarket.py"]),
    ("PredictIt scraper",  [sys.executable, "scrapers/predictit.py"]),
    ("Matcher",            [sys.executable, "scripts/matcher.py"]),
    ("Arb scanner",        [sys.executable, "scripts/arb_scanner.py"]),
]

for name, cmd in steps:
    print(f"\n{'='*50}\n{name}\n{'='*50}")
    result = subprocess.run(cmd, cwd=str(__import__('pathlib').Path(__file__).parent))
    if result.returncode != 0:
        print(f"ERROR: {name} failed with exit code {result.returncode}")
        sys.exit(1)

print("\n✓ All done — open docs/index.html to view results")
