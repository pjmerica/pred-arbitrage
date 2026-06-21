# Handoff — Pred Arbitrage

**Last updated:** 2026-06-21
**Status:** Live dashboard at https://pjmerica.github.io/pred-arbitrage/.
GitHub Actions runs the full pipeline twice daily (12:30 + 00:30 UTC) and
pushes refreshed `docs/arb_data.js` back to master.

This repo is the generalized sibling of `polling-agg-2026`. Where
polling-agg focuses on US elections only and pulls polling alongside
markets, pred-arbitrage scrapes **every active market** on Kalshi /
Polymarket / PredictIt and runs cross-platform arb matching across the
whole universe — sports, entertainment, crypto, politics, weather, etc.

If you're picking this up cold, read this top-to-bottom once. Most of
the architectural decisions here mirror polling-agg; the "Gotchas"
section captures the differences and what's specific to this side.

---

## What this project does

Pulls every active market on three prediction-market platforms, matches
similar markets across platforms by fuzzy text similarity, computes
cross-platform price gaps (potential arbs), and ranks them. The
dashboard has four tabs (added 2026-06-21):

| Tab | What it shows |
|---|---|
| **All** | Every matched arb pair across all categories. Default tab. Standard filters (type, platform pair, gap range, volume, profitable-only, settle-window, search, suspicious-hide). |
| **🗳 Elections** | US 2026 federal races (Senate / Governor / House). Includes the fuzzy matcher's Politics output AND the ported polling-agg election scanner output (`scripts/elections.py`). |
| **⚽ Sports** | Every sports pair, unfiltered. Designed for browsing the cross-listed sports universe regardless of whether there's a real arb. |
| **Other** | Everything else (entertainment, crypto, weather, etc). |

Tab filtering uses `row.category_bucket` set in `scripts/arb_scanner.py`.

---

## Architecture

### Pipeline (top to bottom — `run_all.py`)

```
scrapers/kalshi.py        → data/raw/kalshi_markets.csv
scrapers/polymarket.py    → data/raw/polymarket_markets.csv
scripts/freshen_polymarket.py
                          → overwrites polymarket_markets.csv with live
                             CLOB bid/ask/midpoint (added 2026-06-21)
scrapers/predictit.py     → data/raw/predictit_markets.csv
scripts/matcher.py        → data/processed/matched_pairs.csv
scripts/elections.py      → data/processed/election_pairs.csv  (added 2026-06-21)
scripts/arb_scanner.py    → docs/arb_data.js + data/processed/depth_targets.csv
                            (joins matched_pairs + election_pairs;
                             also runs scrutiny.py for >30pp pairs;
                             tags every row with category_bucket)
scripts/fetch_depth.py    → data/raw/orderbook_depth.csv
scripts/arb_scanner.py    → docs/arb_data.js (re-run, joins depth)
```

The double-run of `arb_scanner.py` around `fetch_depth.py` is
intentional. See polling-agg's HANDOFF for why.

### Matching

The matcher (`scripts/matcher.py`) has two parallel paths:

**`match_political`** — race_id-based, like polling-agg's general-election
path. Picks the highest-OI market per race+platform and crosses them.
Includes the cross-flip safety check for 3-way races (the
`safe_rep`-style logic) but implemented inside the matcher rather than
the scanner.

**`match_fuzzy`** — uses `rapidfuzz` text similarity within
category groups. The category groups (`CATEGORY_GROUPS` dict in
matcher.py) restrict matching to within sports / politics / crypto /
finance / etc. so a "Lakers win NBA" market doesn't match a "Trump
wins election" market just because their fuzzy scores are nonzero.

Both paths pass through a long chain of guards before emitting a
pair. In order:

1. Year range overlap (don't match a 2026 race to a 2028 race).
2. Contract-type matching (party_winner ↔ party_winner; candidate ↔
   candidate). No primary ↔ party_winner mismatches.
3. Last-name match for primary/candidate questions (Tom Brady ≠ JD Vance
   even when both classify as "2028 GOP primary").
4. Numeric-bucket mismatch detection ("57° or below" vs "67° or below").
5. Threshold-bucket detection (different `or below` / `above X`
   numbers).
6. Office-role mismatch (Presidential nominee vs Vice-Presidential
   nominee).
7. Demographic-vs-candidate check (don't pair "Will the nominee be a
   woman?" with "Will Andrew Yang be the nominee?").
8. Sports / event sub-bet type check (Tie vs Over/Under vs spread vs
   player-prop are all different bets even with the same team names).
9. Date / month-day anchor check (catches weekly buckets like "Raw 2026
   - April 13" vs "Raw 2026 - April 20").
10. Subject extraction — handles both "Will <Name>..." (Polymarket) and
    trailing "— <Name>" (Kalshi) phrasings; different subjects skipped.

Each of these guards came from a specific false-pair bug.

### US-2026 elections (ported from polling-agg-2026)

`scripts/elections.py` is a parallel pair builder targeting 2026 US
federal races (Senate / Governor / House). Ported from
[polling-agg-2026](https://github.com/pjmerica/polling-agg-2026)'s
`scripts/arb_scanner.py` on 2026-06-21. **Runs alongside the fuzzy
matcher, doesn't replace it.** The scanner concatenates election rows
on top of fuzzy rows in `docs/arb_data.js`. Duplicates with the
fuzzy Politics path are acceptable today (user decision); future work
can deduplicate.

Three match paths emit rows with distinct `match_type` tags:

- **`general`** — party-level Dem-yes vs Rep-yes markets joined on
  canonical race_id (e.g. `2026-SEN-OH`). Only path that produces
  `arb_type: guaranteed` rows, because it's the only one where both
  legs of the basket exist as separately tradeable contracts.
- **`general_candidate`** — per-candidate general markets ("Will Dan
  Sullivan win the 2026 Alaska Senate race?") cross-matched on
  `(state, office, district, candidate_last, candidate_first)`. Always
  `arb_type: one-sided` (no candidate-no contract exists in this
  template to build a guaranteed basket from).
- **`primary_candidate`** — per-candidate primary markets ("Will Zach
  Wahls be the Democratic nominee for Senate in Iowa?"). Same join
  shape as general_candidate but with party in the key (a Dem and Rep
  primary for the same office can have candidates with the same
  surname).

The race registry (`utils/races.py`) and the house-incumbents scraper
(`scrapers/house_incumbents.py`) were copied verbatim from polling-agg
to feed canonical race metadata. Pred-arb's own scrapers don't tag
markets with race_id, so `elections.py` derives them from titles using
`_extract_state_office()` + `_race_id_from()`.

**Do not** add a runtime import dependency on polling-agg. The two
repos stay independent (separate crons, separate live URLs). The cost
is drift — see AUDIT.md for cross-repo to-do.

**Inferred-complement is NOT used.** Polling-agg ripped it out on
2026-06-18 (a Dem price filled from `1 - Rep` isn't a tradeable yes
ask); the port carries the inner-join behavior forward.

### Categories and dashboard tabs

`scripts/arb_scanner.py` tags every row with
`category_bucket ∈ {Elections, Sports, Other}` based on the source row's
`category` field. The dashboard has four tabs filtering by this field:

- **All** — every row, default tab.
- **🗳 Elections** — `category_bucket == 'Elections'`. Includes both
  the fuzzy matcher's Politics output AND the ported election rows.
- **⚽ Sports** — `category_bucket == 'Sports'`. Browse mode — ignores
  filter bar so every sports pair is visible regardless of arb size.
- **Other** — everything else.

Bucket assignment lives in one helper: `_bucket()` in
`scripts/arb_scanner.py` and `bucketOf()` in `docs/index.html` (the
latter is a backwards-compat fallback for snapshots without the
`category_bucket` field). Keep them in sync.

### Suspicion + scrutiny pipeline

Same as polling-agg's. See that HANDOFF for full detail. Quick recap:

1. Scraper-time filters drop wide-spread / low-liquidity markets at the
   source.
   - Polymarket: liquidity < $200 OR spread > 30pp gets dropped at
     scrape time. The earlier 8pp-spread-drop was reverted on
     2026-06-21 — once `freshen_polymarket.py` rewrites every
     gamma snapshot with a live CLOB midpoint right after the scrape,
     the 8pp filter became unnecessary defensive code that was
     cutting too much.
   - Polymarket: end_date < today gets dropped (Polymarket leaves
     resolved markets flagged active=true; see "Active-flag lies").
   - Kalshi: markets with `yes_bid` missing or 0 are dropped entirely
     ("only buyers, no sellers" = can't exit a position).
   - Kalshi: close_date < today gets dropped (same active-flag-lies
     story).
2. Cross-flip safety on 3-way races.
3. **Depth-time price override** (2026-06-21). When `fetch_depth` has a
   fresh live-CLOB midpoint for a market, the scanner overrides the
   scrape-time `implied_prob_a/b` with that midpoint and recomputes the
   arb math. Catches gamma-stale prices that escaped the scraper-time
   wide-spread filter (e.g. spread was tight at scrape time but the
   "tight" price was itself stale). Logged as
   `Overrode N prices with live depth midpoints`.
4. Depth-derived spread filter after fetch_depth (drops pairs with >25pp
   spread on either side at depth-fetch time).
5. **One-sided orderbook drop** (2026-06-21). Drops any pair where
   either side's live orderbook has an ask but no bid (or bid ≤ 0).
   Same logic as the Kalshi scrape-time filter, but rechecked at depth
   time — a market can go one-sided in the minutes between scrape and
   depth-fetch. User-requested as "only live props" (you can't exit a
   position you can't sell into).
6. **Past-settle-date drop** (2026-06-21). Drops any pair whose
   `settle_date` is before today. Kalshi event-of-the-week and
   Polymarket weekly charts sometimes stay in the "active" feed for a
   few days after they resolve.
7. Rules-text scrutiny for >30pp pairs (`scripts/scrutiny.py`), with
   `data/processed/excluded_pairs.json` for hand-curated criteria
   mismatches. The Iran nuclear deal is the only entry today.
8. Per-pair `suspicion_reasons` array surfaces WHY a pair is flagged.
   Codes: `wide_gap`, `wide_spread_a/b`, `thin_depth_a/b`,
   `criteria_warn`. `one_sided_a/b` was removed as a suspicion code in
   2026-06-21 because those pairs are now dropped at filter step 5
   instead of just being flagged.

### Polymarket pagination — current state and gotchas

**TL;DR**: we use `gamma-api.polymarket.com/events?offset=N` capped at
2000. The "real" fix lives at `clob.polymarket.com/markets` (paginates
properly to ~55k markets) but uses a different field shape and needs a
parser rewrite — tracked in AUDIT.md.

History (chronological, painful — keep this so the next person doesn't
re-tread it):

1. **`/events?offset=N`** — what we used originally. Polymarket added a
   hard cap at offset=2000 in mid-June 2026; anything past returns HTTP
   422 with body
   `"offset too large, use /events/keyset for deeper pagination"`.

2. **`/events/keyset?cursor=...`** — what the 422 message tells you to
   use. **Don't.** Probed 2026-06-21 evening: the `next_cursor`
   returned does NOT advance the pagination. Pass it back with any
   parameter name (`cursor`, `next_cursor`, `page_token`, `after`,
   `pagination_cursor`) and you get the EXACT SAME 100 events forever.
   A run with this endpoint paginated 100,000 "events" that were
   actually 1,000 duplicates of the first page, yielded only 46
   matched pairs / 1 guaranteed arb. Likely a Polymarket-side bug or
   undocumented parameter we haven't found.

3. **Current**: reverted to `/events?offset=N` capped at 2000. We lose
   visibility into markets past offset 2000, but at least we have a
   working pipeline producing 200+ pairs. `fetch_all_events()` in
   `scrapers/polymarket.py` carries a long docstring with this
   history; don't rip it out.

4. **The real fix (tracked in AUDIT.md as next-handoff work)**:
   `clob.polymarket.com/markets` returns 1000 markets per page with a
   working `next_cursor` (base64-encoded offset that actually advances).
   Probed it: walked 60 pages = 55,343 unique markets. Has a
   `tokens` array containing both YES and NO `token_id` directly (no
   separate lookup needed), plus a real `accepting_orders` boolean
   instead of gamma's lies. Field names are completely different —
   needs `parse_market()` rewritten.

### NO-token orderbook for Polymarket

For matched pairs, `scripts/fetch_depth.py` pulls BOTH the YES and NO
orderbooks. Polymarket exposes YES and NO as separately tradeable
tokens with their own bid/ask, so the arb math should use the real NO
ask for the buy-NO leg rather than inferring `1 - YES_bid`.

How it flows:
1. `arb_scanner.py` (pass 1) looks up `no_token_id` from
   `polymarket_markets.csv` for every Polymarket leg of every matched
   pair, writes it into `depth_targets.csv` as `no_market_id`.
2. `fetch_depth.py` sees `no_market_id` on a target row, fetches the
   NO orderbook in addition to the YES one. Writes the NO row to
   `orderbook_depth.csv` with `side="no"` and `yes_market_id=<the
   original YES token>` so the scanner can join them back together.
3. `arb_scanner.py` (pass 2) splits `orderbook_depth.csv` into yes and
   no subsets, joins both onto each pair. New columns:
   `depth_no_a_best_bid`, `depth_no_a_best_ask`, etc.
4. `compute_arb()` uses the real NO ask for the buy-NO leg when
   available; falls back to `1 - YES_bid` only when NO data isn't
   present (Kalshi has no separate NO token; Polymarket markets that
   404'd on the NO fetch).

Kalshi doesn't expose a separate NO token, so `no_market_id` stays
empty for those rows and the math always falls back to the inferred
form (which is fine — Kalshi's YES book is tight enough that the
inference is exact).

### Arb math: real fillable prices, not midpoints

`compute_arb()` in `scripts/arb_scanner.py` takes `bid_a/ask_a/bid_b/
ask_b` (YES-leg orderbook) and `no_bid_a/no_ask_a/no_bid_b/no_ask_b`
(NO-leg orderbook) kwargs. The two basket directions tried:

- **Direction 1**: Buy YES on A + Buy NO on B → `cost = ask_a + no_ask_b`
- **Direction 2**: Buy YES on B + Buy NO on A → `cost = ask_b + no_ask_a`

If either cost < 1 after fees, it's a guaranteed arb. Picks the
higher-net direction. Falls back to midpoint when bid/ask data is
missing (PredictIt, markets that 404'd on depth fetch).

Incident that triggered this rewrite (2026-06-21): the "Will Trump
recognize Somaliland?" pair shipped as a 6.65% guaranteed arb because
the math subtracted midpoints + fees. Real fillable basket (Buy YES
Polymarket 7.6¢ + Buy NO Kalshi 85¢ = 92.6¢) gives ~3.4% guaranteed
after fees — still real, just smaller. The midpoint version overstated
by ~3pp.

**Each row exposes `fillable_ask_a`, `fillable_bid_a`, `fillable_no_ask_a`,
`fillable_no_bid_a`, etc.** so the dashboard can show what you'd
actually pay vs what the platform UI displays. The `implied_prob_a/b`
fields still hold the midpoint (matches what the platforms show when
you click in).

### Active-flag lies

Both Polymarket and Kalshi leave already-resolved markets flagged
`active=true` / status=open in their public feeds. Probed Polymarket
2026-06-21: ~22% of "active" events had `endDate` in the past. Kalshi
sample: 17,863 of 44,269 markets had `close_date` in the past.

Don't trust the status flags. Filter on the date itself:

- `scrapers/kalshi.py` skips rows where `close_date < today` before
  emitting.
- `scrapers/polymarket.py` drops rows where `end_date < today` in the
  post-scrape cleaning step.

There's also a downstream `settle_date < today` filter in
`scripts/arb_scanner.py` for defense in depth (catches any market
that resolves between scrape and dashboard render).

### Polymarket freshness: gamma scrape + live-CLOB freshen

Polymarket has two endpoints we care about:

- `gamma-api.polymarket.com/events` — bulk metadata + cached snapshot
  prices. Fast (~3 min for ~25k events) but `bestBid`/`bestAsk` can
  lag the live CLOB by minutes-to-hours on low-volume markets.
- `clob.polymarket.com/book?token_id=...` — live orderbook. One request
  per market, no observed rate limit (measured ~6 req/sec single-
  threaded in a 50-request probe).

The pipeline does both: gamma scrape fills in metadata + token_ids
fast, then `scripts/freshen_polymarket.py` immediately re-fetches the
live book for every market in parallel (16 worker threads, ~4-6 min
total). The freshened bid/ask/midpoint overwrites the gamma values
before the matcher runs, so the matcher works on actually-live prices.

Earlier we tried bandaid fixes (drop scraper rows with wide gamma
spread, override matched-pair prices in the scanner after fetch_depth).
Those covered some cases but missed others — markets the matcher
never paired because the gamma price looked wrong never got a chance
to be re-priced. Pre-matching freshen is the cleaner fix; the bandaids
were ripped out on 2026-06-21.

Kalshi is different: its `/markets` summary already returns
near-realtime bid/ask in the same payload as market metadata, so a
separate live-fetch buys little.

PredictIt: there is no public orderbook endpoint. The single
`marketdata/all` summary is everything they expose. Whatever it
returns IS the freshest data available.

### Freshness guard

`scripts/arb_scanner.py:_assert_scrape_freshness()` runs at the top of
`run()` and refuses to overwrite `docs/arb_data.js` unless EVERY raw
CSV's `fetched_at` is within the last 12 hours. Catches the
silent-staleness failure mode where a scraper succeeds structurally
(no exit code) but the CSV it produced is from a previous run because
it didn't actually write new data. We had a real incident on
2026-06-21 where Polymarket's CSV was 44 days old while every daily
refresh reported success. The guard fails the scanner step, the
workflow's commit step is skipped, and the dashboard keeps the last
good snapshot.

### Fees (round-trip)

`scripts/arb_scanner.py` top:

| Platform | Fee |
|---|---|
| Kalshi | 2% |
| Polymarket | 2% |
| PredictIt | 12% |

Bumped from 3%/3%/12% in the 2026-06-20 audit to match polling-agg.
Real fills include slippage. The `FEES` dict is the single source of
truth; `scripts/elections.py` imports from there too — do not duplicate.

### Failure semantics

Each scraper raises `SystemExit` on empty API response (no usable rows).
That kills `run_all.py` → workflow's commit step is skipped → live
dashboard keeps the last good data.

HTTP layer retries 4 times on 403/408/429/5xx + network errors with
2/4/8s exponential backoff before raising. Transient blips don't fail
the run.

### GitHub Actions

`.github/workflows/refresh.yml`. Schedule: `30 12 * * *` and `30 0 * * *`
(offset 30 minutes from polling-agg so we don't hammer Polymarket /
Kalshi simultaneously). Same checkout → install → run_all.py →
commit-and-push-with-rebase-retry shape as polling-agg.

---

## File map

```
.github/workflows/refresh.yml  Daily refresh workflow.

run_all.py                     One-shot entrypoint. Calls each step in order.
requirements.txt               pandas, numpy, rapidfuzz.

scrapers/
  kalshi.py            Kalshi v2 trade-api. DO NOT REVERT to v1.
  polymarket.py        gamma-api.polymarket.com. Captures yes_token_id / no_token_id.
  predictit.py         predictit.org/api/marketdata/all/.
  house_incumbents.py  Ballotpedia + congress-legislators YAML, produces
                       data/processed/house_incumbents.json. Read by
                       utils/races.py at import. Ported from polling-agg
                       2026-06-21 for the elections path.

scripts/
  matcher.py           Reads all 3 markets CSVs, produces matched_pairs.csv.
                       Two paths: race_id-based political, fuzzy text for everything else.
                       Long chain of guards prevents false positives (see Matching above).
  freshen_polymarket.py
                       Refetches clob.polymarket.com/book for EVERY
                       Polymarket market in parallel (16 workers, ~4-6 min)
                       and overwrites bid/ask/midpoint with live values.
                       Replaces gamma's cached snapshot, which lags the
                       live CLOB by minutes-to-hours. Added 2026-06-21.
                       Sort bug discovered: CLOB doesn't return bids/asks
                       sorted best-first; sort explicitly with
                       max(bids) / min(asks).
  elections.py         2026 US-election arb builder ported from
                       polling-agg-2026. Three match paths (general,
                       general_candidate, primary_candidate). Writes
                       data/processed/election_pairs.csv. Added 2026-06-21.
  arb_scanner.py       Reads matched_pairs.csv + election_pairs.csv,
                       computes raw_gap_pp / net_gap_pp / arb_type /
                       suspicion_reasons / scrutiny.py results. Tags
                       every row with category_bucket for the dashboard
                       tabs. Produces docs/arb_data.js. Run twice
                       around fetch_depth.py.
                       _assert_scrape_freshness() at top of run() refuses
                       to overwrite arb_data.js if any raw CSV is
                       >12h stale.
                       compute_arb() uses real ASK / NO-ASK for the arb
                       math (see "Arb math" section above), not midpoints.
  fetch_depth.py       Reads depth_targets.csv, fetches Kalshi/Polymarket
                       orderbook ladders, writes orderbook_depth.csv.
                       For Polymarket rows with a no_market_id, fetches
                       BOTH the YES and NO books (added 2026-06-21).
  scrutiny.py          Fetches resolution rules + similarity scoring. Caches in
                       data/processed/scrutiny_cache.json (gitignored).

utils/
  http_headers.py      Shared real-browser HTTP headers / WAF-XHR set.
                       Imported by every scraper + fetch_depth + scrutiny.
                       Single source of truth — see AUDIT.md.
  races.py             Canonical 2026 US race registry (35 Senate, 36 Gov,
                       435 House). Exports RACE_BY_ID. Ported from
                       polling-agg-2026 for the elections path.

data/
  raw/                 Scraper outputs. .gitignore tracks these specifically:
    kalshi_markets.csv     Output of scrapers/kalshi.py.
    polymarket_markets.csv Output of scrapers/polymarket.py + freshen.
    predictit_markets.csv  Output of scrapers/predictit.py.
    orderbook_depth.csv    Output of scripts/fetch_depth.py (YES + NO rows).
                       Changed 2026-06-21: previously the entire data/raw
                       was gitignored, which meant production refreshes
                       wrote CSVs only to the runner's ephemeral disk —
                       a fresh checkout had nothing to work with. Now
                       the workflow's "git add data/" actually commits
                       them back.
  processed/           Mostly gitignored. Tracked:
    excluded_pairs.json    Manual scrutiny excludes.
    house_incumbents.json  Output of scrapers/house_incumbents.py.
    election_pairs.csv     Output of scripts/elections.py.

docs/                  GitHub Pages site. Tracked.
  index.html           Four-tab dashboard (All, Elections, Sports, Other).
                       Tab filtering uses row.category_bucket from the
                       scanner (with a SPORTS_CATS fallback for legacy
                       snapshots).
  arb_data.js          Arb pairs feed. Includes top-level `total`,
                       `guaranteed_count`, `fees`, `updated_at`.
```

---

## Common gotchas

Most of these are documented in polling-agg's HANDOFF; this section
flags the pred-arb-specific ones.

**Universe size**
- Polymarket alone has ~25k active markets. The full pipeline takes
  ~10–15 min per cron because Polymarket pagination is slow and
  fetch_depth pulls orderbooks for ~1k matched pairs.
- The matcher's fuzzy pass uses `rapidfuzz.process.cdist` for vectorized
  scoring. Doing pairwise comparisons naively would be too slow.

**Category filtering**
- The matcher only crosses markets within the same category group
  (`CATEGORY_GROUPS`). This is necessary or you'd get e.g. "Lakers vs
  Celtics" fuzzy-matching "Will Lake County declare bankruptcy" purely
  on text similarity.
- Pred-arb categorizes most matched pairs as "Sports" / "Elections" /
  "Politics" / "Entertainment" / etc. The Sports tab on the dashboard
  filters to sports categories.

**Subject extraction**
- The matcher's `extract_subject` handles two phrasings:
  1. Polymarket: "Will <Name> win the 2026 NL Manager of the Year?"
  2. Kalshi: "2026 NL Manager of the Year Winner? — <Name>"
- Different subjects with the same template are skipped. This catches
  e.g. "Will Brian Montgomery be the GOP nominee for GA-01?" vs
  "Will James Kingston be the GOP nominee for GA-01?" — same race,
  different candidates.

**Polymarket gamma's outcomes/outcomePrices fields are JSON-encoded
strings.** Always `json.loads` them.

**Polymarket cross-cycle Kalshi tickers.** `SENATEOH-28` (2028 cycle)
and `SENATEOHS-26` (2026 OH special) both got tagged with the same
race_id at one point. Fixed by extracting year from `event_ticker`.

**The 3-way race partition bug.** Same as polling-agg — cross-flipping
"Will Reps win" to compare against "Will Dems win" only works in a true
2-way race. Mitigated by `safe_rep` logic in the matcher.

**Resolution-criteria divergence.** Same as polling-agg.
`scripts/scrutiny.py` fetches rules text + scores similarity for any
>30pp pair. Iran is the only manual excluded pair.

**Volume filter is OR not AND.** The dashboard's min-volume filter
hides a pair only if BOTH sides are below the threshold. Was AND
originally, which hid all PredictIt-leg pairs because PredictIt has no
public volume field and the counterparty was often below $500.

---

## Do not

- **Add Claude as co-author on commits.** Plain commits only.
- **Revert Kalshi to v1.**
- **Loosen the matcher guards** without verifying with the deep audit
  scripts (see "When something looks fishy" below). Each guard exists
  because of a specific historical false-pair.
- **Pair markets across category groups.** Even if the text matches.

---

## When something looks fishy

The repo's primary diagnostic tool is `tools/deep_check.py` (see
`tools/README.md`). It walks the top 50 arb candidates, refetches live
orderbooks for both sides, and categorizes each as REAL / MARGINAL /
THIN / GONE / WRONG_PAIR / PREDICTIT_LEG / ONE_PLATFORM at multiple
slippage budgets.

```bash
python tools/deep_check.py
```

Two additional lighter-weight diagnostic scripts live in the
maintainer's local checkout outside this repo:
`audit_arbs.py` (top-K live verification across both repos) and
`verify_arbs.py` (newer shape with the scrutiny-style criteria check).
Not committed because they target both repos via local absolute paths.

---

## Recent work (post-2026-05-15)

In rough order:
- **Scraper resilience pass**: same as polling-agg — `_safe_read_csv`
  in matcher, fail-fast on empty, 4-attempt retry with backoff on
  403/408/429/5xx.
- **Matcher guards added incrementally**: candidate-name match,
  sub-bet type, date/month/day-anchor, office role,
  demographic-vs-candidate, threshold-bucket.
- **Polymarket / PredictIt broken-book filters** at scrape time.
- **Cross-flip safety** for 3-way races.
- **`scripts/scrutiny.py`** for resolution-criteria divergence on big
  gaps.
- **Sports tab** added — bypasses all the filters since users want to
  browse sports markets regardless of arb size.
- **Suspicion + verify badge UX** with multiple reason codes
  surfaced as hover tooltips.

---

## Open work / known issues

- **Node 20 deprecation** in GitHub Actions — bumping
  `actions/checkout` and `actions/setup-python` to their Node-24-
  compatible versions before June 2026 is the fix.
- **Sports markets are 11k+ on Polymarket alone**, only ~40 land in
  matched pairs. The matcher's category-group guards are conservative
  (correctly). Loosening would add false positives faster than real
  pairs.
- **PredictIt's API is sometimes 5 minutes stale.** Their "best ask"
  can lag the live market.

---

## When something breaks at 3am

1. Check **Actions** tab:
   https://github.com/pjmerica/pred-arbitrage/actions
2. Click the failing run, expand the failing step.
3. **HTTP 403 / network error**: usually transient. Re-trigger via
   "Run workflow."
4. **`pandas.errors.EmptyDataError`** or `KeyError: 'implied_prob'`:
   one of the CSVs is empty. Check which scraper. Most likely a real
   upstream outage — wait for the next cron.
5. **Merge-conflict markers in `docs/arb_data.js`**: pull master, re-run
   `scripts/arb_scanner.py` locally, push.
