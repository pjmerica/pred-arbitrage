"""
Arbitrage scanner across all matched market pairs.

Reads data/processed/matched_pairs.csv and computes:
  - raw gap (abs diff in implied prob)
  - net gap after fees
  - arb type: guaranteed (lock profit both sides) vs one-sided (price discrepancy)
  - stake ratio for guaranteed arb
  - guaranteed return %

Sorted by: guaranteed first, then by settle_date ascending (fastest to resolve).

Fee assumptions (round-trip):
  Kalshi:    2%  (1% taker each way)
  PredictIt: 12% (10% on profits + ~2% effective withdrawal)
  Polymarket: 2% (taker fee)

Output: docs/arb_data.js
"""

import pandas as pd
import numpy as np
import json
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).parent.parent
PROCESSED = ROOT / "data" / "processed"

FEES = {
    "kalshi":    0.02,
    "predictit": 0.12,
    "polymarket": 0.02,
}


def compute_arb(prob_a, prob_b, fee_a, fee_b):
    """
    Returns arb type, net gap, guaranteed return, and stake ratios.

    Guaranteed arb: buy Yes on A + buy No on B (= buy Yes complement).
    Cost = prob_a + (1 - prob_b). If cost < 1, gross profit = 1 - cost.
    Net profit after fees = gross - fee_a - fee_b.

    Stake ratio (Kelly-style for guaranteed):
      To equalise payout on both outcomes:
        sA / total = (1 - prob_b) / (2 - prob_a - (1-prob_b))  ... simplifies to:
        sA = (1 - prob_b) / (2 - prob_a - 1 + prob_b) = (1-prob_b)/(1 - prob_a + prob_b) -- hmm
      Simpler: payout if A wins = sA/prob_a, payout if B wins = sB/(1-prob_b)
      Set equal: sA/prob_a = sB/(1-prob_b), sA+sB=1
        sA = prob_a / (prob_a + 1 - prob_b)  ... no, let's do it directly.

      Actually for binary arb:
        Bet sA on Yes@prob_a on platform A.  Return if Yes: sA/prob_a, if No: 0.
        Bet sB on No (= Yes complement) at (1-prob_b) on platform B. Return if No: sB/(1-prob_b), if Yes: 0.
        To guarantee same net on both outcomes:
          sA / prob_a = sB / (1 - prob_b)
          sA + sB = 1
        => sA = prob_a / (prob_a + 1 - prob_b)  -- WRONG, let's re-derive.

      If Yes: win sA*(1/prob_a - 1) - sB  (A pays out (1-prob_a)/prob_a profit, B stake lost)
              = sA/prob_a - sA - sB = sA/prob_a - 1
      If No:  win sB*(1/(1-prob_b) - 1) - sA
              = sB/(1-prob_b) - 1
      Set equal: sA/prob_a = sB/(1-prob_b)  and sA+sB=1
        sA = prob_a*(1-prob_b) / (prob_a*(1-prob_b) + (1-prob_b)*... hmm.

      Simplest correct form:
        sA = (1-prob_b) / ((1-prob_b) + prob_a*(... ))  -- let's just use the standard formula.

      Standard prediction market arb stake formula:
        sA = (1/prob_a) / (1/prob_a + 1/(1-prob_b))   -- normalised inverse odds
        sB = (1/(1-prob_b)) / (1/prob_a + 1/(1-prob_b))
        Guaranteed ROI (gross) = 1/(prob_a + (1-prob_b)) - 1  (only if prob_a+(1-prob_b)<1)
    """
    prob_a, prob_b = float(prob_a), float(prob_b)
    raw_gap = abs(prob_a - prob_b)
    net_gap = raw_gap - fee_a - fee_b

    result = {
        "raw_gap_pp": round(raw_gap * 100, 2),
        "net_gap_pp": round(net_gap * 100, 2),
        "profitable_onesided": bool(net_gap > 0),
        "arb_type": "one-sided",
        "guaranteed_return_pct": None,
        "stake_a_pct": None,
        "stake_b_pct": None,
        "action": "",
    }

    # Try both directions for guaranteed arb
    # Direction 1: buy Yes on A, buy No on B (i.e. buy Yes@prob_b complement)
    for pA_yes, pB_yes in [(prob_a, prob_b), (prob_b, prob_a)]:
        pB_no = 1 - pB_yes
        cost = pA_yes + pB_no  # total cost to cover both outcomes
        if cost < 1.0:
            gross = 1.0 - cost
            net = gross - fee_a - fee_b
            if net > 0:
                # Stake ratio using inverse-odds normalisation
                inv_a = 1 / pA_yes
                inv_b = 1 / pB_no
                total_inv = inv_a + inv_b
                sA = inv_a / total_inv
                sB = inv_b / total_inv

                if pA_yes == prob_a:
                    action = f"Buy Yes on {'{pa}'}, Buy No on {'{pb}'}"
                    stake_note = f"Stake {round(sA*100,1)}% on {{pa}} Yes + {round(sB*100,1)}% on {{pb}} No"
                else:
                    action = f"Buy Yes on {'{pb}'}, Buy No on {'{pa}'}"
                    stake_note = f"Stake {round(sB*100,1)}% on {{pb}} Yes + {round(sA*100,1)}% on {{pa}} No"
                    sA, sB = sB, sA  # keep sA = platform_a stake

                result.update({
                    "arb_type": "guaranteed",
                    "guaranteed_return_pct": round(net * 100, 2),
                    "stake_a_pct": round(sA * 100, 1),
                    "stake_b_pct": round(sB * 100, 1),
                })
                break

    # One-sided action label
    if result["arb_type"] == "one-sided":
        if prob_a > prob_b:
            result["action"] = "Buy Yes on {pb} (cheaper), sell/fade on {pa}"
        else:
            result["action"] = "Buy Yes on {pa} (cheaper), sell/fade on {pb}"

    return result


