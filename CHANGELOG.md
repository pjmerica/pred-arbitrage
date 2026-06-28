# Changelog

All notable changes to the pred-arbitrage codebase. Each entry pairs a
short summary with the commit hash so the full diff is reachable via
`git show <hash>`.

**Rule (adopted 2026-06-24)**: every code change must add an entry
here. Data-refresh commits from the workflow (subjects starting with
`data refresh:`) are autonomous and excluded — they're noise in this
view. Hand-written commits all get an entry.

Format: `[hash] commit subject — one-sentence summary of WHY.`

---

## Unreleased

(nothing pending)

## 2026-06-28

- `[ef74bae]` matcher: tournament-winner matcher (tennis + FIFA World Cup) — explored unmatched markets across all categories to find more cross-platform overlaps. Tennis: Kalshi `<Tournament> Men's/Women's Singles Winner — <Player>` vs Polymarket `Will <Player> win the 2026 Men's/Women's <Tournament>?` — token_sort_ratio scores 76-78, below sports threshold of 80, so fuzzy alone never catches them. World Cup: Kalshi `2026 FIFA World Cup Winner — <Team>` vs Polymarket `Will <Team> win the 2026 FIFA World Cup?` — naming styles too divergent for fuzzy. New `match_tournament_winner()` in matcher.py extracts `(event_class, gender_or_country, contestant)` keys via dedicated regexes and exact-matches. Local: **40 pairs** (14 tennis: Sinner US Open 5pp gap, Alcaraz US Open 8pp gap; 26 World Cup: Mexico 1.9pp, USA 1.4pp). Fed (Kalshi per-meeting vs Polymarket year-total cuts), NFL/NBA season (one-sided coverage), Tesla deliveries (bucket schema, blocked by same 3-leg issue as oil) all investigated and intentionally not built — no clean overlap or no schema fit.
- `[3bed213]` docs: defer range-bucket matcher with reasoning — investigated extending matcher to handle Polymarket bucket-settle markets (`cl-settle-jun-2026` $70-$77 vs Kalshi WTI synthetic shows a 22pp gap). Actionable arb requires 3 legs (Polymarket bucket + Kalshi NO at lower strike + Kalshi YES at upper strike); our pair schema is 1-vs-1. User chose to defer rather than extend the schema. Logged as AUDIT to-do #0 with the unblock sketch (add `market_id_c` column, 3-leg basket-cost path in `compute_arb`, third dashboard column; ~2-3 hours). Threshold matcher (7cedd68) already surfaces the 1-vs-1 crypto/oil `reach $X` arbs which were the bigger immediate win.
- `[7b6faa8]` docs: reflect threshold matcher in HANDOFF / AUDIT / NOTES / SCRAPER_NOTES — 7cedd68 added a third matching path but the docs still said "two matching paths". Updated HANDOFF.md (full match_threshold_pairs section: asset list, pattern catalog, PER_CATEGORY_THRESHOLD overrides), AUDIT.md ("Two matching paths" → "Three matching paths", matcher.py file-table row refreshed to ~900 LOC), NOTES_FOR_REVIEWER.md (mental-model + one-hour reading list now mention all three paths), SCRAPER_NOTES.md (pipeline line for matcher.py now lists all three paths). README.md unchanged (only had a generic `python scripts/matcher.py` invocation, no architecture description that needed updating).
- `[7cedd68]` matcher: threshold-comparison matcher for crypto/commodities — fuzzy matcher misses pairs where the same event is phrased very differently across platforms (Kalshi "How high will Bitcoin get in 2026? — Above $200,000.00" vs Polymarket "Will Bitcoin reach $200,000 by December 31, 2026?"). New `match_threshold_pairs()` in scripts/matcher.py parses (asset, direction, strike, month) tuples from titles and pairs Kalshi+Polymarket markets sharing (asset, direction, month) within strike tolerance — 2% for crypto/precious metals, $1.50 for commodities. Closest-strike-only rule prevents one Polymarket strike spawning N near-strike Kalshi duplicates. Local: 24 new threshold pairs across BTC/ETH/SOL year-end, plus Oil June (Bitcoin dip $50K 0.5pp gap; Ethereum dip $1250 8.5pp gap; Solana reach $200 6.5pp gap; BTC $200k 2.5pp gap). Matched pairs total 152 → 325 across this and the prior threshold-lowering work. Range-bucket matcher (Kalshi laddered vs Polymarket bucket settle) deferred until this threshold matcher proves clean in production.
- `[9461898]` dashboard: revert Settle Within default to Any time — 8d6647a defaulted the page filter to 30 days; user clarified that preference was personal, not a public-default. Reverted so the dashboard's first impression isn't an arbitrary cut. 3/14/180-day options stay available.
- `[8d6647a]` matcher: per-category fuzzy thresholds + dashboard 30d-settle default — user asked why arb opportunities are all long-dated election stuff and not faster-settling markets. Investigation: we scrape 37K Kalshi + 6K Polymarket markets across 15+ categories but only 152 pairs make it through matching, almost all elections. Root cause: a single FUZZY_THRESHOLD=88 (set tight to avoid candidate-vs-party false positives in elections) was filtering out non-political pairs where the same event is phrased very differently across platforms ("How high will BTC get in June?" vs "Will Bitcoin hit $150k by June 30?"). Changes: (1) `scripts/matcher.py` adds PER_CATEGORY_THRESHOLD overrides — crypto 72, finance/science/companies 74, sports 80, etc. politics stays at 88. (2) `docs/index.html` Settle Within filter now defaults to 30 days (was Any time) and gets 3/14/180-day options for finer slicing. Local test: matched pairs jumped 152 → 280, surfacing real overlaps: Ballon d'Or candidates (10+ matches), Ronaldo's next club, OpenAI/Anthropic IPO order, Fed rate cut, album releases, Starship launch counts. Bucket-edge false positives (e.g. Kalshi "5 launches" pair to Polymarket "5-6 launches") still possible — flagged as future scrutiny guard. Category-specific matchers (price-level crypto, oil ladder, deadline questions) intentionally deferred until we see what threshold lowering alone surfaces in production.

