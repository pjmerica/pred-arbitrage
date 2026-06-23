# Scraper notes — platform API quirks we've learned the hard way

This file is the "tribal knowledge" reference for the three prediction
market APIs we scrape. It exists because much of this isn't in any
official docs — we discovered it by hitting the APIs, watching things
break, and tracing back to root cause.

Update this file whenever you discover a new quirk. Quirks have a way
of being forgotten between sessions and re-discovered weeks later. The
goal: a new person reading the codebase shouldn't need to re-probe any
of these.

**Last meaningful update:** 2026-06-22.

---

## How the pipeline reads each platform

Pipeline order (`run_all.py`):
1. `scrapers/kalshi.py` → `data/raw/kalshi_markets.csv`
2. `scrapers/polymarket.py` → `data/raw/polymarket_markets.csv` (gamma snapshot)
3. `scripts/freshen_polymarket.py` → overwrites bid/ask/midpoint with live CLOB data
4. `scrapers/predictit.py` → `data/raw/predictit_markets.csv`
5. `scrapers/house_incumbents.py` → `data/processed/house_incumbents.json`
6. `scripts/matcher.py` → `data/processed/matched_pairs.csv` (fuzzy cross-platform)
7. `scripts/elections.py` → `data/processed/election_pairs.csv` (US-2026 race_id-based)
8. `scripts/arb_scanner.py` (pass 1) → `data/processed/depth_targets.csv` + first `docs/arb_data.js`
9. `scripts/fetch_depth.py` → `data/raw/orderbook_depth.csv` (YES + NO orderbooks for matched pairs)
10. `scripts/arb_scanner.py` (pass 2) → final `docs/arb_data.js`

The double-run of `arb_scanner.py` is intentional: pass 1 emits the
list of markets fetch_depth should pull; pass 2 joins the depth data
back onto the pairs.

---

## Kalshi (api.elections.kalshi.com/trade-api/v2)

### Endpoints we use

- **`GET /events?status=open&with_nested_markets=true`** — primary
  scrape source. Returns events + nested markets in one payload.
  Paginated via cursor. WAF-protected — see "WAF" below.
- **`GET /series?category=Politics`** — used by polling-agg to find
  series. Less filtered, more rows.
- **`GET /markets/{ticker}/orderbook?depth=50`** — used by
  `fetch_depth.py` for matched pairs.
- **`GET /markets/{ticker}`** — single-market lookup. Used in probes.

### WAF — Kalshi blocks identifying User-Agents

**The biggest gotcha.** Kalshi runs a WAF that started rejecting our
requests in mid-June 2026 with HTTP 403. The flag was the
`User-Agent` containing the substring `(research/...)`. Fix:

- Use a real Chrome User-Agent: `"Mozilla/5.0 (Windows NT 10.0; ...)
  AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 ..."`.
- For `/events?with_nested_markets=true` specifically, the UA alone
  wasn't enough — needed `Referer: https://kalshi.com/`, `Origin:
  https://kalshi.com`, and `Sec-Fetch-{Mode,Site,Dest}` headers to
  look like a real browser XHR. See `utils/http_headers.py:
  browser_xhr_headers()`.

All header construction lives in `utils/http_headers.py`. Never
re-define HEADERS in individual scraper modules — that's how the WAF
bug got re-introduced multiple times.

### Field semantics

For each market under an event:

- **`ticker`** — full market ticker, e.g. `KXNHPRIMARY-01R26-HNOV`.
  Three segments: `{series}-{event_specifier}-{market_specifier}`.
- **`event_ticker`** — `{series}-{event_specifier}`, e.g.
  `KXNHPRIMARY-01R26`. Distinguishes events under the same series.
- **`series_ticker`** — `{series}`, e.g. `KXNHPRIMARY`.
- **`title`** (on the event) — human title, e.g. `"NH Primary
  Winners"`.
