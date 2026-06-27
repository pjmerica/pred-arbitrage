# Notes for the Reviewer — pred-arbitrage

**Audience**: senior engineer doing a code-correctness + onboarding +
security review. Start here, then go to `HANDOFF.md` for architecture,
`AUDIT.md` for the prioritized to-do list, and `CHANGELOG.md` for the
running log of every hand-written commit (data-refresh commits
excluded).

**Status**: live at https://pjmerica.github.io/pred-arbitrage/. Daily
cron at 12:30 and 00:30 UTC. Most recent manual run as of writing:
[GitHub Actions](https://github.com/pjmerica/pred-arbitrage/actions).

This is a working personal project, not a production system. Owner is
the only user; no auth, no PII, no user-provided input flows through
the pipeline. Failure modes are bounded: at worst the live dashboard
shows a stale snapshot.

---

## What this project is, in one paragraph

Scrapes every active market on Kalshi, Polymarket, and PredictIt
(~30-50k markets total). Matches similar markets across platforms by
text similarity (with ~10 guards against false-pairs) and by canonical
US-2026 race_id for election markets. Computes cross-platform price
gaps and flags pairs that are guaranteed arbs (you can buy YES on one
platform and NO on another for a combined cost < $1 after fees).
Outputs `docs/arb_data.js`, which a single-page dashboard
(`docs/index.html`) renders into four filterable tabs.

---

## The minimum mental model

Three platforms, three scrapers, three matching paths (race_id-based
for elections, threshold-comparison for crypto/commodities, fuzzy text
for everything else), one scanner that computes arbs from the matched
pairs. The scanner runs **twice** around a depth-fetch step because
the first pass emits a list of "interesting" markets whose live
orderbooks the second pass joins back in.

```
                                                        ┌─→ docs/arb_data.js → dashboard
scrapers/* → matcher.py + elections.py → arb_scanner ─┤
                                                        └─→ depth_targets.csv → fetch_depth → arb_scanner (pass 2)
```

A "matching" change might touch `matcher.py` or `elections.py`. An
"arb math" change touches `arb_scanner.py:compute_arb`. A "what does
the dashboard show" change touches `docs/index.html`. Each of those
files has a docstring at the top with more context.

---

## What's likely to surprise you (read this!)

### 1. We have separate logic paths for US-2026 elections

The standard fuzzy matcher (`scripts/matcher.py`) tries to match
markets by text similarity across all categories. For US elections we
ALSO run a parallel, more-precise matcher (`scripts/elections.py`)
that uses canonical race_ids (like `2026-SEN-OH`) + per-candidate name
parsing. Both paths' outputs are concatenated in `arb_scanner.py`.
**Duplicates are accepted** (one race may match via both paths) — user
explicit decision 2026-06-21, to be deduplicated later.

`scripts/elections.py` was hand-ported from the sibling repo
`polling-agg-2026` and will drift if either side changes its election
logic. See `AUDIT.md` to-do #6 for the sync options.

### 2. There are TWO price layers — display vs fillable

Easy to misread the codebase here. Every row in `arb_data.js` has:

**Display prices** — what we show on the dashboard, picked per
platform to match the number that platform's own UI shows:
- `implied_prob_a/b` ← this is the display number
- **Kalshi** dashboard price = Kalshi's `last_price` (kalshi.com
  shows last-trade)
- **Polymarket** dashboard price = bid/ask midpoint (polymarket.com
  tracks the live order book, not last-trade)
- **PredictIt** dashboard price = midpoint of `bestBuyYes`/`bestSellYes`

**Fillable prices** — what you'd actually pay or receive to trade:
- `fillable_ask_a/b` — buy YES = pay the ask
- `fillable_bid_a/b` — sell YES = receive the bid
- `fillable_no_ask_a/b` — buy NO = pay the NO ask (Polymarket has a
  separate NO token; Kalshi infers from `1 - YES_bid`)
- `fillable_no_bid_a/b` — sell NO = receive the NO bid

**The dashboard SHOWS display prices.** **The arb math RUNS on
fillable prices.** `compute_arb()` reads `fillable_ask_*` and
`fillable_no_ask_*`, never touches `implied_prob_*`.