---

## 2026-06-24

- `[fc09dce]` kalshi: pull real NO bid/ask + wire into scanner — Kalshi exposes no_bid_dollars / no_ask_dollars on every market but we'd been ignoring them and inferring `1 - YES_bid` instead. The K-No column on the dashboard always showed "—" because no_a_real / no_b_real were never True for Kalshi-side rows. Three changes: (1) `scrapers/kalshi.py` preserves no_bid/no_ask alongside yes_bid/yes_ask in the CSV. (2) `scripts/arb_scanner.py` builds a kalshi_no_lookup dict from the raw CSV at startup, falls back to it when fetch_depth's depth_no_* is empty for a Kalshi side. (3) compute_arb's no_a_real / no_b_real flags now fire for Kalshi rows, populating K-No on the dashboard. Smoke test against backfilled local data showed 51 Kalshi-side rows with real NO flagged. Kalshi's matching engine enforces YES+NO=1 so the numbers are mathematically equivalent to the inferred ones, but having real values lets the dashboard's K-No column actually populate and removes the "inferred / not really fetched" gap from the data model.
- `[506f80e]` docs: introduce CHANGELOG.md — established the convention that every hand-written commit gets a one-line entry here. Data-refresh commits from the cron are excluded. Backfilled every hand-written commit from 2026-06-20 onward.
- `[39d8491]` matcher: drop pairs whose subject diverges on a common-prefix template — fixes the Taiwan/Somaliland fake pair where the existing `extract_subject` guard's verb list (win/be/become/...) missed "Will Trump recognize <X>" templates. New guard: when titles share a 3+ word common prefix and the next token diverges with both being alphabetic non-stopwords ≥3 chars, drop. Sat after the existing subject guard so the strict surname logic still runs first for "Will X win/be..." patterns.
- `[9f7816b]` dashboard: add 6 per-platform YES/NO ASK columns — K-Yes, K-No, Po-Yes, Po-No, Pr-Yes, Pr-No showing fillable ASK in cents. Cells `—` when the platform isn't in the pair or when real bid/ask wasn't fetched. Complements the existing midpoint columns (Prob A / Prob B); the new columns show what you'd actually pay to trade.

