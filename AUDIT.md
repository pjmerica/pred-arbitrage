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
- **Two matching paths.** `match_political` joins on canonical
  `race_id`; `match_fuzzy` uses `rapidfuzz` text similarity within
  category groups (`CATEGORY_GROUPS` in `scripts/matcher.py`). Most of
  the file is the 10+ guard chain that prevents false fuzzy pairs.
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
| `matcher.py` | ~750 | **live, hot** | Two matching paths plus 10+ guards on the fuzzy path. Each guard tracks a specific historical false-pair (candidate-name, sub-bet type, year overlap, threshold buckets, office role, demographic, date anchors, rank, month/day anchor, subject extraction). **Don't loosen any guard without running `tools/deep_check.py` first.** |
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

## To-do (open)

Highest leverage first.

1. **Pin `requirements.txt`** to versions matching what the GHA runner
   ships: `pandas>=2.2,<3.0`, `numpy>=2.0,<3.0`, `rapidfuzz>=3.0`.
2. **Add a smoke test.** Run each scraper with a small limit, verify
   the CSV shape, run the matcher on truncated data, check
   `arb_data.js` has rows.
3. **Log silently-swallowed JSON parse failures in `polymarket.py`**.
4. **Cross-repo sanity check.** Either a CI step that diffs
   `polling-agg-2026/scripts/arb_scanner.py:FEES` vs ours, or move the
   shared constants into a published shared package. The headers helper
   AND `scripts/elections.py` are now in the same situation —
   `elections.py` is a hand-port of polling-agg's election logic, and
   it WILL drift if polling-agg changes its parsing regexes / arb math
   / fee model without someone re-syncing here. Three options:
     a. Periodic manual diff (cheap, drifts silently).
     b. CI step that fails if specific function source diffs.
     c. Publish polling-agg's election module as a pip package,
        pred-arb depends on it. Cleanest but most setup.
5. **Deduplicate elections matches.** Pred-arb's fuzzy matcher already
   surfaces some 2026 elections in its Politics output; `elections.py`
   adds more (often the same ones via the direct race_id path). User
   explicitly chose to accept duplicates today (2026-06-21) — flag a
   "preferred source" once the volume is observable. The
   race_id-based rows from `elections.py` are typically more accurate
   than fuzzy text matches; prefer them when dedup happens.
5. **Refactor `scripts/matcher.py` guard chain into named helpers.**
6. **Verify the Node 20→24 migration didn't break anything.** GHA
   forced Node 24 around 2026-06-16; pred-arb's workflow uses
   `actions/checkout@v4` and `actions/setup-python@v5` (Node-20-built).
   The annotation says they're auto-running on Node 24 now. Worth
   bumping to versions explicitly built for Node 24 before the
   2026-09-16 cutoff.

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