Critical: **the per-platform display rule is real, not a bug.**
Kalshi and Polymarket genuinely display different things on their UIs
— Kalshi shows last-trade, Polymarket shows midpoint. If you
"simplify" by unifying them, the dashboard's headline price stops
matching what users see when they click through to the platform. See
the **Espaillat incident** in `HANDOFF.md`'s "Price semantics"
section for what happens when you get this wrong.

Anti-patterns the code intentionally avoids:
- Using `last_trade_price` as the display number for Polymarket
- Using midpoint as the display number for Kalshi
- Using `implied_prob` in the arb math (it's display, not fillable)
- Inferring Polymarket NO ask as `1 - YES_bid` (Polymarket has a
  real NO token with its own book; fetch it)

See `HANDOFF.md` → "Price semantics — READ THIS BEFORE CHANGING ANY
PRICE FIELD" for the full anti-pattern checklist and incident log.

### 3. Polymarket pagination is currently capped at ~2000 events

Polymarket's `gamma-api/events?offset=N` returns HTTP 422 past
offset=2000. The replacement they suggest (`/events/keyset`) is
broken — the cursor doesn't actually advance (probed 2026-06-21:
returns the same 100 events forever). We reverted to offset and live
with the cap. The dashboard's coverage is ~200-300 matched pairs.

The real fix is to switch the scraper to `clob.polymarket.com/markets`
(real pagination, returns ~55k markets, has both YES + NO token_ids
natively). Tracked as `AUDIT.md` to-do #1. Not a blocker but the
highest-leverage open work.

### 4. Both platforms lie about "active" status

Polymarket leaves ~22% of `active=true&closed=false` events with
`endDate` in the past. Kalshi leaves ~40% of `open` markets with
`close_date` past. Both scrapers filter on the date itself rather
than trusting the status flag. There's also a defense-in-depth
filter in `arb_scanner.py` that drops pairs where `settle_date <
today`. See `HANDOFF.md` "Active-flag lies" section.

### 5. The freshness guard can fail-loud and the dashboard is fine with it

`scripts/arb_scanner.py:_assert_scrape_freshness()` checks every raw
CSV's `fetched_at` and refuses to write a new `arb_data.js` if any is
more than 12h old. When it fires, the workflow's commit step skips
and the live dashboard keeps its previous good snapshot. Look for
"FRESHNESS CHECK FAILED" in GitHub Actions logs.

### 6. We track raw scraper CSVs in the repo

`.gitignore` un-ignores the four scraper CSVs and the orderbook depth
CSV. This is unusual (raw data in git) but intentional: the cron
re-scrapes from scratch every run, and committing the CSVs back means
a fresh `git clone` has working data without needing to wait for the
next cron. Verified the CSVs stay under 20 MB each so this isn't a
repo-bloat issue today.

---

## Where to look for what

| You want to know... | Read this |
|---|---|
| What does each tab show? | `README.md` "What it shows" |
| Full architecture + every file | `HANDOFF.md` |
| What's broken / what's next | `AUDIT.md` "To-do" |
| Why we made a weird choice | `HANDOFF.md` corresponding section; some choices in `AUDIT.md` "Bugs squashed today" |
| Today's commit log (Jun 21) | `git log --since=2026-06-21 --oneline` |
| Where the dashboard tabs come from | `scripts/arb_scanner.py:_bucket` + `docs/index.html:bucketOf` (kept in sync manually) |
| Why the elections scanner is duplicated from polling-agg | `scripts/elections.py` top docstring |

---

## Code-correctness signals

Things I'm reasonably confident about (validated by today's smoke runs
and direct API probes):

- Kalshi scraper handles its v2 trade-api shape correctly (paginates
  events with `/events?with_nested_markets=true`, parses `yes_bid`/
  `yes_ask`/`last_price` from `*_dollars` decimal fields).
- Polymarket scraper handles gamma's nested events shape AND the
  outcome/price JSON-string-vs-list ambiguity. Bid/ask come from
  `bestBid`/`bestAsk` and are then immediately refreshed from the
  live CLOB by `scripts/freshen_polymarket.py`.
- 78-digit Polymarket token_ids are read with `dtype={"yes_token_id":
  str, "no_token_id": str}` everywhere — silent float corruption was
  a real bug that took an afternoon to find earlier in this project.
