# Code Audit & To-Do — pred-arbitrage

**Created:** 2026-06-20
**Scope:** every tracked file in the repo (scrapers, scripts, tools, docs,
infra, root). Companion file lives at
`polling-agg-2026/AUDIT.md` and covers the sibling repo. Anywhere two
repos share design (fees, headers, scraper retry shape), the polling-agg
audit is the source of truth.

This is a working document. As issues get fixed, move them out of the
**To-do** section at the bottom and into the **Done** section, with the
commit hash so we can find what changed.

---

## Major design decisions (not bugs — for context)

These are choices that affect how the rest of the audit reads. Don't
"fix" any of these without thinking about the second-order consequences.

- **This repo is the generalized sibling of polling-agg.** Polling-agg
  focuses on US elections only and pulls polling alongside markets;
  pred-arbitrage scrapes every active market on Kalshi / Polymarket /
  PredictIt and runs cross-platform arb matching across the whole
  universe (sports, entertainment, crypto, politics, weather).
- **Three matching paths.** `match_political` joins on canonical
  `race_id`; `match_threshold_pairs` parses
  `(asset, direction, strike, month)` from price-threshold titles
  (Kalshi↔Polymarket only, crypto + commodities); `match_fuzzy` uses
  `rapidfuzz` text similarity within category groups (with per-category
  thresholds in `PER_CATEGORY_THRESHOLD`). Most of the file is the
  10+ guard chain that prevents false fuzzy pairs.
- **The scanner runs twice around `fetch_depth`** — same as polling-agg.
  Pass 1 emits `depth_targets.csv`, fetch_depth populates orderbooks,
  pass 2 joins depth and recomputes suspicion flags.
- **Fail-fast scrapers + skipped commit step.** If any scraper returns
  zero usable rows, `run_all.py` exits non-zero, the workflow's commit
  step is skipped, live dashboard keeps the last good snapshot.
- **`FEES` is the single source of truth.** Kalshi 2%, Polymarket 2%,
  PredictIt 12%. Kept in lockstep with polling-agg.
- **Kalshi GET /events is WAF-protected.** Requires the full
  Sec-Fetch / Referer / Origin set, not just a real-browser UA. See
  `utils/http_headers.py:browser_xhr_headers()`.

---

## Inventory + per-file notes

Lines are post-fixes from this audit.

### Scrapers (`scrapers/`)

