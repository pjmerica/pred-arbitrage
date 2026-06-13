# Handoff — Pred Arbitrage

**Last updated:** 2026-06-13
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
dashboard has two tabs:

| Tab | What it shows |
|---|---|
| **Markets** | Every matched arb pair across politics, entertainment, finance, etc. The usual filters (type, platform pair, gap range, volume, profitable-only, settle-window, search, suspicious-hide). |
| **⚽ Sports** | Every sports pair, unfiltered. Designed for browsing the cross-listed sports universe regardless of whether there's a real arb. |

---

## Architecture

### Pipeline (top to bottom — `run_all.py`)

```
scrapers/kalshi.py        → data/raw/kalshi_markets.csv
scrapers/polymarket.py    → data/raw/polymarket_markets.csv
scrapers/predictit.py     → data/raw/predictit_markets.csv
scripts/matcher.py        → data/processed/matched_pairs.csv
scripts/arb_scanner.py    → docs/arb_data.js + data/processed/depth_targets.csv
                            (also runs scrutiny.py for >30pp pairs)
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

### Suspicion + scrutiny pipeline

Same as polling-agg's. See that HANDOFF for full detail. Quick recap:

1. Scraper-time filters drop wide-spread / low-liquidity markets at the
   source.
2. Cross-flip safety on 3-way races.
3. Depth-derived spread filter after fetch_depth.
4. Rules-text scrutiny for >30pp pairs (`scripts/scrutiny.py`), with
   `data/processed/excluded_pairs.json` for hand-curated criteria
   mismatches. The Iran nuclear deal is the only entry today.
5. Per-pair `suspicion_reasons` array surfaces WHY a pair is flagged.

### Fees (round-trip)

`scripts/arb_scanner.py` top:

| Platform | Fee |
|---|---|
| Kalshi | 3% |
| Polymarket | 3% |
| PredictIt | 12% |

Conservative. Real fills include slippage.

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

scripts/
  matcher.py           Reads all 3 markets CSVs, produces matched_pairs.csv.
                       Two paths: race_id-based political, fuzzy text for everything else.
                       Long chain of guards prevents false positives (see Matching above).
  arb_scanner.py       Reads matched_pairs.csv, computes raw_gap_pp / net_gap_pp /
                       arb_type / suspicion_reasons / scrutiny.py results.
                       Produces docs/arb_data.js. Run twice around fetch_depth.py.
  fetch_depth.py       Reads depth_targets.csv, fetches Kalshi/Polymarket orderbook
                       ladders, writes orderbook_depth.csv.
  scrutiny.py          Fetches resolution rules + similarity scoring. Caches in
                       data/processed/scrutiny_cache.json (gitignored).

data/
  raw/                 Scraper outputs (gitignored).
  processed/           All gitignored EXCEPT:
    excluded_pairs.json    Tracked. Manual scrutiny excludes.

docs/                  GitHub Pages site. Tracked.
  index.html           Two-tab dashboard (Markets, Sports).
  arb_data.js          Arb pairs feed.
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

Two diagnostic scripts in the user's local checkout (not in this repo):

- `c:/Users/pjmer/Documents/audit_arbs.py` — top-K live verification
  against orderbooks. Refetches live bid/ask and recomputes basket
  cost. Shows whether a "guaranteed" arb actually survives current
  prices.
- `c:/Users/pjmer/Documents/deep_check_predarb.py` — multi-slippage
  walk through the top 50 pairs. Shows max-fill-size at 0pp / 1pp /
  3pp slippage so you can see if the apparent arb has any real depth.
- `c:/Users/pjmer/Documents/verify_arbs.py` — same shape but newer,
  includes the scrutiny-style criteria check.

Run these when investigating a suspect pair. They live outside the
repo because they're for the human, not the pipeline.

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
