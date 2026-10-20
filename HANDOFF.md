# Handoff — Pred Arbitrage

**Last updated:** 2026-10-15 (docs consolidated; the June-era handoff, AUDIT,
NOTES_FOR_REVIEWER and MATCHING_REVIEW are in [history/](history/))
**Live:** https://pjmerica.github.io/pred-arbitrage/ (GitHub Pages from `docs/` on `master`)

Pred-arbitrage scrapes every open market on Kalshi, Polymarket and PredictIt, pairs markets
that ask the same question, and prices a cross-platform basket (YES on one platform + NO on
the other) at the live order books. A basket that costs less than $1 after real fees is a
locked profit. Its election-only sibling is `polling-agg-2026`. The two repos share some files
(below) but never import from each other.

Other docs:
- [CHANGELOG.md](CHANGELOG.md): every hand-written change and why.
- [SCRAPER_NOTES.md](SCRAPER_NOTES.md): platform API quirks.
- [tools/README.md](tools/README.md): the CI checks.
- [history/](history/): how the project got here, including the incident write-ups.

---

## 1. Start here

**Work from the GitHub issue "Board health: needs attention"** (`tools/health.py`, the last
CI step). It opens itself when something needs a human, which notifies the owner. It updates
itself while the problem persists and closes itself when it clears. No open issue means nothing
to do. It lists:
- unreviewed market families with live pairs (`data/processed/series_review.csv`);
- series-map approvals lapsing within 14 days;
- links the link check had to repair;
- stale data (full scrape > 14 h old, or no re-price for > 4 h);
- held-back baskets: unverified, with a positive return.

Every CI run's job summary also shows the live re-check of each guaranteed basket
(`tools/livecheck.py`) and the link check.

### CI

