"""Per-contract TAKER fees, from each platform's published formula.

Verified 2026-09-24:
  Kalshi      fee = ceil_to_cent(0.07 × M × C × P × (1 − P))
              M = the series' `fee_multiplier` (GET /trade-api/v2/series — one
              call returns all ~14k series with fee_type + fee_multiplier;
              today: 14,176 quadratic×1, 159 quadratic_with_maker_fees×1,
              18 ×0.5, 14 ×0 = fee-free). Makers pay 0.0175 × ... on
              *_with_maker_fees series; we always assume taker (crossing the
              book). Sources: kalshi.com/docs/kalshi-fee-schedule.pdf,
              docs.kalshi.com fee_rounding.
  Polymarket  fee = C × rate × p × (1 − p), taker only, rate from each
              market's gamma `feeSchedule.rate` when `feesEnabled` (crypto
              0.07, sports/culture/weather/economics 0.05, politics/finance/
              tech 0.04, geopolitics 0). Source: docs.polymarket.com fees.
  PredictIt   10% of profit on a winning contract + 5% on withdrawal. No
              API; modelled per contract as 0.10 × (1 − p) + 0.05 (assumes
              that leg wins and is withdrawn — the conservative case).

A leg whose fee parameters are unknown falls back to the old flat per-leg
charge (FLAT_FALLBACK), which is higher than any quadratic fee here.
FEE_SAFETY_MARGIN (per basket) covers Kalshi's round-up-to-the-cent on
small orders and one tick of slippage.
"""

FLAT_FALLBACK = {"kalshi": 0.02, "polymarket": 0.02, "predictit": 0.12}
FEE_SAFETY_MARGIN = 0.005
KALSHI_TAKER_COEF = 0.07


def kalshi_spec(fee_multiplier):
    try:
        m = float(fee_multiplier)
    except (TypeError, ValueError):
        return None
    return None if m != m else {"kind": "kalshi", "mult": m}


def polymarket_spec(fee_rate):
    try:
        r = float(fee_rate)
    except (TypeError, ValueError):
        return None
    return None if r != r else {"kind": "polymarket", "rate": r}


def predictit_spec():
    return {"kind": "predictit"}


def leg_fee(platform, spec, price):
    """Fee in $ per contract for a taker buy at `price` (0-1)."""
    p = float(price)
    if spec is None:
        return FLAT_FALLBACK.get(platform, 0.05)
    k = spec["kind"]
    if k == "kalshi":
        return KALSHI_TAKER_COEF * spec["mult"] * p * (1 - p)
    if k == "polymarket":
        return spec["rate"] * p * (1 - p)
    if k == "predictit":
        return 0.10 * (1 - p) + 0.05
    return FLAT_FALLBACK.get(platform, 0.05)