| File | LOC | Status | Notes |
|---|---|---|---|
| `kalshi.py` | ~280 | **live, healthy** | Uses `browser_xhr_headers("https://kalshi.com")`. 6-retry exponential backoff with 60s cap (longer than polling-agg's because pred-arb hits `/events?with_nested_markets=true` which the WAF treats more strictly). |
| `polymarket.py` | ~290 | **live, healthy** | Uses `DEFAULT_HEADERS`. Two `except Exception` blocks on JSON parse of `outcomes`/`outcomePrices` — same shape as polling-agg, same recommendation: add a `print(... file=sys.stderr)` so silent skips show up. |
| `predictit.py` | ~120 | **live, healthy** | Uses `DEFAULT_HEADERS`. 4-retry, drops spreads > 15pp. |

### Scripts (`scripts/`)

| File | LOC | Status | Notes |
|---|---|---|---|
| `matcher.py` | ~900 | **live, hot** | Three matching paths (political race_id, threshold-comparison Kalshi↔Polymarket for crypto/commodities, fuzzy text within category groups with `PER_CATEGORY_THRESHOLD` overrides) plus 10+ guards on the fuzzy path. Each guard tracks a specific historical false-pair (candidate-name, sub-bet type, year overlap, threshold buckets, office role, demographic, date anchors, rank, month/day anchor, subject extraction). **Don't loosen any guard without running `tools/deep_check.py` first.** |
| `arb_scanner.py` | ~355 | **live, healthy** | 4% combined fees after the audit (was 6%). Conditional scrutiny import — `except Exception: _scrutinize = None` is acceptable (the scanner runs fine without it). Calls `scripts.scrutiny.scrutinize` for >30pp pairs. |
| `fetch_depth.py` | ~185 | **live, healthy** | Uses `DEFAULT_HEADERS`. `_http_json` swallows exceptions and returns None — caller handles None. |
| `scrutiny.py` | ~195 | **live, healthy** | Uses `DEFAULT_HEADERS`. 7-day cache; SequenceMatcher similarity scoring. PredictIt rule fetch is a no-op (no public endpoint). |

### Utils (`utils/`)

| File | LOC | Status | Notes |
|---|---|---|---|
| `__init__.py` | 0 | empty, **new in this audit** | leave it |
| `http_headers.py` | 53 | **new in this audit** | Mirrors polling-agg's helper. `BROWSER_UA`, `DEFAULT_HEADERS`, `browser_xhr_headers(origin)`. Keep these two files in lockstep when either is edited. |

### Tools (`tools/`)

| File | LOC | Status | Notes |
|---|---|---|---|
| `deep_check.py` | ~260 | **live diagnostic** | Walks the top-50 arb candidates, re-fetches live orderbooks, categorizes as REAL/MARGINAL/THIN/GONE/WRONG_PAIR/PREDICTIT_LEG/ONE_PLATFORM. Two `except Exception` blocks (UTF-8 reconfigure, HTTP) — both acceptable. One long function (`basket_at_slippage` ~60 LOC with 4 nested loops) — could split if expanding. |
| `README.md` | 42 | clean | Documents the diagnostic categories. |

### Docs (`docs/`)

| File | LOC | Status | Notes |
|---|---|---|---|
| `index.html` | ~485 | **live, hot** | Two-tab dashboard (Markets, Sports). Reads `ARB`/`fees`/`updated_at` from `arb_data.js`. Global state in `fType`, `fPair`, `fCategory`, `fMinGap`, `fMaxGap`, `fProfitable`, `fSettle`, `fSearch`, `fSuspicious`. Search input debounced in this audit. |
| `arb_data.js` | generated | tracked but auto-built | Committed by the daily refresh workflow. |

### Infra

| File | Status | Notes |
|---|---|---|
| `run_all.py` | clean | 25 lines, fail-fast on first non-zero exit. |
| `requirements.txt` | **unpinned** | 3 packages, no versions: `pandas`, `numpy`, `rapidfuzz`. **Same recommendation as polling-agg: pin.** |
| `.github/workflows/refresh.yml` | clean | 12:30 + 00:30 UTC cron (30-minute offset from polling-agg so we don't hammer the same APIs simultaneously). |
| `LICENSE` | clean | All-rights-reserved. |
| `README.md` | clean | Updated 2026-06-13. |
| `HANDOFF.md` | clean | Updated 2026-06-13. |

---

## Bugs and risks worth tracking

### Latent regressions

1. **HTTP header drift** — fixed in this audit. Four files (`polymarket.py`,
   `predictit.py`, `fetch_depth.py`, `scrutiny.py`) used to have their
   own `HEADERS = {"User-Agent": "Mozilla/5.0 (research/pred-arb)"}`
   constant. Updating Kalshi's UA in mid-June 2026 left them all out of
   sync. Now everyone imports from `utils/http_headers.py`. `kalshi.py`
   also migrated to `browser_xhr_headers()` instead of inlining the full
   header set.
2. **Fee drift between repos** — was already drifted at audit time
   (polling-agg 2%, pred-arb 3%). Fixed in this pass to 2%/2%/12%
   matching polling-agg. No automated check to keep them in sync.
3. **Matcher guards** — every guard exists because of a specific past
   false-pair. Loosening any guard requires re-running
   `tools/deep_check.py` afterward.

### Defensive coverage gaps

1. **`requirements.txt` is unpinned.** Same risk as polling-agg.
2. **`polymarket.py` swallows JSON parse errors silently.** Same as
   polling-agg — add a `print(..., file=sys.stderr)`.
3. **PredictIt has no public orderbook endpoint.** `scrutiny.py` skips
   it; `fetch_depth.py` skips it; `deep_check.py` categorizes any
   PredictIt-leg pair as `PREDICTIT_LEG` rather than verifying. That's
   the right call but worth knowing.
4. **Sports category is enormous and most don't matter for arbs.**
   Polymarket alone has 11k+ sports markets; only ~40 land in matched
   pairs because the category-group guards are intentionally
   conservative. Loosening would add false positives faster than real
   pairs.

### Low-priority cleanup

1. **`scripts/matcher.py` is 750 lines.** The guard chain has clear
   structure but extracting helpers (`_guard_candidate_name`,
   `_guard_sub_bet_type`, etc.) would help readability.
2. **`tools/deep_check.py:basket_at_slippage` is 60 lines with 4 nested
   loops.** Split into helpers if extending.
3. **No tests anywhere.** Cron is the only safety net.

### Security surface

1. **`innerHTML` writes in `docs/index.html`.** Same low-risk note as
   polling-agg.
2. **No auth, no user data.** Public read-only.
3. **GitHub Actions has `contents: write` only.** No third-party
   tokens.

---

## To-do — next handoff

Ordered by leverage. Items 1-3 are blocking-quality issues; the rest
are improvements.

### 0. (deferred 2026-06-27) Range-bucket matcher for monthly settle markets

Polymarket has bucket-settle markets (`Will Crude Oil settle at $70-$77
in June?`) and Kalshi has dense above-strike ladders for the same
settle date (`Oil Price (WTI) on Jun 30, 2026? — Above $X`). The
**actionable arb is 3-leg**: buy Polymarket `$A-$B`, buy Kalshi NO on
`Above $A`, buy Kalshi YES on `Above $B`. All 3 legs are needed to
pay $1 in every state of the world.

Current pair schema is 1 Kalshi market + 1 Polymarket market —
doesn't model 3-leg arbs. User decision 2026-06-27 (after seeing real
22pp gap on `cl-settle-jun-2026` $70-$77 bucket): defer. Threshold
matcher (7cedd68) already surfaces 1-vs-1 crypto/oil `reach $X` arbs
which were the bigger immediate win.

To unblock when revisited: extend `matched_pairs.csv` with optional
`market_id_c` column, add a 3-leg basket-cost path in
`compute_arb()`, and add a third platform column in the dashboard.
~2-3 hours.

### 1. Rewrite Polymarket scraper to use `clob.polymarket.com/markets`

**Why**: gamma's `/events?offset=N` is hard-capped at offset=2000.
`/events/keyset` is broken (cursor doesn't advance). The CLOB endpoint
paginates properly and returns the full ~55k market universe.

**What you get for free**: the `tokens[]` array contains both YES and
NO `token_id` directly — you can delete the no_token_id lookup we
flow through `depth_targets.csv → fetch_depth.py`. Also a real
`accepting_orders` boolean instead of gamma's lying `active=true` flag.

**Sketch**:
- Endpoint: `https://clob.polymarket.com/markets?next_cursor=...`
- Returns `{data: [...], next_cursor: "...", limit, count}`.
- next_cursor is base64-encoded offset (`MTAwMA==` = 1000); pass it
  back as `next_cursor` param. Genuinely advances. End-of-data signal
  is `next_cursor == "LTE="` (base64 `-1`).
- Each row has different fields than gamma: `condition_id`, `question`,
  `tokens[].token_id` (YES and NO), `end_date_iso`, `accepting_orders`,
  `enable_order_book`. Need to rewrite `parse_market()` to map these
  to our CSV schema (yes_token_id, no_token_id, question, end_date,
  implied_prob, best_bid, best_ask, liquidity, volume, ...). Some
  fields aren't in CLOB rows — `liquidity` and `volume` come from
  gamma; might need to keep gamma as a supplementary fetch for those.
- Filter: `accepting_orders == true` AND `enable_order_book == true`
  AND `end_date_iso >= today`.

After this is in, `freshen_polymarket.py` becomes mostly redundant
(CLOB metadata is fresher than gamma) — could be retired or kept as a
defensive depth-time price refresh.

### 2. Flow PredictIt `bestBuyYes` / `bestSellYes` through as real bid/ask

**Why**: PredictIt has no live orderbook, but the `bestBuyYesCost` and
`bestSellYesCost` fields ARE actual bid/ask. Today we throw them away
in `scrapers/predictit.py` after using them for spread-filter, then
the arb scanner uses a synthetic ±5pp spread around the midpoint as a
stopgap. Result: PredictIt-leg arbs are still noisier than they need
to be.

**What to do**:
- `scrapers/predictit.py`: write `best_buy_yes` and `best_sell_yes`
  through to the CSV (they're already there) — already done; verify.
- `scripts/matcher.py:load_predictit()`: preserve those columns into
  `matched_pairs.csv`.
- `scripts/arb_scanner.py`: when platform is predictit, populate
  `bid_a/ask_a` (or b) from those fields instead of the synthetic
  ±5pp. The PredictIt scrutiny path in `compute_arb` should fall
  through to the real-bid/ask logic.

### 3. Surface fillable bid/ask in the dashboard UI

**Why**: `compute_arb` already adds `fillable_ask_a`, `fillable_bid_a`,
`fillable_no_ask_a`, etc. to every row, but `docs/index.html` only
shows the midpoint (`implied_prob_a/b`). The midpoint matches what
the platforms display when you click in, but the ACTUAL arb math
runs on the fillable prices — they should be visible.

**What to do**: in the row template, show midpoint as the headline
number (today's behavior) but expose fillable_ask + fillable_no_ask
in a small grey subtitle so users can verify the math: "Buy YES on
Kalshi @ 21¢ ask + Buy NO on Polymarket @ 92.9¢ ask = $1.139 cost".
For guaranteed-arb rows specifically, show the stake_note too (it's
already computed).

### 4. Pin `requirements.txt`

`pandas>=2.2,<3.0`, `numpy>=2.0,<3.0`, `rapidfuzz>=3.0`, `pyyaml>=6.0`.

### 5. Smoke test in CI

Run each scraper with a small limit, verify the CSV shape, run the
matcher on truncated data, check `arb_data.js` has rows. One file,
runs in <30s on every PR. This would have caught the keyset-broken
bug before it shipped — the test would have asserted `len(events) >
some-floor` and noticed only 100 events.

### 6. Polling-agg cross-repo sync mechanism

`scripts/elections.py` is a hand-port from polling-agg's
`arb_scanner.py`. It WILL drift. Options:
- a. Periodic manual diff (cheap, drifts silently).
- b. CI step that fails if specific function sources diverge.
- c. Publish polling-agg's election module as a pip package and
  depend on it. Cleanest, most setup.

Same situation for `utils/http_headers.py` and the `FEES` dict (both
duplicated between repos).

### 7. Dedup elections matches between fuzzy + ported paths

Today both pred-arb's fuzzy matcher AND the ported elections module
surface US-2026 races. Duplicates accepted on user's explicit call
(2026-06-21). Pick a winner: race_id-based rows from `elections.py`
are typically more accurate than fuzzy text matches, so prefer those
when a dedup pass goes in.

### 8. Refactor `scripts/matcher.py` (750 LOC, guard chain)

Extract each false-pair guard into a named helper:
`_guard_candidate_name`, `_guard_sub_bet_type`, `_guard_threshold`,
etc. Doesn't change behavior; just makes it possible to edit one
guard without re-reading 750 lines.

### 9. Verify Node 20→24 migration

GHA forced Node 24 around 2026-06-16. Workflows still use
`actions/checkout@v4` and `actions/setup-python@v5` (Node-20-built),
running under Node 24 forcibly. Bump to the Node-24-native versions
before the 2026-09-16 cutoff.

### 10. Log silently-swallowed JSON parse failures in `polymarket.py`

Two `except Exception:` blocks in `parse_market()` silently default to
`[]` for outcomes/prices. Add a `print(... file=sys.stderr)` so we
notice if gamma's field shape changes.

### 11. Investigate Polymarket scraper truncation

Even with offset capped at 2000, recent prod runs show ~6k markets
saved (events expand to markets — each event has multiple). Worth
spot-checking we're seeing all the events we expect. Item 1 (CLOB
rewrite) makes this moot.

---

## Bugs squashed today worth remembering

- **`bool(NaN)` is True**: see `[[handoff]]` "Python footguns"
  (polling-agg AUDIT). Anywhere we read an optional flag column from a
  pandas DataFrame, use `is True` not `bool(...)`.
- **CLOB book API doesn't sort bids/asks best-first.** Empirically
  bids come back roughly low-to-high. Sort explicitly with
  `max(bids)` / `min(asks)`. (Cost me an hour writing
  `freshen_polymarket.py`.)
- **Polymarket gamma's `active=true&closed=false` flags lie.** ~22%
  of "active" events have endDate in the past. Filter on the date
  itself.
- **Kalshi's `open_events` feed includes already-closed markets.**
  ~40% of rows have close_date in the past. Filter on date.
- **Polymarket gamma's bestBid/bestAsk lags the live CLOB by
  minutes-to-hours on low-volume markets.** Don't trust gamma prices
  for arb math; always re-fetch the live CLOB book.
- **Polymarket `/events/keyset` cursor doesn't advance.** Don't use it.
- **PredictIt rows with bid 2¢ / ask 99¢ get midpoint 50.5%.** Their
  scraper-time spread > 15pp filter catches most but not all. The
  arb scanner should use real bid/ask, not midpoint.
- **Platforms display different prices on their UIs.** Kalshi shows
  `last_price`; Polymarket shows bid/ask midpoint. The dashboard's
  display price (`implied_prob_a/b`) must be picked per-platform to
  match each one — unifying the rule breaks one or the other. See the
  Espaillat incident below. Anti-pattern: "I'll simplify by using
  midpoint everywhere" / "I'll simplify by using last_price
  everywhere" — both wrong.

---

## Done (in this audit pass)

Commit hash to be filled at push time.

- Created `utils/http_headers.py` (mirror of polling-agg's). `BROWSER_UA`,
  `DEFAULT_HEADERS`, `browser_xhr_headers(origin)`.
- Migrated four files to import from it: `scrapers/polymarket.py`,
  `scrapers/predictit.py`, `scripts/fetch_depth.py`,
  `scripts/scrutiny.py`. All now send the same real-browser UA.
  (Before this pass, those four still leaked the old
  `(research/pred-arb)` UA — a latent regression of the Kalshi WAF bug
  we'd just fixed.)
- Migrated `scrapers/kalshi.py` to call `browser_xhr_headers(
  "https://kalshi.com")` instead of inlining the full header set.
- Synced `FEES` dict to 2%/12%/2% (was 3%/12%/3%). Matches polling-agg.
- Added debounce on the dashboard search input in `docs/index.html`
  (150ms; numeric filters left as-is).

### 2026-06-21 — Elections tab + ported polling-agg election logic

- Copied `utils/races.py` (511-race registry) and
  `scrapers/house_incumbents.py` verbatim from polling-agg-2026.
- Created `scripts/elections.py` (~590 LOC). Three matching paths
  (general / general_candidate / primary_candidate). Hand-ported from
  polling-agg's `scripts/arb_scanner.py`. Reads pred-arb scraper CSVs
  via normalizing adapter loaders that derive race_id from titles
  (pred-arb scrapers don't pre-tag rows with race_id like polling-agg's
  do). Output rows match pred-arb's existing schema (`implied_prob_a/b`,
  `profitable_onesided`, `category='Elections'`, `category_bucket=
  'Elections'`).
- Wired into `run_all.py` between the matcher and arb scanner.
- Modified `scripts/arb_scanner.py` to:
  - Read `data/processed/election_pairs.csv` and concat its rows on
    top of fuzzy-matcher output. Duplicates accepted today (user
    decision).
  - Compute `category_bucket ∈ {Elections, Sports, Other}` on every
    row so the dashboard tabs can filter without re-running heuristics.
- Rebuilt `docs/index.html` dashboard:
  - Tab strip is now **All / 🗳 Elections / ⚽ Sports / Other** (was
    Markets / Sports). Each tab filters the same dataset by
    `row.category_bucket`.
  - Tab count badges populated from row buckets.
  - `bucketOf()` JS helper as a backwards-compat fallback for old
    arb_data.js snapshots that don't have `category_bucket` yet.
- Initial pair counts on the post-port smoke run: 589 total
  (was 402 before the port). Breakdown: Elections 402, Other 150,
  Sports 37. Election bucket: 174 fuzzy + 41 political + 187 ported
  (53 general + 20 general_candidate + 114 primary_candidate).
- HANDOFF.md updated: new "US-2026 elections" + "Categories and
  dashboard tabs" sections under Architecture; file map expanded to
  cover `utils/`, `house_incumbents.py`, `elections.py`; fee table
  refreshed (was still showing the stale 3% rates).
- README.md updated: four-tab description; pipeline step list expanded
  to cover the elections module + category tagging step.

### 2026-06-21 (later that day) — Liveness pass: only-tradeable rows

Pipeline-wide changes so the dashboard only ever shows pairs the user
could actually trade right now. Triggered by the user spotting a stale
gap on the "New Zealand recognizes Palestine before 2027" market
(Kalshi 14¢ vs Polymarket 34¢; live CLOB showed Polymarket was actually
~20¢, so the real gap was 6pp not 20pp).

Diagnosis: Polymarket's gamma `/markets` endpoint is what we scrape for
the bulk pass, and gamma caches a snapshot that lags the live CLOB by
minutes-to-hours on low-volume markets. Old scraper logic for wide-
spread gamma rows was "use the ask price to be conservative" — but
"conservative" was the wrong word for a BUYER (which is what arb math
needs); the stale-high ask makes the price look optimistic. NZ's
gamma was bid=0.16 / ask=0.34 (18pp wide); shipped as 0.34. Live
depth showed bid=0.16 / ask=0.24 (8pp).

Five layered fixes:

1. **`scrapers/kalshi.py`**: drop rows with `yes_bid <= 0` at the source
   (about 7,700 of 44,000 markets). One-sided books can be bought into
   but not exited.
2. **`scrapers/polymarket.py`**: tighten the gamma wide-spread filter
   from "spread > 10pp → use ask" to "spread > 8pp → drop the row".
   Wide-spread gamma is almost always stale; using its ask poisons the
   downstream arb math. Markets dropped here may resurface tomorrow
   once gamma refreshes.
3. **`scripts/arb_scanner.py`**: depth-time price override. When
   `fetch_depth` has a fresh live-CLOB midpoint for a market, override
   the scrape-time `implied_prob_a/b` with that midpoint and recompute
   arb math. Caught 34 prices on the smoke run; NZ specifically went
   from 14% vs 34% (fake 20pp) to 14% vs 20% (real 6pp guaranteed arb).
4. **`scripts/arb_scanner.py`**: drop pairs where either side's live
   orderbook has an ask but no bid (or bid ≤ 0). Same rationale as the
   Kalshi scrape-time drop, but rechecked at depth-fetch time — a
   market can go one-sided in the minutes between scrape and depth.
   Dropped 74 pairs on the smoke run.
5. **`scripts/arb_scanner.py`**: drop pairs with `settle_date` already
   in the past. Resolved markets sometimes linger in upstream feeds.
   Dropped 18 pairs on the smoke run.

Removed `one_sided_a/b` from the suspicion reason map and from
`docs/index.html`'s `reasonText` (those pairs are now dropped at fix
#4 instead of being flagged).

Pair counts: 562 → 495 after all five filters (with the existing
locally-cached scraper CSV; production numbers will differ once the
next cron re-scrapes).

HANDOFF.md updated: new section "Why we don't just hit the live
orderbook for every market" with actual measured numbers (6 req/sec
on the CLOB book endpoint, no rate limiting observed in a 50-request
probe — earlier "30+ minutes" claim was overstated); suspicion+
scrutiny section rewritten to cover the new filter steps in order.

**Open option worth considering:** switch the Polymarket scrape from
gamma to wide-coverage CLOB. Measured cost is ~10-15 min with modest
concurrency; payoff is deletion of the wide-spread drop + depth-time
override + most of the staleness-fighting code. Currently optimized
for workflow speed (gamma is 3 min vs CLOB's 10-15 min); the
staleness cost is the price.

### 2026-06-21 (evening) — Wide-coverage live CLOB + freshness guard

User reported pred-arb's daily refresh had been failing and asked for
"something to make sure that the pulls from polymarket and kalshi are
up to date." Three failure modes addressed:

1. **pyyaml missing from requirements.txt**. The elections port
   (this morning) added `scrapers/house_incumbents.py` which imports
   `yaml`, but the new dep was never added to `requirements.txt`. Both
   daily refreshes since the port failed at that step. One-line fix.

2. **Polymarket scraper silently truncating to ~2100 events**. The
   pagination loop did `except Exception: break` on the FIRST transient
   error. Polymarket's `/events` endpoint returns occasional HTTP 422s
   on specific offsets that succeed on retry. The "break" was causing
   each daily refresh to ship a small subset of the universe as if it
   were complete. Replaced with: 3 retries per page, skip persistently-
   failing pages, stop only after 3 consecutive empty/failed pages.

3. **Polymarket gamma snapshot staleness — the NZ-Palestine incident**.
   Gamma's `bestBid`/`bestAsk` cache lags the live CLOB by minutes-to-
   hours. NZ market shipped at 34¢ midpoint (gamma 16/34) when the
   live CLOB was 17/20 (real midpoint 18.5¢). Earlier bandaids tried
   to detect-and-drop wide gamma rows; that lost half the universe and
   still missed close cases. **New approach:** `scripts/
   freshen_polymarket.py` runs immediately after the gamma scrape and
   re-fetches the live `clob.polymarket.com/book` for EVERY market in
   parallel (16 worker threads, ~4-6 min), overwriting bid/ask/midpoint
   with live values. The matcher then runs on actually-live prices.
   Removed the 8pp scraper drop entirely.

   Also fixed a bug in the freshen logic where I assumed CLOB bids
   came back sorted best-first. Empirically they're unsorted (or
   reverse-sorted). Sort explicitly with max(bids) / min(asks).

4. **Freshness guard.** New `_assert_scrape_freshness()` at the top of
   `arb_scanner.py:run()`. Reads the `fetched_at` of every raw CSV and
   raises SystemExit if any is more than 12 hours old. Catches the
   silent-staleness mode where a scraper succeeds structurally but
   produces no new data. Live dashboard keeps the last good snapshot
   when this fires.

Files added: `scripts/freshen_polymarket.py` (~135 LOC).
Files modified: `requirements.txt`, `scrapers/polymarket.py`,
`scripts/arb_scanner.py`, `run_all.py`.

HANDOFF.md updated: new "Polymarket freshness" section explaining the
gamma-then-CLOB design; new "Freshness guard" section; file-map and
pipeline diagram updated.

### 2026-06-21 (later evening) — pagination switch + dead-prop filters

User asked to probe why the first manual run only pulled 6196
Polymarket markets vs the ~25k we'd expected. Two findings:

1. **Polymarket's `/events?offset=N` is capped at offset=2000.** Probed
   directly: any offset > 2000 returns HTTP 422 with body
   `"offset too large, use /events/keyset for deeper pagination"`.
   This is a server-side API change, not a regression. The old retry
   loop was masking it by stopping on the first error — and the new
   one (skip-and-continue) was hitting persistent 422s on every page
   past 2000.

   Fix: switched `fetch_all_events()` to `/events/keyset` with cursor
   pagination. Same retry/skip safety, no offset cap.

2. **Both platforms leave dead markets flagged as active.** Probed
   `active=true&closed=false` on Polymarket: 11 of 50 sampled events
   had `endDate` already in the past. Kalshi CSV from earlier today
   showed 17,863 of 44,269 markets with `close_date` already past.
   Their own status flags lie. The downstream past-settle-date filter
   in `arb_scanner.py` catches these eventually, but only after they
   bloat the matcher input.

   Fix: added scraper-level date filters.
   - `scrapers/kalshi.py`: skip rows where `close_date < today`
     before they even enter the dedup-then-emit loop.
   - `scrapers/polymarket.py`: drop rows where `end_date < today` in
     the post-scrape cleaning step (alongside the existing liquidity
     and wide-spread drops).

Files modified: `scrapers/kalshi.py`, `scrapers/polymarket.py`.

Expected impact: dramatic shrink in scraper output (Kalshi: ~44k → ~26k;
Polymarket: was capped at ~6k due to the bug, should jump back to ~20k
clean after the keyset switch). Matcher universe should be roughly
similar to pre-2026-06-21 — but every row now represents a market that
is genuinely still trading.

### 2026-06-21 (final pass) — real ASK / NO ask arb math + keyset revert

Three things tonight:

**A) Arb math now uses real fillable prices (ASK for YES leg, real NO
ASK for NO leg).** User noticed the "Will Trump recognize Somaliland?
Before 2027" pair shipping as 6.65% guaranteed when the live Kalshi
ask was 21¢ (not midpoint 18¢). Previously the math used:

```
cost = prob_a (midpoint) + (1 - prob_b (midpoint))
```

That's the cost to enter at MIDPOINT, which is never actually fillable.
The fix: `compute_arb()` takes new `bid_a/ask_a/bid_b/ask_b` and
`no_bid_a/no_ask_a/no_bid_b/no_ask_b` kwargs. Uses real ASK for the
buy-YES leg, real NO ASK for the buy-NO leg. Falls back to midpoint
only when bid/ask data is missing (PredictIt has no public orderbook;
synthetic ±5pp spread stopgap added).

After the fix, Somaliland correctly downgrades to a ~3.4% guaranteed
arb (Buy YES Polymarket 7.6¢ + Buy NO Kalshi 85¢ = 92.6¢, net 3.4%
after 4% combined fees). Real, just smaller than the midpoint-math
overstate.

User asked: "why are you guessing at No bids, why not just pulling
the No price?" Correct call. Polymarket exposes YES and NO as separate
CLOB tokens (`yes_token_id` / `no_token_id`). Wired the NO token
through `depth_targets.csv → fetch_depth.py → orderbook_depth.csv` so
the scanner reads real NO bid/ask instead of inferring `1 - YES_bid`.
Inference only matches reality on tight symmetric books; an
asymmetric-book test showed inferred returns 11% guaranteed where
real returns 6% — exactly the 5pp inflation pattern we'd been seeing.

Each row now also exposes `fillable_ask_a/b`, `fillable_bid_a/b`,
`fillable_no_ask_a/b`, `fillable_no_bid_a/b` so the dashboard can
show what you'd actually pay vs what the platform UI displays.

**B) Polymarket `/events/keyset` is broken.** This pass had a brief
detour where I tried to use it (the previous AUDIT entry's "switch to
keyset" change). Probed it directly: passing the returned `next_cursor`
back as `cursor` / `next_cursor` / `page_token` / `after` /
`pagination_cursor` all return the SAME 100 events forever. Production
run paginated 100,000 "events" that were 1,000 duplicates, yielded
46 matched pairs / 1 guaranteed arb. **Reverted to `/events?offset=N`
capped at 2000.** Universe goes back to ~200 matched pairs / handful
of guaranteed arbs — much better than the keyset disaster, slightly
less than the pre-cap days.

**C) Discovered the REAL fix for Polymarket coverage:
`clob.polymarket.com/markets`**. This is a separate endpoint that:
- Paginates properly (probed: 60 pages = 55k+ unique markets, real
  `next_cursor` base64-encoded offset that genuinely advances)
- Returns 1000 markets per page (10x gamma's 100)
- Includes `tokens[]` array with both YES and NO token_ids directly
  (no separate lookup needed; would also delete the no_token_id
  flow we just added)
- Has real `accepting_orders` boolean instead of gamma's lying
  `active=true&closed=false` flags

The field shape is completely different from gamma's nested
`{events: [{markets: [...]}]}`, so it needs `parse_market()` rewritten.
Not doing it inline tonight — left as the top item in the to-do list
below.

Files modified: `scripts/arb_scanner.py` (compute_arb + depth-join +
depth_targets emit), `scripts/fetch_depth.py` (NO orderbook fetch),
`scrapers/polymarket.py` (keyset revert).

### 2026-06-21 (late evening) — Display price per-platform rule (the Espaillat incident)

User reported the "NY-13 Adriano Espaillat" Polymarket side showing
**43%** on our dashboard while polymarket.com was showing **62%** for
the same market.

Diagnosis:
- Live Polymarket CLOB book at the time of check: bid 0.61 / ask 0.62
  / midpoint **0.615**. Polymarket's UI was showing 62% (rounded
  midpoint).
- `last_trade_price` on the same CLOB endpoint: **0.43** — a stale
  trade from hours earlier on a thinly-traded contract. The trade
  cleared at 43¢; nothing has traded since but the order book has
  moved up to a 61-62¢ spread.
- Our `scripts/freshen_polymarket.py` (introduced earlier today) was
  preferring `last_trade_price` over midpoint when populating
  Polymarket's `implied_prob`. We'd applied the Kalshi rule
  ("platforms show last_price") to Polymarket without verifying.

The hidden assumption: **platforms display the same price field**.
They don't. Kalshi's UI shows last-trade; Polymarket's UI tracks the
live order book and looks more like a midpoint. The Espaillat market
exposed this because last_trade and midpoint differed by 18pp.

Earlier today the **Somaliland incident** was the inverse problem on
the same axis. There the *display* was already correct (Kalshi 18¢
midpoint) but the *arb math* was wrong (using midpoint instead of
real ASK to compute basket cost). Both incidents trace back to the
same root cause: failing to keep DISPLAY price and FILLABLE price as
distinct concepts.

**The fix** (commit aadec8d): per-platform display rule, codified.
- `scrapers/kalshi.py` → `implied_prob = last_price` (matches kalshi.com)
- `scripts/freshen_polymarket.py` → `implied_prob = bid/ask midpoint` (matches polymarket.com)
- `scrapers/predictit.py` → `implied_prob = midpoint of bestBuy/bestSell` (matches predictit.org, unchanged)

The arb math (`compute_arb()` in `scripts/arb_scanner.py`) is unaffected
— it has always taken its prices from `fillable_ask_*` and
`fillable_no_ask_*`, never from `implied_prob_*`. Display and fillable
are now firmly two separate concepts.

**Anti-pattern checklist** (added to HANDOFF.md "Price semantics"
section):
- ❌ Use Polymarket's `last_trade_price` as the display number
- ❌ Use Kalshi's bid/ask midpoint as the display number
- ❌ Use the display price (`implied_prob_*`) in the arb math
- ❌ Infer Polymarket's NO ask as `1 - YES_bid` (it has a separate token)
- ❌ Trust a market's `active=true&closed=false` flag
- ❌ Trust the cursor that Polymarket's `/events/keyset` returns

Files modified: `scripts/freshen_polymarket.py` (revert to midpoint),
`scripts/arb_scanner.py` (compute_arb docstring expanded),
`scrapers/kalshi.py` (per-platform rule called out in comment),
`HANDOFF.md` ("Price semantics" section rewritten with full table +
anti-pattern list + both incidents documented),
`NOTES_FOR_REVIEWER.md` (section 2 rewritten).