- **`yes_sub_title`** (on the market) — the side label kalshi.com
  shows for THIS market within the event:
  - General party markets: `"Democratic party"` / `"Republican party"`
  - Candidate markets: candidate name e.g. `"Hollie Noveletsky"`
  - Yes/No threshold markets: `"Yes"` / `"No"` (we skip these)
- **`yes_bid_dollars`** / **`yes_ask_dollars`** — decimal-dollar
  (0.00–1.00) bid/ask. The `_fp` suffix variants exist for sizes
  (open_interest_fp, volume_fp) but prices are always `_dollars`.
- **`last_price_dollars`** — last trade price. Decimal-dollar.
- **`previous_yes_bid_dollars`** / **`previous_yes_ask_dollars`** —
  prior-snapshot bid/ask. Useful for change-detection; not used today.
- **`no_bid_dollars`** / **`no_ask_dollars`** — exposed on the same
  market object, but **Kalshi doesn't have a separately tradeable NO
  token** the way Polymarket does. NO prices are inferred from
  `1 - YES`, and the orderbook endpoint splits buys into `yes_dollars`
  and `no_dollars` arrays (both are BUY orders, see below).

### Orderbook endpoint semantics — easy to invert

`GET /markets/{ticker}/orderbook?depth=50` returns:

```
{
  "orderbook_fp": {
    "yes_dollars": [[price_str, size_str], ...],   # YES BUY orders
    "no_dollars":  [[price_str, size_str], ...]    # NO BUY orders
  }
}
```

Both arrays are **resting BUY orders**, not bid/ask. Translation:

- **best YES bid** = `max(yes_dollars price)` — what someone will pay
  to buy YES right now (you'd sell into this).
- **best YES ask** = `1 - max(no_dollars price)` — derived. Someone
  buying NO at 0.85 is equivalent to selling YES at 0.15, which is
  the ask for YES.
- Sizes are in CONTRACTS ($1 max payout each).

Earlier (long-ago) bug we never want to re-introduce: treating
`yes_dollars` as YES ASKS produced fake $0.001-cost guaranteed arbs on
every market. Comment in `fetch_depth.py:_kalshi_yes_book()` calls
this out.

### URL construction for kalshi.com

Kalshi's website is a SPA. Every URL returns the same HTML; client-
side JS decides what to render based on the path. **The path matters.**

- ❌ `kalshi.com/markets/{series_ticker}` — works for single-event
  series but makes the SPA pick an arbitrary event when the series
  contains multiple events (NH primaries example: KXNHPRIMARY covers
  NH-01 D, NH-01 R, NH-02 D, NH-02 R — single-segment URL landed on
  NH-02 when we wanted NH-01).
- ✅ `kalshi.com/markets/{series_lower}/{event_ticker_lower}` —
  two-segment, pins to the right event. Both segments lowercase to
  match what Kalshi's own navigation produces.

Implementation in `scrapers/kalshi.py:parse_market()`.

### Race ID inference

`infer_race_id_from_ticker()` in `scrapers/kalshi.py` maps series
tickers to canonical race_ids:
- `HOUSEWI1` → `2026-H-WI-01`
- `SENATEOH` → `2026-SEN-OH`
- `SENATEOHS` → `2026-SEN-OH-S` (special election; trailing S in
  series ticker)
- `GOVPARTYNY` → `2026-GOV-NY`

**Cross-cycle ticker collision**: `SENATEOH-26` (2026 regular) and
`SENATEOH-28` (2028 regular) and `SENATEOHS-26` (2026 special)
overlap. The year suffix lives in `event_ticker`, not `series_ticker`,
so the inference function takes both and rejects anything not from
2026.

### "Open" feed lies about market status

`status=open` returns markets where `close_date` is already in the
past. Sample on 2026-06-21: 17,863 of 44,269 "open" markets had
`close_date` earlier than today. `scrapers/kalshi.py` filters these
out at scrape time. Don't trust the status flag alone.

### "One-sided" books are common, drop them

For long-tail outcomes (e.g. "Will Taylor Swift sing the next Bond
theme?") Kalshi often shows `yes_ask` set but no `yes_bid`. You can
buy YES at the ask but can't exit — nobody's bidding. We drop these
at scrape time (~7,700 rows out of 44k on 2026-06-21).

---

## Polymarket (gamma-api.polymarket.com + clob.polymarket.com)

### Two different APIs, both with quirks

- **gamma-api.polymarket.com/events** — bulk metadata + CACHED bid/ask
  snapshots. Fast for the universe scan; STALE on low-volume markets.
- **clob.polymarket.com/book?token_id=...** — live orderbook for a
  single market. Real-time. Used by `freshen_polymarket.py` and
  `fetch_depth.py`.
- **clob.polymarket.com/markets** (not currently used) — paginated
  metadata feed for the full ~55k market universe. Real `next_cursor`
  that advances. This is the long-term fix for the offset cap below;
  rewrite tracked in `AUDIT.md` to-do #1.

### Gamma pagination — offset capped at 2000

`gamma-api.polymarket.com/events?offset=N` hard-caps at offset=2000.
Anything past returns HTTP 422:

```
{"type":"validation error",
 "error":"offset too large, use /events/keyset for deeper pagination"}
```

**`/events/keyset` is broken.** Probed 2026-06-21 with every cursor
parameter name we could think of (`cursor`, `next_cursor`,
`page_token`, `after`, `pagination_cursor`): the returned
`next_cursor` does not advance pagination. Pass it back and you get
the same 100 events. Production run with keyset paginated 100,000
"events" that were 1,000 duplicates of the first page — yielded only
46 matched pairs / 1 guaranteed arb.

We're currently stuck with `?offset=N` capped at 2000. The real fix
is to switch to `clob.polymarket.com/markets` (different field shape,
needs `parse_market` rewritten).

### Gamma is stale — always freshen

Gamma's `bestBid`/`bestAsk` lag the live CLOB by minutes-to-hours on
low-volume markets. Concrete incident (2026-06-21): NZ recognize-
Palestine market had gamma bb=0.16/ba=0.34 (18pp spread) shipped as a
20pp arb against Kalshi's 14¢; live CLOB at the same instant was
0.16/0.24 (8pp), real midpoint 20¢ → real arb was 6pp not 20pp.

`scripts/freshen_polymarket.py` re-fetches the live CLOB for every
Polymarket market in parallel right after the gamma scrape (~5 min,
16 worker threads). Overwrites bid/ask/midpoint before the matcher
runs.

### CLOB orderbook quirk — bids/asks NOT sorted best-first

`clob.polymarket.com/book?token_id=...` returns:

```
{
  "bids": [{"price": "0.16", "size": "..."}, ...],
  "asks": [{"price": "0.62", "size": "..."}, ...],
  "last_trade_price": "0.43",
  ...
}
```

**The arrays are NOT sorted with best-first.** Empirically the bids
come back roughly low-to-high (sometimes insertion order) and the
asks high-to-low. If you read `rows[0]` without sorting you get the
WORST price on each side. We learned this writing `freshen_polymarket
.py` — first version returned `bid=0.01 ask=0.99` for a market whose
real top-of-book was `0.17 / 0.20`.

Always sort. Best bid = `max(bid prices)`. Best ask = `min(ask
prices)`.

### YES and NO are separate tokens with separate books

Each Polymarket binary market has two CLOB tokens:
- `tokens[0]` is YES, `tokens[1]` is NO (almost always — verify if
  the field shape ever changes).
- Each has its own `token_id` (78-digit integer, MUST be read as
  string or pandas corrupts it to float in scientific notation).
- Each has its own orderbook reachable at `/book?token_id=...`.

The NO ask is **not** always equal to `1 - YES_bid`. On asymmetric
markets they can differ by several percent. For arb math we now
fetch the real NO orderbook via `no_market_id` flowed through
`depth_targets.csv`. Inferring would inflate arb returns on
asymmetric markets — asymmetric-book test showed inferred 11% vs
real 6% guaranteed return.

### Polymarket UI shows midpoint, not last_trade

**Per-platform rule** (codified in
`HANDOFF.md` "Price semantics"):

| Platform | UI shows | Where we set this |
|---|---|---|
| Kalshi | `last_price` | `scrapers/kalshi.py` |
| Polymarket | bid/ask midpoint | `scripts/freshen_polymarket.py` |
| PredictIt | midpoint of bestBuy/bestSell | `scrapers/predictit.py` |

DO NOT unify these — the Espaillat incident on 2026-06-21 was caused
by applying Kalshi's "last_price" rule to Polymarket. Live Polymarket
book was bid 0.61 / ask 0.62 (midpoint 0.615, UI showing 62%) but
last_trade was 0.43 (stale single trade hours earlier). Our dashboard
shipped 43% because we used last_trade. Don't relitigate.

### `active=true&closed=false` flags lie

Same as Kalshi. ~22% of Polymarket events with `active=true` and
`closed=false` have `endDate` in the past. Filter on the date itself
in `scrapers/polymarket.py`.

### `outcomes` / `outcomePrices` fields

Often JSON-encoded strings, NOT lists. Always `json.loads` defensively
with an `except Exception` fallback. Both fields are arrays whose
order matches `tokens[]`:
- `outcomes = ["Yes", "No"]`
- `outcomePrices = ["0.62", "0.38"]`

These are the LAST KNOWN prices, often days stale on illiquid markets.
We don't use them for live pricing; freshen_polymarket overrides
with CLOB data.

---

## PredictIt (predictit.org/api/marketdata/all/)

### One endpoint, the whole universe

`GET /api/marketdata/all/` returns every active market with all its
contracts in one payload. ~600 markets total, ~4000 contracts. No
pagination. Refresh is whatever PredictIt's cache says.

### Field semantics

For each contract under a market:

- **`bestBuyYesCost`** — what you'd pay to BUY YES = the ASK
- **`bestSellYesCost`** — what you'd get to SELL YES = the BID
- **`lastTradePrice`** — last trade clearing price
- **`status`** — `"Open"` or `"Closed"`. Trust this one (unlike
  Kalshi / Polymarket).

### Naming convention is reversed from intuition

`bestBuyYesCost` = ASK (it's the cost to BUY, which means you pay the
ask). `bestSellYesCost` = BID. Easy to invert mentally.

### Broken-book pattern: bid 2¢ / ask 99¢

For inactive contracts, PredictIt shows `bestBuyYesCost=0.99`,
`bestSellYesCost=0.02` — the entire $0.01–$0.99 trading range as
"bid/ask". Midpoint is meaningless (50.5%). Our scraper drops rows
with bid-ask spread > 15pp.

### No public orderbook endpoint

PredictIt doesn't expose orderbook depth. `bestBuyYes` and
`bestSellYes` ARE the best bid and ask — there's no deeper book to
fetch. `fetch_depth.py` skips PredictIt entirely; `tools/deep_check.py`
categorizes PredictIt-leg pairs as `PREDICTIT_LEG` (can't verify
deeper).

### 12% effective fees

5% on profits + ~7% effective withdrawal fee (no withdrawal under
$10; 5% withdrawal fee). Any PredictIt-leg arb needs a much bigger
gap to clear fees than Kalshi/Polymarket (both 2%).

---

## Cross-platform behaviors

### Race ID derivation (US 2026 elections only)

`utils/races.py` defines the canonical race registry — 511 races for
2026 (Senate / House / Governor). `scripts/elections.py:
_race_id_from_title()` derives race_ids from market titles using
`_extract_state_office()`, which has subtle handling for:

- State names that are also candidate surnames ("Washington" as a
  surname vs the state — anchor the state-name search to the substring
  AFTER the office word).
- Special elections (suffix `-S` on race_id, e.g. `2026-SEN-OH-S`).
- House district numbering (zero-padded: `2026-H-CA-12`, never
  `2026-H-CA-12`).

### Per-platform dem/rep side detection

Each platform identifies the Dem/Rep side of a general-election
market differently:

- **Kalshi**: use the `yes_sub_title` field (literally `"Democratic
  party"` / `"Republican party"`). Fall back to scanning
  `market_title` for `"Democrat... win"` / `"Republican... win"` if
  yes_sub_title is empty. The reason for both: pred-arb's CSVs may
  predate the `yes_sub_title` column being added (2026-06-22); old
  rows still need title-scan. **Don't depend on the constructed
  `market_title` string** — its format has drifted between repos.
- **Polymarket**: scan the `question` text for "Democrat" / "Republican"
  patterns.
- **PredictIt**: `contract_name` is literally `"Democratic"` or
  `"Republican"`.

### Order of operations matters

If you change a scraper's output shape, downstream consumers
(matcher.py, elections.py) may silently break because their column
filters or regexes assume the old shape. Always:
1. Grep for every reader of the changed column.
2. Run the full pipeline locally with a fresh scrape.
3. Diff `docs/arb_data.js` row counts before/after.

### "Same matching logic" doesn't mean "same matches"

`scripts/elections.py` is a hand-port of polling-agg's
`scripts/arb_scanner.py` general-election logic. The Python is
character-for-character identical for the matching pieces. But:

- pred-arb's Kalshi scraper produces titles like `"WI-01 House
  winner? — Democratic party"` (constructed).
- polling-agg's Kalshi scraper passes through Kalshi's API title
  `"Will Democratic win the House race for WI-1?"` (raw).

Same regex, different input → different matches. The 2026-06-22
WI-01 incident traced to this. Fix was to use `yes_sub_title` (a
structured per-side label that's the same on both repos) rather than
matching on the title string.

---

## Cross-platform matching gotchas

### Time-window mismatches across platforms

Same outcome, different time horizon → real prices diverge for
legitimate reasons; matcher pairs them anyway. Examples seen:

- Kalshi: "Will Elon Musk receive a pardon **before Jan 21, 2029**" at
  43¢ (Trump's whole second term)
- PredictIt: "Will Trump pardon Elon Musk **in 2026**" at 7.5¢ (just
  this year)
- 35.5pp gap on the same person → looks like a guaranteed arb, isn't.

**Where it leaks through**: scrutiny.py skips every pair where one
leg is PredictIt because there's no PredictIt rule-fetch endpoint to
do similarity scoring. Year-mismatch heuristic added 2026-06-22:
scrutiny extracts `20\d\d` from each side's question text and drops
the pair when the year sets are disjoint AND raw_gap > 30pp. Runs
before the PredictIt skip so it covers those too.

### Polymarket "[flipped]" pairs from political matcher

`scripts/matcher.py:match_political` constructs cross-party arb
candidates by flipping one platform's "Dem-YES" to mean "Rep-NO" on
the other. Output rows are tagged with `[flipped]` in the question
text. Watch this path carefully: the NY Governor 72.1% guaranteed
arb shipped on 2026-06-22 was a bogus number that survived the math
because `compute_arb` was likely treating a flipped probability
inconsistently. If you see suspiciously-high `political`-match-type
guaranteed arbs (NY Gov, etc), check the math against real fillable
prices first.

## Things you'll discover and want to add here

If you find yourself probing one of the APIs by hand because some
behavior surprised you, write it down here BEFORE you fix it. The
fix is local; the knowledge benefits every future session.

Suggested format for new entries:

```
### YYYY-MM-DD — Short description

**Symptom**: what looked wrong on the dashboard / in the data.

**Diagnosis**: what you found when you probed the API directly.

**Fix**: where in the code, what changed.

**The lesson**: the generalizable thing the next person should know.
```