## 2026-06-23

- `[baf0cf6]` arb_type: four-bucket taxonomy (guaranteed / pre-fee / price-gap / one-sided) — split the old binary `guaranteed` vs `one-sided` into four labels. `guaranteed` keeps its meaning (locked profit after fees). `pre-fee` is new: basket cost <$1 pre-fee but fees eat the gap. `price-gap` is new: basket cost ≥$1 even pre-fee (markets disagree but no risk-free combo exists). `one-sided` was redefined: real bid/ask wasn't available on either direction. Added per-row `yes_a_real`/`no_a_real`/etc. flags so the dashboard can hover-tip why a row was classified. Dashboard explainer rewritten as a bulleted list of all four buckets, filter dropdown updated, per-row badges color-coded.
- `[0d39d6e]` dashboard: add one-sided definition to the explainer — first attempt at clarifying one-sided. Later subsumed by the four-bucket split above.

## 2026-06-22

- `[c19edb2]` scrutiny: drop pairs with disjoint year sets in question text — when raw_gap > 30pp and both sides' titles contain years (`\b20\d{2}\b`) that don't overlap, drop. Catches the class of fake-arb where the same person/event appears on both platforms with different time horizons. Runs BEFORE the predictit skip so predictit-leg pairs are covered.
- `[d6646bb]` kalshi: preserve raw API title so elections.py uses polling-agg's regex unchanged — new `raw_market_title` CSV column = Kalshi's API title field passed through unmodified. elections.py's `_load_kalshi_general` prefers it for side detection, so the dem/rep regex matches the same input polling-agg matches against. Kept the broadened title fallback for backward compat with stale CSVs (deleteable once cron has run a few times).
- `[f9ad7cc]` elections: match Kalshi House general markets (pred-arb title shape) — broadened the dem/rep regex in `_load_kalshi_general` to also catch "Democrat(?:ic)? party" / "Republican party" alongside the polling-agg-shaped "...win..." pattern. Stopgap; d6646bb above is the cleaner fix.
- `[e6dd0f6]` kalshi: build URLs with event_ticker so they land on the right event — single-segment `kalshi.com/markets/{series_ticker}` URLs made Kalshi's SPA pick an arbitrary event when the series contained multiple. Two-segment form `{series_lower}/{event_ticker_lower}` pins to the right event. User reported NH-01 R Noveletsky link going to NH-02.
- `[6311fe7]` docs: codify per-platform display-price rule so we don't relitigate — documented in 6 places (HANDOFF, NOTES_FOR_REVIEWER, AUDIT, kalshi.py, freshen_polymarket.py, arb_scanner.compute_arb) that Kalshi shows `last_price`, Polymarket shows midpoint, PredictIt shows bestBuy/bestSell midpoint. Don't unify — they really do display different things.

## 2026-06-21

