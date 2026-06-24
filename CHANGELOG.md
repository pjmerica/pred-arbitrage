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