def run():
    pairs_path = PROCESSED / "matched_pairs.csv"
    if not pairs_path.exists():
        print("No matched_pairs.csv found — run scripts/matcher.py first")
        return

    df = pd.read_csv(pairs_path)
    df = df[df["implied_prob_a"].notna() & df["implied_prob_b"].notna()].copy()
    print(f"Processing {len(df)} matched pairs...")

    rows = []
    for _, r in df.iterrows():
        pa, pb = r["platform_a"], r["platform_b"]
        fee_a = FEES.get(pa, 0.05)
        fee_b = FEES.get(pb, 0.05)

        arb = compute_arb(r["implied_prob_a"], r["implied_prob_b"], fee_a, fee_b)

        # Fill in platform names in action strings
        arb["action"] = arb["action"].replace("{pa}", pa.title()).replace("{pb}", pb.title())

        raw_gap_pp = arb["raw_gap_pp"]
        # Flag gaps > 40pp as suspicious — likely a data/matching error
        suspicious = bool(raw_gap_pp is not None and raw_gap_pp > 40)

        rows.append({
            "match_type": r.get("match_type", "fuzzy"),
            "race_id": r.get("race_id", ""),
            "category": r.get("category", ""),
            "platform_a": pa,
            "platform_b": pb,
            "question_a": r.get("question_a", ""),
            "question_b": r.get("question_b", ""),
            "market_id_a": r.get("market_id_a", ""),
            "market_id_b": r.get("market_id_b", ""),
            "implied_prob_a": round(float(r["implied_prob_a"]), 4),
            "implied_prob_b": round(float(r["implied_prob_b"]), 4),
            "settle_date": str(r.get("settle_date", ""))[:10],
            "url_a": r.get("url_a", ""),
            "url_b": r.get("url_b", ""),
            "fuzzy_score": r.get("fuzzy_score", 100),
            "suspicious": suspicious,
            **arb,
        })

    result = pd.DataFrame(rows)

    # Sort: guaranteed first, then by settle_date asc (soonest), then raw_gap desc
    result["_is_guaranteed"] = (result["arb_type"] == "guaranteed").astype(int)
    result["_settle_sort"] = result["settle_date"].replace("", "9999-99-99")
    result = result.sort_values(
        ["_is_guaranteed", "_settle_sort", "raw_gap_pp"],
        ascending=[False, True, False]
    ).drop(columns=["_is_guaranteed", "_settle_sort"])

    guaranteed = result[result["arb_type"] == "guaranteed"]
    profitable = result[result["profitable_onesided"] & (result["arb_type"] == "one-sided")]
    print(f"Guaranteed arb opportunities: {len(guaranteed)}")
    print(f"Profitable one-sided: {len(profitable)}")
    print(f"Total pairs: {len(result)}")

    if not guaranteed.empty:
        print("\nTop guaranteed arb (by settle date):")
        cols = ["platform_a", "platform_b", "question_a", "settle_date", "raw_gap_pp", "guaranteed_return_pct"]
        print(guaranteed[cols].head(20).to_string(index=False))

    # Write JS
    def clean(v):
        if isinstance(v, float) and np.isnan(v):
            return None
        if pd.isna(v):
            return None
        return v

    records = [{k: clean(v) for k, v in row.items()} for row in result.to_dict(orient="records")]

    out = ROOT / "docs" / "arb_data.js"
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write("const ARB = ")
        json.dump({
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "fees": FEES,
            "total": len(records),
            "guaranteed_count": len(guaranteed),
            "races": records,
        }, f, separators=(",", ":"), default=str)
        f.write(";")
    print(f"\nWrote {len(records)} pairs to docs/arb_data.js")


if __name__ == "__main__":
    run()
