# tools/

Diagnostic scripts for investigating suspect arb pairs. Not part of the
daily pipeline — these are for the human to run when something on the
dashboard looks fishy.

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
- **WRONG_PAIR** — questions describe different outcomes
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