| Workflow | Schedule (UTC) | What it does |
|---|---|---|
| `refresh.yml` "Daily refresh" | 00:07, 12:07 | tests → `run_all.py` (scrape everything, re-match, price) → link check/repair → dashboard smoke test → live re-check → commit → health issue. ~10-15 min. |
| `reprice.yml` "Re-price (hourly)" | :17 every hour | watchdog (dispatches `refresh.yml` if the last full scrape is > 14 h old) → tests → `run_all.py --reprice` (re-fetch only the matched pairs' books + PredictIt, ~1,200 requests) → same checks → commit → health issue. ~6-8 min. |
| `llm-validate.yml` | manual | Scores `tools/llm_review.py` against the hand-reviewed families. Unused: the owner prefers deterministic rules to an API key. |

The workflows share the `pred-arb-data` concurrency group, so they never overlap. The link check
and live re-check have `continue-on-error` and can't block a publish; failing tests or a failing
smoke test do block it, and the live page then keeps its last good data.

**GitHub's cron is best-effort and fires late or not at all.** In October 2026 the hourly
re-price actually ran about 4 times a day (the same count as when it was every 2 h). Full
refreshes start up to ~6 h late, and the watchdog only runs when a re-price does. Health only
runs inside a CI run, so it can't report a gap while nothing is running.

### Running locally

```bash
py -m pytest tests -q          # regression suite (one case per past fake-arb class)
py run_all.py                  # full refresh (~15 min)
py run_all.py --reprice        # re-price the last full run's pairs (~6 min)
node tools/dash_smoke.js docs tools/dash_exercise.js
```

Python is `py` on the owner's machine. Before committing code, revert the generated files
(`docs/arb_data.js`, `data/processed/*`, `data/raw/*`, `data/reprice/*`); CI regenerates them.

**Commits:** plain messages, **no Claude co-author line**. Every hand-written commit gets a
CHANGELOG entry: write `[pending]`, commit, then fill in the hash in a follow-up
`changelog: log own commit hash for <hash>` commit. If a rebase changes the hash, correct it.

---

## 2. The rules (each one exists because of a real incident)

- **Only structured match types can be `guaranteed`** (`VERIFIED_MATCH_TYPES` in
  `scripts/arb_scanner.py`):
  - threshold;
  - tournament-tennis, tournament-worldcup, tournament-league, tournament-season;
  - game;
  - primary-nominee;
  - general.

  A title-similarity (`fuzzy`) pair can be guaranteed only when its market family is
  **approved in the series map** or **template-matched** (below). Anything else shows as
  `unverified`.
- **Every fuzzy pair must pass `utils/proposition.incompatibility()`.** It is the outcome
  gate: placement (win / 2nd / top-N / finalist / qualify / playoffs), period, division,
  fixture teams and date, day vs window, "or above" vs "exactly", exact score, mention counts,
  subject, full person name and same-city club initials. **A new fake class gets a new field
  here** (plus a regression test), not a one-off guard. The scanner re-runs the gate on every
  pass, so a fix applies at the next re-price.
- **Election party legs go through `utils/election_shapes.py`** (identical file in
  polling-agg). It is an allowlist; Polymarket lists margin buckets, "within 5%",
  "closest race" and governor+Senate combos under every race.
- **The scanner downgrades guaranteed → unverified** when:
  - one leg is effectively settled;
  - the rules texts look different (`criteria_warn`);
  - the resolution windows don't nest (`window_mismatch`);
  - a crypto basket buys YES on Kalshi (`basis_risk_direction`: CF trimmed mean vs Binance wick);
  - the return is above 25% (15% until 2026-10-19);
  - a game leg kicked off in the last 8 h (`in_play`, from Polymarket's `gameStartTime`);
  - it is an election basket settling 2026-11-03 .. 2027-01-31 (`ELECTION_SETTLING`).

  The action text names the reason.
- **Window containment** (`utils/rules_window.py`): the YES leg's resolution window must
  contain the NO leg's. "Postponed after <date>" counts as a window end, and Kalshi's close
  date stands in for a missing end on tournament / primary / template pairs. A family's
  `safe_yes` is enforced here.
- **Stakes are proportional to price** (equal contracts on both legs). Return and profit are
  per $ staked. Never size by inverse odds: that split was backwards and lost money in the
  likely outcome.
- **Fees are real per leg** (`utils/fees.py`, identical in polling-agg):
  - Kalshi: 0.07 × series multiplier × P(1−P).
  - Polymarket: market fee rate × p(1−p).
  - PredictIt: 10% of profit + 5% withdrawal.
  - Plus a 0.5¢ margin per basket.

  Guaranteed needs ≥ 0.25% return. The flat `FEES` dict only feeds the display "net gap".
- **Links** are built only in `utils/links.py`:
  - Kalshi: `/markets/{series}/{event}`, lowercase, series = event-ticker prefix.
  - Polymarket: `/event/{event}/{market}`.

  CI re-derives every link from the API by the priced ID and repairs it (`tools/linkcheck.py --fix`).
- **Don't:**
  - feed non-Yes/No Polymarket markets (team moneylines) into fuzzy matching: their two tokens
    are two teams, so a "basket" is a double bet;
  - remove the `[flipped]` quote swap in the scanner (the fake 85% Wyoming arb);
  - pair across category groups;
  - let scrutiny drop pairs on low rules-text similarity (identical questions score 5-13 across
    venues);
  - approve a family without reading **both rules texts in full**.

### Series map and templates

`data/series_map.json` holds hand-reviewed Kalshi-series ↔ Polymarket-event families (290 on
2026-10-15). Each entry has the following fields:
- `status`: approved / rejected;
- `note`: what both sides resolve on;
- `caveat`: ties, deadlines, extra time;
- optional `safe_yes`: the only hedged direction;
- optional `review_by`, plus `on_lapse: "reject"` to retire it on that date (otherwise it
  returns to the queue);
- optional `polymarket_market_kinds`.

Kalshi `*MENTION*` series are rejected by rule.

Unreviewed families with live pairs go to `data/processed/series_review.csv` and the health
issue. **Template match** (`utils/rules_template.py`) auto-reviews a family when both of its
rules texts (names, dates and numbers masked) match both texts of an approved reference. The
references are `tests/data/reviewed_families.json` plus the auto-grown `data/template_refs.json`.
Soccer BTTS / correct-score pairs are checked per pair from the standard period clause.
First-to-score always stays hand-reviewed, because Kalshi counts extra time.

---

## 3. Pipeline

`run_all.py` (full refresh):

```
scrapers/kalshi.py            -> data/raw/kalshi_markets.csv      (trade-api v2; aborts below 7,000 events)
scrapers/polymarket.py        -> data/raw/polymarket_markets.csv  (/events/keyset; aborts below 10,000 events)
scripts/freshen_polymarket.py    live CLOB bid/ask/mid over gamma's stale snapshot
scrapers/predictit.py         -> data/raw/predictit_markets.csv
scrapers/house_incumbents.py  -> data/processed/house_incumbents.json
scripts/matcher.py            -> data/processed/matched_pairs.csv
scripts/elections.py          -> data/processed/election_pairs.csv (US 2026 races, ported from polling-agg)
scripts/arb_scanner.py           pass 1: writes depth_targets.csv
scripts/fetch_depth.py        -> data/raw/orderbook_depth.csv     (YES and NO books of matched markets)
scripts/arb_scanner.py           pass 2: docs/arb_data.js
scripts/trim_reprice_inputs.py-> data/reprice/*.csv               (matched rows only, for --reprice)
```

`--reprice` restores `data/reprice/*.csv` into `data/raw/` and runs only PredictIt, the two
scanner passes and `fetch_depth`. The scanner's freshness guard refuses to publish if a raw
CSV is older than 12 h (26 h for the Kalshi/Polymarket inputs in re-price mode).

The full Kalshi/Polymarket scrapes are **not committed**. CI commits only an explicit file
list: `docs/arb_data.js`, `data/reprice/*`, `predictit_markets.csv`, `orderbook_depth.csv`,
`matched_pairs.csv`, `election_pairs.csv`, `series_review.csv`, `house_incumbents.json`,
`series_map.json` and `template_refs.json`. On a rebase conflict the full refresh keeps its
own generated files, but master's `series_map.json` (the human edit).

### Matchers (`scripts/matcher.py`)

| Function | match_type | Keys on |
|---|---|---|
| `match_threshold_pairs` | threshold | asset, direction, exact strike, month/day, touch vs level (crypto, commodities) |
| `match_tournament_winner` | tournament-tennis / -worldcup | tournament, gender, player / country |
| `match_league_futures` | tournament-league | league, season, team (`_LEAGUE_TEAMS`), cross-checked with Kalshi's label |
| `match_season_totals` | tournament-season | team, season, win-total / points line |
| `match_games` | game | league, date, teams; moneyline, spread, total, halves, team totals; tennis, UFC |
| `match_primary_nominee` | primary-nominee | race_id, party, person |
| `match_political` / `elections.py` | political / general / general_candidate | race_id + party side via election_shapes |
| `match_soccer_fixtures` | fuzzy (so family/template checks apply) | date, teams (base slug), market kind; one Polymarket market per pair (clash guard) |
| `match_subject_events` | fuzzy | Kalshi subject-list events ↔ Polymarket by shared names, wording rule, edition year, event date ±45 d |
| `match_fuzzy` | fuzzy | rapidfuzz `token_sort_ratio` within category groups, per-category thresholds, then the gate |

### Prices: display vs fillable (don't mix them)

- `implied_prob_*` is the **display** price, matching each platform's own UI:
  - Kalshi: last price;
  - Polymarket: bid/ask midpoint, never `last_trade_price` (the Espaillat incident);
  - PredictIt: midpoint.

  The dashboard no longer shows these (its price cells are live asks); they only feed the
  gap columns. Where a display price sits outside the live bid/ask (Kalshi's last trade can be
  days old), `compute_arb` uses the book midpoint for the gap instead.
- `fillable_ask_*` / `fillable_no_ask_*` are the **live order book** prices, and the basket
  math uses only these (the Somaliland incident: midpoint math overstated a basket by 3 pp).
- Polymarket NO is its own token with its own book: fetch it, don't infer `1 − YES bid`.
  Kalshi NO ask = 1 − best YES bid.
- `arb_type`:
  - `guaranteed`: basket < $1 after fees, verified;
  - `unverified`: the same math but held back;
  - `pre-fee`: < $1 only before fees;
  - `price-gap`: no basket;
  - `one-sided`: no real book on either direction.
- Don't trust `active` / `open` flags on either platform: filter on the dates. Polymarket
  bids/asks aren't sorted best-first.

---

## 4. Shared with polling-agg

`utils/fees.py`, `utils/election_shapes.py` and `tools/linkcheck.py` are identical in both
repos; change both together. `tools/health.py` is the same tool plus pred-arb-only parts.
`utils/races.py` and `scrapers/house_incumbents.py` were ported once and have since diverged.

---

## 5. When something breaks

1. Open the failing run in [Actions](https://github.com/pjmerica/pred-arbitrage/actions) and
   expand the failed step.
2. **Tests failed:** a code change reintroduced a fake class. Fix the code, never the test.
3. **Scraper aborted below its sanity floor:** a platform outage or a changed API. The board
   keeps its last data; check SCRAPER_NOTES.md.
4. **Freshness guard:** a raw CSV is stale (often a skipped full refresh). Dispatch
   `refresh.yml`.
5. **Smoke test failed:** the page would throw on the new data. Run `dash_smoke.js` locally.
6. **Push failed after 3 attempts:** concurrent commits. Re-run.
7. **A wrong pair on the board:** find which gate field should have caught it, add the field
   to `utils/proposition.py` (or reject the family in the series map), and add a regression
   test.

`tools/deep_check.py` is a manual diagnostic. It re-fetches full books for the top 50 rows and
grades them REAL / MARGINAL / THIN / GONE / WRONG_PAIR at 0/1/3 pp slippage.

---

## 6. Open items

- **Cron reliability:** see §1. Owner decided 2026-10-15 not to add an outside trigger
  (external cron service, local scheduled task or a sleeping runner); the board updates when
  GitHub's cron fires, about 4 times a day. Don't propose it again.
- **Repo history is ~800 MB** (old committed scrapes). Shrinking it needs a history rewrite
  and force-push (owner's call).
- **Lapsing approvals** (the health issue lists each 14 days ahead):
  - **Retire themselves** (`on_lapse: reject`):
    - KXMLSFTTS 10-18;
    - KXHOUSERACE and KXAKSENGOVCOMBO 11-02;
    - KXUEFANLFTTS 11-18;
    - KXLIGAMXFTTS 11-30;
    - KXUECLFTTS 12-20;
    - KXUCLFTTS and KXUELFTTS 2027-01-28;
    - KXAFCCLFTTS 2027-02-20;
    - KXPRESPERSON 2028-11-06.
  - **Return to the review queue** 11-30: KXUEFANLBTTS, KXUEFANL1HBTTS, KXUEFANLSCORE and
    KXCONCACAFNLBTTS.
- **PredictIt** has no public order book, and its API can be ~5 min stale.
