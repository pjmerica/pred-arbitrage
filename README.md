# pred-arbitrage

A live dashboard for **cross-platform prediction-market arbitrage**.
Scrapes Kalshi, Polymarket, and PredictIt; matches similar markets across
platforms; surfaces price gaps that might be tradeable. Published at
[pjmerica.github.io/pred-arbitrage](https://pjmerica.github.io/pred-arbitrage/).

This is the generalized sibling of
[polling-agg-2026](https://github.com/pjmerica/polling-agg-2026) — that one
focuses on US elections; this one covers the full universe (sports,
entertainment, crypto, politics, weather, etc.).

## What it shows

Two tabs:

- **Markets** — every matched arb pair, with the usual filters (type,
  platform pair, gap range, volume, settle window, suspicious-hide).
- **⚽ Sports** — every sports pair unfiltered. For browsing the
  cross-listed sports universe regardless of arb size.

Each pair includes raw gap / net gap after fees / guaranteed-return %
where applicable / tradeable depth / a `⚠ verify` badge listing all
warnings (wide gap, wide live spread, one-sided book, thin depth,
resolution-criteria mismatch).

## How it works

A GitHub Actions workflow runs twice daily (**12:30 + 00:30 UTC** —
08:30 and 20:30 ET; offset 30 min from
[polling-agg-2026](https://github.com/pjmerica/polling-agg-2026) so the
two repos don't hit the same APIs simultaneously). Each run:

1. Scrapes Kalshi (v2 trade-api), Polymarket (gamma), PredictIt (full API).
2. Runs a fuzzy text matcher across all three platforms, restricted to
   within category groups. Several guards strip false matches
   (candidate-name mismatch, sub-bet type mismatch, threshold-bucket
   mismatch, etc.).
3. Computes cross-platform price gaps. For any pair > 30pp gap, fetches
   each market's resolution rules and runs a text similarity check
   (`scripts/scrutiny.py`): pairs scoring under 50 are dropped entirely,
   50–75 are kept but tagged `criteria_warn`, ≥75 pass clean.
4. Fetches live orderbook depth for matched pairs and re-runs the
   scanner to surface top-of-book size and "tradeable" depth within 1pp
   and 3pp of the best ask.
5. Commits the refreshed `docs/arb_data.js` back to master.

GitHub Pages auto-redeploys from `/docs`. Cron is best-effort — actual
fire time can lag 5–30 min.

If any scraper returns empty data (transient outage or API change), the
run fails fast and the commit step is skipped, so the live dashboard
keeps showing the last good snapshot rather than going stale silently.

## Running locally

Requires **Python 3.12** (the version pinned in
`.github/workflows/refresh.yml`).

```bash
pip install -r requirements.txt
python run_all.py
```

A full run takes ~10–15 minutes — most of that is the Polymarket scrape
(~25k active markets) and `fetch_depth.py` polling orderbooks for every
matched pair. During iterative development you can re-run just one step
against the already-cached CSVs in `data/raw/`:

```bash
python scripts/matcher.py        # re-match on cached scraper output
python scripts/arb_scanner.py    # re-score gaps + suspicion reasons
```

Outputs land in `data/raw/` (gitignored) and `docs/arb_data.js` (tracked).
Open `docs/index.html` directly to view the dashboard.

## Documentation

- [`HANDOFF.md`](./HANDOFF.md) — full architecture, matcher guards,
  scrutiny pipeline, file map, gotchas, failure semantics.

## License

See [`LICENSE`](./LICENSE). All rights reserved — personal project, not
open source. No license is granted to copy, modify, or redistribute.
