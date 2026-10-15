# tools/

Checks around the pipeline. The tools in the table run in CI on every full
refresh and re-price (`.github/workflows/refresh.yml`, `reprice.yml`);
`deep_check.py` is a manual diagnostic.

| Tool | CI step | What it does |
|------|---------|--------------|
| `linkcheck.py --fix --pages` | Verify and repair market links | Rebuilds each leg's URL from the platform API by the ID that was priced and repairs any that differ (Kalshi `/markets/{series}/{event}`, Polymarket `/event/{event}/{market}`). Results go to `data/processed/linkcheck.json`. |
| `dash_smoke.js` + `dash_exercise.js` | Dashboard smoke test | Runs `docs/index.html`'s JS against the fresh data in a DOM stub, through every tab, filter and sort. A runtime exception fails the run before the commit, so the live page keeps its last good data. |
| `livecheck.py` | Live re-check of guaranteed baskets | Re-fetches both books and fee parameters for every guaranteed basket and recomputes the return. Report only (LIVE OK / CLOSED in the job summary). |
| `health.py --issue` | Board health issue | Opens, updates or closes the "Board health: needs attention" GitHub issue: unreviewed families, approvals lapsing within 14 days, repaired links, stale data, held-back baskets. |
| `llm_review.py` | Model review of new market families | No-op without an `ANTHROPIC_API_KEY` secret. With one, drafts verdicts for new families into the health issue; `LLM_REVIEW_MODE=auto` writes them to `data/series_map.json` only after a clean `--validate` run (`llm-validate.yml`). |

## `deep_check.py`

Walks the top 50 arb candidates from `docs/arb_data.js` (sorted by
guaranteed return, then net gap). For each, fetches live full
orderbooks on both platforms, then for three slippage budgets
(0pp / 1pp / 3pp) computes the maximum basket size where the
cross-platform arb still nets > 0 after fees.

Categorizes each pair as:

- **REAL** — net > 0 at ≥ 50 contracts within 1pp slippage
- **MARGINAL** — net > 0 at 10-49 contracts
- **THIN** — net > 0 only at zero slippage or < 10 contracts
- **GONE** — live prices closed the gap (apparent arb was stale)
- **WRONG_PAIR** — `utils/proposition.incompatibility` finds the titles
  describe different outcomes (title-similarity pairs only; structured
  match types are checked by the matcher)
- **PREDICTIT_LEG** — one side is PredictIt (no public orderbook to verify)
- **ONE_PLATFORM** — could only fetch one side

Use this after a daily refresh to spot-check whether the top entries on
the dashboard are actually fillable, or have already evaporated.

```bash
python tools/deep_check.py
```

Runs against the current `docs/arb_data.js`. Network access required
(fetches Kalshi `/orderbook` and Polymarket `/book` for every pair).

## Related diagnostic scripts

Two more diagnostic scripts live in the user's local checkout outside
this repo: `audit_arbs.py` (lighter top-K live verification, runs
against both repos) and `verify_arbs.py` (similar shape with the
scrutiny-style criteria check). Not committed because they target both
repos via the user's local paths.
