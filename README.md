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

A GitHub Actions workflow runs twice daily and:

1. Scrapes Kalshi (v2 trade-api), Polymarket (gamma), PredictIt (full API).
2. Runs a fuzzy text matcher across all three platforms, restricted to
   within category groups. Several guards strip false matches
   (candidate-name mismatch, sub-bet type mismatch, threshold-bucket
   mismatch, etc.).
3. Computes cross-platform price gaps. For any pair > 30pp gap, fetches
   each market's resolution rules and runs a similarity check (the
   `scrutiny.py` module) — pairs with diverging criteria are dropped.
4. Fetches live orderbook depth for matched pairs and re-runs the
   scanner to surface "tradeable" sizes at 1pp and 3pp slippage.
5. Commits the refreshed `docs/arb_data.js` back to master.

GitHub Pages auto-redeploys from `/docs`.

## Running locally

```bash
pip install -r requirements.txt
python run_all.py
```

Outputs land in `data/raw/` (gitignored) and `docs/arb_data.js` (tracked).
Open `docs/index.html` directly to view the dashboard.

## Documentation

- [`HANDOFF.md`](./HANDOFF.md) — full architecture, matcher guards,
  scrutiny pipeline, file map, gotchas, failure semantics.

## License

Personal project. No license granted.
