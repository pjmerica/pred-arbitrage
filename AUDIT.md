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