- `[aadec8d]` freshen: revert Polymarket display to midpoint (last_trade was wrong choice) — user reported NY-13 Espaillat Polymarket side shipping at 43% when polymarket.com showed 62%. Live CLOB midpoint was 61.5% but last_trade_price was 0.43 (stale single trade). Kalshi shows last_price; Polymarket shows midpoint. Per-platform display rule.
- `[9f47a58]` audit prep: NOTES_FOR_REVIEWER + tighten workflow + small polish — added top-level NOTES_FOR_REVIEWER.md, replaced `git add data/ docs/` with an explicit allowlist, condensed verbose comments, added stderr logging to silent JSON-parse fallbacks in polymarket.py.
- `[3c33ad1]` display: use last_trade_price as implied_prob; stop overwriting it — Kalshi scraper now picks implied_prob from `last_price` first (matches kalshi.com). Removed the depth-time midpoint override in arb_scanner that was clobbering Kalshi's last_price.
- `[1a83dc6]` docs: document today's pagination/arb-math journey + next-handoff to-do — added HANDOFF "Polymarket pagination" section, full AUDIT entry for the keyset detour + revert + arb math rewrite, sharp 11-item to-do list.
- `[2781921]` polymarket: revert keyset (broken) -> offset (capped at 2000) — `/events/keyset` returns same 100 events forever. Reverted to `/events?offset=N` capped at 2000. Real fix (rewrite to `clob.polymarket.com/markets`) tracked in AUDIT.
- `[c448b91]` fetch real Polymarket NO orderbook, use it in arb math — depth_targets.csv now carries `no_market_id`; fetch_depth pulls both YES and NO books. compute_arb uses real NO ask for the buy-NO leg instead of inferring `1 - YES_bid`.
- `[65d1ccf]` arb_scanner: use real ASK / (1-BID) for arb math, not midpoints — Somaliland incident. Math was using midpoints; real fillable basket uses YES ask + NO ask. Smaller true arbs survive, fake midpoint-arbs go away.
- `[534ecd2]` polymarket: keyset pagination + drop dead props (close_date past) — switched to /events/keyset (later found broken). Added scraper-level date filters: Kalshi `close_date < today`, Polymarket `end_date < today`. Both platforms leave dead markets flagged active.
- `[dfc471e]` freshness: live CLOB freshen for every polymarket market + staleness guard — new `scripts/freshen_polymarket.py` re-fetches `clob.polymarket.com/book` for every market in parallel after the gamma scrape. `_assert_scrape_freshness()` guard refuses to write arb_data.js if any CSV is > 12h old.
- `[af9d5ca]` liveness: only ship pairs that can actually be traded right now — Kalshi scraper drops rows with `yes_bid <= 0` at source. Polymarket scraper drops gamma rows with bid-ask spread > 8pp (later reverted in the freshen pass). Scanner drops pairs where live orderbook on either side has an ask but no bid.
- `[c5c2a97]` arb_scanner: drop pairs whose settle_date is already in the past — defense-in-depth past-settle-date filter in addition to the scraper-level close_date/end_date drops.

## 2026-06-20

- `[59f4ffc]` elections: port polling-agg arb scanner; add Elections/Sports/Other tabs — copied utils/races.py + scrapers/house_incumbents.py from polling-agg. New `scripts/elections.py` (~590 LOC) with three matching paths: general (party-level Dem/Rep), general_candidate, primary_candidate. Output rows tagged `category='Elections'`, `category_bucket='Elections'`. Dashboard tabs: All / Elections / Sports / Other.
- `[3c7bba8]` audit pass: unify HTTP headers, sync FEES to 2%, debounce search, AUDIT.md — created `utils/http_headers.py` (BROWSER_UA, DEFAULT_HEADERS, browser_xhr_headers). Migrated kalshi/polymarket/predictit/fetch_depth/scrutiny to import from it. Synced FEES from 3%/12%/3% to 2%/12%/2% (matches polling-agg). 150ms debounce on dashboard search.

---

## How to update this file

When you add a commit:

1. If the entry deserves its own line (most code commits do), add it
   under the dated section for today. Format:
   `` - `[hash]` <commit subject> — <one-sentence WHY> ``
2. Data-refresh commits from the workflow get NO entry. They're
   autonomous.
3. If you're in the middle of a multi-commit feature, add entries to
   the **Unreleased** section at the top and move them under the
   dated section when the feature ships.

Why dated sections instead of semver versions: this project has no
releases — it's a continuously-deployed dashboard. Date is the
only sensible group key.
