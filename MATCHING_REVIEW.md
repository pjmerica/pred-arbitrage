# Matching Review — why the board shows fake arbs and wrong links

**Date:** 2026-09-23 · **Data reviewed:** live `docs/arb_data.js` from CI refresh `2026-09-23T17:34Z`
**Scope:** every script in the pipeline, the full commit history (221 commits, 2026-04-13 → 2026-07-31), and the uncommitted working tree.

> **Status (2026-09-23, same day):** P0 items 1–5 are implemented. Same-data result: guaranteed 69 → 5, all equal-strike crypto pairs; 33 rows moved to `unverified`; 0 margin/combo election legs; Polymarket links open the exact market. §2–§5 describe the board *before* the fix. P1 and P2 remain open, except a first slice of #13 (`tests/test_matching_regressions.py`, run in CI). See CHANGELOG 2026-09-23.
>
> **Second pass (same day):** audited every pair type on the post-fix board. Fixes:
> - `utils/proposition.py` outcome-signature gate on fuzzy pairs. This is the first piece of #6: fields are placement, period, division, teams, fixture date, day-vs-window, storm category, exact score and person.
> - Exact-strike threshold tolerance.
> - Kalshi ticker/title state cross-check (Kalshi's `SENATELA-26` is the Kentucky race).
> - Settled-one-side and crypto basis-risk checks in the scanner.
>
> New findings for §6:
> - **Crypto thresholds are not identical questions.** Kalshi resolves on a CF Benchmarks trimmed mean, Polymarket on any Binance 1-minute wick. Only YES-Polymarket + NO-Kalshi is hedged; this is now enforced.
> - **Window-start differences are common** (Polymarket "between market creation and …" vs Kalshi calendar year). The settled-one-side check catches the extreme cases. A rules-text parser for window start is still P1 #10.
>
> **Third pass (2026-09-24):** covered the arb math, order-book fetch, scrapers, scrutiny and dashboard.
> - **The recommended bet split was backwards.** It put $96 on the 3.5¢ leg and $4 on the 90¢ leg, which is not a hedge. It now holds equal contracts on both legs. **polling-agg-2026 has the same bug** (`scripts/arb_scanner.py`, inverse-odds split); fixed there on 2026-09-24 as well.
> - Per-leg liquidity, plus a "max $ at these prices" capacity figure on the dashboard.
> - Per-market Polymarket liquidity (it was the event total).
> - Scrutiny warns instead of silently dropping.
> - "Or above" vs "exactly" bound field in the signature.
> - `deep_check.py` fee table fixed.
>
> **Fourth pass (2026-09-24):**
> - Every link on both dashboards was checked live. Polymarket pages carry the leg's exact question, except sports games, which open the game with the sub-market selected. Every Kalshi link pins the leg's event.
> - A 15% implausible-return cap now applies (guaranteed → unverified).
> - CI job summary lists what each refresh published.
> - Finalist/semifinalist placement added, after new DWTS "Finalists" markets appeared.
> - polling-agg-2026 got the same fixes: its 3 live "guaranteed" arbs (56–81%) were margin buckets. See its CHANGELOG.
>
> **Fifth pass (2026-09-24/25): every board arb verified, real fees, series map.**
>
> *Per-arb verification.* Live books plus both rules texts, read side by side:
>
> | Pair | Verdict |
> |---|---|
> | BNB <$500, BTC <$55k | Real. The basket takes the safe direction (Polymarket's window starts earlier, and it resolves on any Binance wick, so it is the easier trigger). BNB capacity is about 5 contracts. |
> | Bad Bunny Google Top-5, French ballot ×5 | Same rules on both sides. Real, small (+4–6¢ gross). |
> | Hawaii hurricane | **Not a hedge.** Kalshi counts the 2026 season (ends Nov 30); Polymarket counts through Dec 31. The basket bought YES on the narrower window. New safeguard below. |
>
> *New safeguards.*
> - **`utils/rules_window.py`:** parses each leg's resolution window from its rules text. A basket must buy YES on the side whose window contains the other's (`window_mismatch`: guaranteed → unverified).
> - **`data/series_map.json`:** 52 hand-reviewed Kalshi-series ↔ Polymarket-event families, each read in full on 2026-09-24.
>   - **44 approved.** These may now be "guaranteed". Tie-rule and deadline caveats are recorded per family; the Nations League entries lapse on 2026-11-30, before knockout matches.
>   - **8 rejected, dropped with the reason logged:**
>     - album release (definition and window);
>     - Hawaii hurricane (window);
>     - crypto capital-gains (law vs executive action);
>     - Spain PM (post-election vs any next PM);
>     - OPEC exit (UAE exclusion);
>     - Somaliland and Palestine recognition (qualifying acts differ);
>     - Nobel Peace (joint prize collapsed to one winner, special clauses).
>   - **Unreviewed families** stay unverified and are listed in `data/processed/series_review.csv`, which CI commits each run. To extend the map, read both rules in full and add an entry with a note.
>
> *Real fees.* `utils/fees.py` uses each platform's published taker formula, verified 2026-09-24, plus a 0.5¢ per-basket safety margin. Unknown parameters fall back to the flat 2% / 12%.
>
> | Platform | Taker fee | Where the parameters come from |
> |---|---|---|
> | Kalshi | 0.07 × multiplier × C × P(1−P), rounded up to the cent | `fee_multiplier` per series, from one `GET /series` call |
> | Polymarket | rate × C × p(1−p) | each market's `feeSchedule.rate` when `feesEnabled` (crypto 0.07, sports/culture/weather 0.05, politics 0.04, geopolitics 0) |
> | PredictIt | 10% of profit + 5% withdrawal | no API; modelled on the conservative side |
>
> Sources: docs.polymarket.com (fees) · docs.kalshi.com (fee rounding, series fee fields) · Kalshi fee schedule PDF (via search; kalshi.com rate-limits scripted fetches).
>
> Live verification with `tools/deep_check.py` on the top 50: 0 WRONG_PAIR. The crypto guaranteed arbs are real but small (about $5–$30 capacity at quoted prices), and they close within minutes.

---

## 1. Summary

- **About 59 of the 69 "guaranteed" arbs on the live board are fake.** Each one pairs two *different* questions: "2nd place" vs "win", "wins by 0–3%" vs "wins", "XRP $3.00" vs "XRP $3.20". Even with the default *hide-suspicious* filter, **30 of the 38 visible guaranteed rows are fake, including the top 8.**
- **Links go to the wrong prop** for two reasons:
  - Polymarket links point at the *event*, not the market that was priced. A click on the XRP event opens a page with 20+ strikes.
  - Some pairs link to a genuinely different market. The election matcher now selects Polymarket's new "margin of victory" markets as the Dem-win leg.
- **Root cause:** the pipeline decides two markets are the same because their *titles look alike*. It then removes bad pairs one at a time with ~30 hand-written guards. Each time a platform lists a new kind of market (margin buckets, placement markets, 2nd-half props), it gets through by default. Since July 5 nobody has added a guard, so the board has filled back up.
- **The fix is to fail closed.** Pair two markets only when both parse into the same structured proposition, or when a human has approved the series pairing. Anything else can be *shown* but never labelled *guaranteed*. Details are in §5.

---

## 2. The live board today (2026-09-23)

568 pairs total, 69 labelled `guaranteed`.

| # | Failure class | Example (A ↔ B) | Where it comes from |
|---|---|---|---|
| 16 | Placement vs winner | Kalshi "Big Brother S28 — **2nd place** — Taylor Brown" ↔ PM "Will Taylor Brown **win** Big Brother S28?" | Fuzzy matcher. `rank_token()` only knows `#N`/top/first, not "2nd place", "3rd place" or "Top 3 finishers" ([matcher.py:605](scripts/matcher.py#L605)) |
| 13 | Threshold strike mismatch | Kalshi "XRP above **$3.00**" ↔ PM "XRP reach **$3.20**" | Threshold matcher `max(2.0, …)` tolerance. **Fixed in the uncommitted working tree; not deployed** |
| 8 | Margin-of-victory vs party-win (political path) | PM "Republican wins TN Senate **by 45% or more**" ↔ PI "Which party wins TN Senate — Republican" | `political_contract_type()` sees "republican party" → `party_winner` ([matcher.py:150](scripts/matcher.py#L150)); `best_prob` then picks the highest-liquidity market for the race_id, which is now a margin market |
| 7 | Margin-of-victory vs party-win (elections path) | Kalshi "Dem wins AZ-02" ↔ PM "Dem candidate wins AZ-02 **by 0%–3%**" | `_load_polymarket_general` treats *any* question containing "democratic" as the Dem-win market and keeps the highest-liquidity one ([elections.py:391](scripts/elections.py#L391)) |
| 5 | "Closest race" / "within 5%" vs candidate-win | Kalshi "Kansas Senate winner — Adam Hamilton" ↔ PM "Will the Kansas Senate race be **within 5%**?" | `match_political` only rejects when types *differ*. Both classify as `other`, and `other == other` passes ([matcher.py:282](scripts/matcher.py#L282)) |
| 3 | Full-match vs 2nd-half prop | "Barcelona vs Paris FC: BTTS" ↔ "…Both Teams to Score **in Second Half**" | Fuzzy. `sub_bet_type()` knows "first half" but not "second half" ([matcher.py:665](scripts/matcher.py#L665)) |
| 3 | Monthly max (touch) vs price on a date (level) | "How high will XRP get in **September** — above $1.70" ↔ "XRP **above $1.70 on September 25**" | Fuzzy path. The touch-vs-level guard exists only in the threshold matcher |
| 2 | Different tournament | Chess Olympiad **Women's** ↔ **Open** | Fuzzy. No gender/division guard |
| 1 | Opposite outcome | "League Phase **Top** Finisher — Real Madrid" ↔ "Real Madrid finish **last**" | Fuzzy |
| 1 | Different scope | "Hurricane landfall in **Hawaii**" ↔ "**Category 5** hurricane landfall in the **US**" | Fuzzy |
| **~10** | **Plausibly real** | XRP $3/$4/$6 = $3/$4/$6 (year max), BNB <$500, BTC <$75k Sept, ETH <$1,500, Hawaii hurricane = Hawaii hurricane, Google Top-5 (×2), de Villepin on ballot | Threshold matcher + clean fuzzy |

**Why fake pairs look like big wins:** if A and B are different questions, the "Yes on A + No on B" basket is not a hedge. It is two bets that can both lose. The more different the questions, the bigger the apparent gap, so fake pairs rise to the **top** of a board sorted by return. A 12–80% "guaranteed" return on liquid markets is almost always a mismatch.

---

## 3. The link problem

| Platform | What we link to | Problem |
|---|---|---|
| Polymarket | `polymarket.com/event/{event_slug}` ([polymarket.py:709](scrapers/polymarket.py), [elections.py:92](scripts/elections.py#L92)) | Multi-market events (XRP price ladder, DWTS winner, margin-of-victory buckets) land on a page with many options. The user can't tell which one we priced. `market_slug` is already scraped but not used in the URL. |
| Kalshi | `kalshi.com/markets/{series}/{event}` ([kalshi.py:264](scrapers/kalshi.py)) | Correct event, but an event is a ladder or candidate list. The dashboard doesn't show the exact strike or candidate label (`yes_sub_title`) next to the link. |
| PredictIt | `predictit.org/markets/detail/{market_id}` | OK. It's a market with several contracts, so the contract name must be displayed. |
| Election rows | Built in `elections.py`, not the scrapers | Can point at a genuinely **different** market (margin buckets, see §2), not just the right event. |

---

## 4. History of code changes

Full per-commit detail from 2026-06-20 onward is in [CHANGELOG.md](CHANGELOG.md). This section condenses all 221 commits into phases and records the lesson each phase taught.

### Phase 1 — Build-out (Apr 13–14)
- `7bbf10f` Initial scanner: Kalshi/Polymarket/PredictIt scrapers → race_id + fuzzy (`token_sort_ratio`) matcher → arb math → static dashboard.
- `8affe2d` First false-positive fixes: fuzzy threshold 82→88, contract-type classification, party flip, longest-first state names, year-overlap guard.
- `c8f4170`, `5033803`, `7c6449a` Scrapers moved to Polymarket `/events` and Kalshi `trade-api/v2` (32k markets); category-grouped fuzzy matching with `cdist`. Pairs 168→613, "guaranteed" 59→219.
- **Lesson not yet visible:** 219 "guaranteed" arbs was never plausible. Wider coverage mostly added false matches.

### Phase 2 — First guard wave (Apr 17 – May 7)
- `69dbdb6` Polymarket URLs switched from condition_id to **event** slug. This is the origin of today's event-level links.
- `69dbdb6`, `1311906`, `75c6754`, `f8ac1f3`, `23fe4b5` Guards added: numeric-bucket, run-vs-win, rank bucket, date-bucket, candidate-name, month-anchor, (month, day), sub-bet type, threshold-bucket, office-role (P vs VP), demographic.
- `75c6754` Order-book depth fetch. A manual audit that day found **every** "guaranteed" arb was illusory.
- `90ddf7b`, `72d4cd1` Drop stale one-sided / wide-spread Polymarket quotes.
- `550c615` Don't party-flip three-way races (fake 76% Nebraska).
- `8e3078c` GitHub Actions daily refresh. From here on, code auto-deploys with no review gate.

### Phase 3 — Price correctness (May 7 – Jun 21)
- `1db5dae`, `222826b`, `d13877f`, `8d9d5ae` Drop broken/wide books on each platform.
- `94b7cf2` `scrutiny.py`: compare resolution-rules text, but **only for gaps >30pp**.
- `3c7bba8` Shared HTTP headers; fees synced to 2/12/2.
- `59f4ffc` **`elections.py` ported from polling-agg.** This is a second, independent election matcher running alongside `match_political`, with duplicates explicitly accepted.
- `c5c2a97`, `af9d5ca`, `dfc471e`, `534ecd2` Past-dated / untradeable / stale filters and the Polymarket live CLOB freshen.
- `65d1ccf`, `c448b91` Arb math moved from midpoints to real asks, including the real Polymarket NO book (Somaliland incident).
- `2781921` Polymarket keyset pagination found broken; reverted to offset (capped at 2000).
- `3c33ad1` → `aadec8d` Display-price flip-flop (last trade vs midpoint), settled as a per-platform rule.

### Phase 4 — Link + taxonomy fixes (Jun 22–24)
- `e6dd0f6` Kalshi URLs pinned to the event (NH-01 link landed on NH-02).
- `f9ad7cc`, `d6646bb` Kalshi election side detection uses the raw API title.
- `c19edb2` Disjoint-year scrutiny guard.
- `baf0cf6` Four-bucket arb taxonomy (guaranteed / pre-fee / price-gap / one-sided).
- `9f7816b`, `fc09dce` Per-platform Yes/No ask columns; real Kalshi NO quotes.
- `39d8491` Common-prefix subject guard (Taiwan/Somaliland).

### Phase 5 — Coverage push + second guard wave (Jul 3–5)
- `8d6647a` **Per-category fuzzy thresholds lowered** (crypto 72, finance 74, sports 80). Pairs 152→280. This opened the door to most of today's fuzzy fakes.
- `7cedd68` Threshold matcher (crypto/commodities) with `max(2.0, 2%)` tolerance. This is the bug behind today's 13 XRP strike mismatches.
- `ef74bae`, `4252b13` Tournament-winner and primary-nominee structured matchers. These are the right pattern (see §5).
- `1774f47` Exclude already-claimed markets from fuzzy (Argentina winner vs finalist).
- `52215f1` Five more fuzzy guards (diverging keywords, settle drift, year asymmetry, dash-subject, range-vs-point).
- `9207fa2` Polymarket coverage ×3 (five ordered passes). The larger universe surfaced **six new fake-arb classes** in one day, each patched with another guard.
- `2c469aa` Guaranteed rows sorted by return. This correctly surfaces real arbs, but also puts the biggest *mismatches* first.
- `a43b249` Real PredictIt Yes/No quotes replace the synthetic spread.

### Phase 6 — Unattended (Jul 5 → today)
- CI kept refreshing twice a day (all runs green). Meanwhile Polymarket listed **margin-of-victory** events for every House/Senate/Governor race, plus "closest race" and "within 5%" markets. Big Brother / DWTS placement markets appeared, and USL/CanPL/UWCL second-half props. None were anticipated by a guard, so all of them flowed onto the board.
- **Uncommitted since Jul 31:** the `match_threshold_pairs` tolerance fix (`max(strike*0.02, 0.01)`) plus its CHANGELOG entry. Verified today: threshold pairs 77→56, **zero** strike mismatches remain. CI still runs the old code.

### Pattern across all phases
The same cycle has repeated at least eight times:
1. Coverage widens (new scraper, lower threshold, more passes).
2. The board fills with fake "guaranteed" arbs.
3. A human audit finds them.
4. A guard is added for each specific example.
5. The board looks clean until the platforms list something new.

The guard list is a **denylist**. A denylist can't anticipate market types that don't exist yet.

---

## 5. Root causes

1. **Matching is on title similarity, not on what the market resolves on.** `token_sort_ratio` ignores word order and scores "2nd place — X" vs "X win" highly. Worse, the one token that changes the meaning ("2nd", "second half", "by 3%", "women's") is exactly what fuzzy scoring downweights.
2. **Guards are a denylist (fail-open).** There are ~30 guards across `match_fuzzy`, and each blocks one previously seen shape. New shapes pass by default.
3. **The political paths "pick the best market per race" instead of "pick the market that asks the right question".** `best_prob` / `best_liq` choose by open interest or liquidity from *any* market tagged with the race_id. When a platform adds high-liquidity derivative markets (margins), those win.
4. **`other == other` counts as a match** in `match_political`.
5. **Two election matchers** (`match_political` + `elections.py`) with different rules, and duplicates accepted by design. Twice the surface area for the same bugs.
6. **Classification trusts the match.** `compute_arb` labels any pair with real quotes and a basket under $1 as `guaranteed`. Nothing in the pipeline asks whether both markets could resolve differently. Resolution-rules scrutiny exists but only fires above 30pp, and the most profitable fakes sit at 10–25pp.
7. **No one-to-one constraint.** One Kalshi strike ("XRP above $3.00") is paired with three Polymarket strikes; one Kalshi placement market pairs with several PM markets.
8. **No regression tests and no publish gate.** Every past incident was checked by eye on one run. CI pushes whatever the pipeline produces, and nothing stops a run where the guaranteed count or top return jumps.
9. **Local/deployed drift.** A verified fix has sat uncommitted for 7+ weeks, and the local data was 2.5 months stale at the start of this review.

---

## 6. Long-term fixes

### P0 — Stop the bleeding (small, do first)
1. **Commit and push the pending threshold-tolerance fix.** Already verified: 13 fakes gone.
2. **`match_political`: reject `other` pairs.** Change `if type_a != type_b` to also skip when `type_a == "other"`.
3. **Election Dem/Rep leg selection: allowlist, not "contains democratic".** Accept only titles matching an explicit party-win template, and reject `by \d+%`, `margin`, `within`, `closest`, `flip`, `turnout`. Apply to both `elections.py` and `match_political`.
4. **Deep links:** Polymarket `…/event/{event_slug}/{market_slug}` (check the format against a few live markets before shipping). On the dashboard, print the exact leg being bought next to each link: Kalshi `yes_sub_title` / strike, PredictIt contract name.
5. **Temporary publish rule:** only `threshold`, `tournament-*`, `primary-nominee` and a vetted `general` may be labelled `guaranteed`. Show `fuzzy` / `political` pairs as "unverified match" until P1 lands.

### P1 — Change the matching model (the real fix)
6. **Parse every market into a structured proposition, then match on equality.** Something like:
   `{subject, event, outcome_type (win / place-N / top-N / margin-bucket / threshold / BTTS…), qualifier (period, half, division, gender), strike, direction, touch|level, window_end}`.
   Markets that don't parse are **unmatched**. They are not handed to fuzzy for a best guess. The tournament and nominee matchers already work this way and have zero false positives in today's data; generalise that approach.
7. **Use platform structure, not titles.** Kalshi exposes `series_ticker`, `event_ticker`, `yes_sub_title`, `strike_type` / `floor_strike` / `cap_strike` and `rules_primary`. Polymarket exposes the event slug, `groupItemTitle`, `groupItemThreshold` and `description`. Strikes and candidates should come from these fields, not regexes over the question text.
8. **Curated series mapping (human in the loop).** A versioned file such as `data/series_map.json` maps Kalshi series ↔ Polymarket event-slug patterns once, e.g. `KXXRPMAXY` ↔ `what-price-will-xrp-hit-before-*`, and explicitly *no match* for `KXBIGBROTHERRANK` vs "win". Unmapped combinations go to a **review queue** (a dashboard tab), not the board. This is what makes new market types fail closed.
9. **Fuzzy becomes discovery only.** Fuzzy output feeds the review queue so a person can approve a series mapping. It never emits a tradeable pair directly.
10. **Resolution-rules check on every guaranteed candidate**, not just >30pp. Compare the Kalshi `rules_primary` window/threshold vs the Polymarket `description`, and cache the result.
11. **One-to-one assignment** per (event, outcome): each leg may appear in at most one pair, keeping the closest match.
12. **One election matcher.** Fold `elections.py` into the structured matcher (race_id + outcome_type + party/candidate) and delete the duplicate path.

### P2 — Process guards so it can't silently regress
13. **Golden test set.** Turn every incident in CHANGELOG and this review into a labelled `(question_a, question_b, should_match)` case (Argentina finalist, Wyoming flip, XRP $2 vs $4, BB 2nd vs win, AZ-02 margin, Kansas within-5%…). `pytest` in CI runs before the pipeline publishes.
14. **Publish gate in `refresh.yml`.** Refuse to push, or push with a banner, when:
    - any guaranteed return is above ~10%;
    - the guaranteed count jumps by more than 2× run-over-run;
    - a new (Kalshi series, Polymarket event-slug) combination produces a guaranteed row.
15. **Weekly novelty report.** List the new series and event slugs that appeared. That is exactly where the Phase 6 breakage came from.
16. **Docs consolidation.** HANDOFF, AUDIT, NOTES_FOR_REVIEWER and SCRAPER_NOTES overlap heavily (~2,200 lines). Keep CHANGELOG plus one architecture doc so the next reviewer reads one source of truth.

### Suggested order
P0 items 1–5 (a few hours) → golden test set (#13) so each later step is measurable → structured proposition + series map (#6–8) for the categories that actually produce real arbs (crypto thresholds, tournaments, elections, awards) → retire fuzzy-to-board (#9) → publish gate (#14).

---

## Appendix — minor findings
- PredictIt candidate rows from `elections.py` have `market_id = None`, so real PI quotes can't be joined and those pairs are always `one-sided` ([elections.py:666](scripts/elections.py#L666)).
- `_load_polymarket_general` requires Dem + Rep on the same platform, but the leg it keeps is still chosen by *event* liquidity (`liquidity` is event-level in the scraper, [polymarket.py:724](scrapers/polymarket.py)). Every market in a margin-bucket event ties, so the choice among them is arbitrary.
- `scrutiny.py` only drops below a 50% rules-text similarity. SequenceMatcher on boilerplate-heavy rules text rarely scores that low, even for different questions.
- Local `data/processed/matched_pairs.csv` and `depth_targets.csv` date from Jul 5. They're gitignored, so CI regenerates them, but local debugging against them is misleading.