- The matcher's guard chain (`scripts/matcher.py`, ~10 guards) is
  consistent with the false-pair history in `HANDOFF.md`. Each guard
  exists because of a specific past bug.
- The freshness guard, past-settle-date drop, one-sided book filter,
  and dead-prop scraper filters are all running in production (see
  GitHub Actions logs for "Dropped" and "Skipped" counters).

Things I'd want a second pair of eyes on:

- The arb math (`compute_arb` in `scripts/arb_scanner.py`). I rewrote
  it 2026-06-21 to use real bid/ask. The math is in the docstring;
  the test cases I ran by hand are in commit `5345556`'s body. Worth
  a careful read.
- The `_bucket` function and the `bucketOf` JS helper in
  `docs/index.html` are kept in sync manually. If you change one,
  change the other.
- The `freshen_polymarket.py` thread pool sets `NUM_WORKERS=16`. I
  didn't probe Polymarket's rate limit beyond a 50-request burst — if
  Polymarket starts 429ing under heavier load, we'd silently lose
  rows. Currently no retry inside the freshen step; failed fetches
  leave the gamma value in place.
- `scripts/elections.py` is a 920+ LOC hand-port of polling-agg's
  election logic. The two repos can drift if either side changes
  parsing regexes. We accepted this for now (see `AUDIT.md` to-do #6).

---

## Security surface

- No secrets in this repo. The workflow has `permissions: contents:
  write` only.
- No third-party tokens, no auth on the dashboard.
- All scraped data flows through `urllib.request` with no
  user-supplied input. Market titles flow into the dashboard via
  `innerHTML`, but the source is platform-curated (Kalshi /
  Polymarket / PredictIt) — they aren't accepting adversarial input.
  Listed as a low-priority XSS surface in AUDIT.md anyway.
- `data/raw/` is committed to the repo for cold-start convenience
  (see "We track raw scraper CSVs" above). Nothing sensitive lives
  there.

---

## How to know if something's broken

1. **GitHub Actions tab** — last "Daily refresh" run should be ✓.
   3-7 minute runtime is normal. Failure usually surfaces via:
   - Scraper SystemExit (one of the APIs returned nothing usable)
   - Freshness guard fail-loud (some CSV is stale)
   - PYYAML missing / requirements drift (rare)
2. **Live dashboard timestamp** — top right shows "Updated Xh ago".
   Should be < 24h. If older, see #1.
3. **`tools/deep_check.py`** — diagnostic script. Walks the top-50
   arb candidates, refetches live orderbooks, categorizes as REAL /
   THIN / GONE. Run when "this arb looks fishy."

---

## Today's session summary (2026-06-21)

If you want to time-box, today's commits cover:

| Commit | What |
|---|---|
| (search `git log --since=2026-06-21`) | A lot. See `AUDIT.md` "2026-06-21" entries for the full play-by-play. |

High-impact:
- Elections tab + ported polling-agg election scanner
- `compute_arb` rewrite to use real ASK / NO ASK (was midpoint)
- Polymarket NO orderbook fetch (was inferring `1 - YES_bid`)
- Polymarket live CLOB freshen (was relying on stale gamma cache)
- Past-settle-date / past-close-date / past-end-date filters
- Scrape freshness guard
- Display price = last_trade (matches platform UIs)
- Polymarket pagination: tried `/events/keyset`, found broken,
  reverted to `/events?offset=` capped at 2000

---

## If you have 15 minutes

1. Read this file (you're doing it).
2. Read `AUDIT.md` "To-do — next handoff" section.
3. Glance at the most recent GitHub Actions run.

## If you have an hour

4. Read `HANDOFF.md` top to bottom. Architecture + every gotcha.
5. Read `scripts/arb_scanner.py:compute_arb` (the math).
6. Read `scripts/matcher.py:match_fuzzy` + `match_threshold_pairs` (the matching).

## If you have a half-day

7. Read every Python file. The total is ~5k LOC; each file has a
   docstring at the top.
8. Pick a row from `docs/arb_data.js`, trace it backward to the raw
   scraper output, verify the math.
9. Tackle `AUDIT.md` to-do #1 (rewrite Polymarket scraper to use
   `clob.polymarket.com/markets`). That's the highest-leverage change
   open right now.
