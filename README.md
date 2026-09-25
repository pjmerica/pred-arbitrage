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

Four tabs, filtered by row category:

- **All** — every matched arb pair, with the usual filters (type,
  platform pair, gap range, volume, settle window, suspicious-hide).
- **🗳 Elections** — 2026 US Senate / Governor / House races. Pulls from
  two paths: the fuzzy matcher's Politics category AND a ported version
  of polling-agg-2026's election scanner (`scripts/elections.py`). The
  ported logic adds party-level Dem/Rep markets, per-candidate
  general-election pairs, and per-candidate primary pairs — all matched
  on canonical race_id rather than fuzzy text.
- **⚽ Sports** — every sports pair unfiltered. For browsing the
  cross-listed sports universe regardless of arb size.
- **Other** — everything else (entertainment, crypto, weather, policy,
  etc).

Each pair includes:

- both legs' exact questions, each linked to that exact market;
- raw gap and net gap after fees;
- the Yes/No basket, with a hedged $100 split and the max stake at the quoted prices;
- a `⚠ verify` badge listing every warning.

Only pairs matched on structured keys (same crypto strike and window, same tournament and player, same race and party) can be **Guaranteed**. Pairs matched on similar titles show as **Unverified**, as does any basket above a 15% return.

## How it works

Between full runs, a lightweight re-price workflow (`reprice.yml`) re-checks the order books of already-matched pairs every 2 hours (~1,200 requests), so the board catches arbs that open and close within a few hours.

A GitHub Actions workflow runs twice daily (**12:30 + 00:30 UTC** —
08:30 and 20:30 ET; offset 30 min from
[polling-agg-2026](https://github.com/pjmerica/polling-agg-2026) so the
two repos don't hit the same APIs simultaneously). Each run:

0. Runs `tests/` (regression cases for every past fake-arb incident);
   a failure stops the refresh before anything is published.
1. Scrapes Kalshi (v2 trade-api), Polymarket (gamma), PredictIt (full API).
2. Runs structured matchers (crypto thresholds, tournaments, primary
   nominees, political race_id) and a fuzzy text matcher within category
   groups. Fuzzy pairs must also agree on an outcome signature
   (`utils/proposition.py`: placement, period, division, teams, date,
   score, person...).
3. Runs `scripts/elections.py` — the US-2026 election-specific arb
   builder ported from polling-agg-2026. Matches party-level Dem/Rep
   markets on canonical race_id, plus per-candidate general and primary
   markets keyed on `(state, office, district, candidate_last_name)`.
   Writes `data/processed/election_pairs.csv` which the scanner appends
   on top of its fuzzy output (duplicates with the fuzzy Politics path
   are acceptable today and may be deduplicated later).
4. Computes cross-platform price gaps. For any pair > 30pp gap, fetches
   each market's resolution rules and runs a text similarity check
   (`scripts/scrutiny.py`): pairs scoring under 75 are tagged
   `criteria_warn` and can't be Guaranteed (low scores warn instead of
   dropping, since 2026-09-24). Manual excludes in
   `data/processed/excluded_pairs.json` are dropped.
5. Tags every row with `category_bucket ∈ {Elections, Sports, Other}`
   so the dashboard tabs can filter without re-parsing the raw category.
6. Fetches live orderbook depth for matched pairs and re-runs the
   scanner to surface top-of-book size and "tradeable" depth within 1pp
   and 3pp of the best ask.
7. Commits the refreshed `docs/arb_data.js` back to master and lists
   every guaranteed/unverified row in the Actions job summary.

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
