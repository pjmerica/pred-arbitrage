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
    # Conservative round-trip fee approximation per platform. Kept in
    # lockstep with polling-agg's FEES dict.
    # Kalshi taker fee tops out around 1% each way; Polymarket gas + fee
    # tops out around 1% each way; 2% per platform leaves a cushion
    # against slippage without burying real arbs under fake-fee math.
    # PredictIt is 5% on profits + 5% on withdrawals = 12% effective.
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
        "stake_a_dollars": None,
        "stake_b_dollars": None,
        "profit_dollars": None,
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
                    # Dollar amounts for a $100 total stake
                    "stake_a_dollars": round(sA * 100, 2),
                    "stake_b_dollars": round(sB * 100, 2),
                    "profit_dollars": round(net * 100, 2),
                })
                break

    # One-sided action label
    if result["arb_type"] == "one-sided":
        if prob_a > prob_b:
            result["action"] = "Buy Yes on {pb} (cheaper), sell/fade on {pa}"
        else:
            result["action"] = "Buy Yes on {pa} (cheaper), sell/fade on {pb}"

    return result


def _assert_scrape_freshness():
    """Fail loudly if any platform's raw CSV is more than MAX_AGE_HOURS old.

    Catches the silent-staleness failure mode where a scraper succeeds
    structurally (no exit code) but the CSV it produced is from a
    previous run because it didn't actually write new data. We had a
    real incident on 2026-06-21 where Polymarket's CSV was 44 days old
    while every daily refresh reported success. This guard fires before
    arb_scanner does its work so the arb_data.js gets rewritten only
    when the inputs are actually fresh.
    """
    MAX_AGE_HOURS = 12
    raw = ROOT / "data" / "raw"
    issues = []
    for name in ("kalshi_markets.csv", "polymarket_markets.csv", "predictit_markets.csv"):
        path = raw / name
        if not path.exists():
            issues.append(f"{name}: missing")
            continue
        try:
            df = pd.read_csv(path, nrows=1, dtype={"yes_token_id": str, "no_token_id": str})
        except Exception as e:
            issues.append(f"{name}: unreadable ({e})")
            continue
        if "fetched_at" not in df.columns:
            issues.append(f"{name}: no fetched_at column")
            continue
        ts = pd.to_datetime(df["fetched_at"].iloc[0], errors="coerce", utc=True)
        if pd.isna(ts):
            issues.append(f"{name}: unparseable fetched_at")
            continue
        age_h = (datetime.now(timezone.utc) - ts.to_pydatetime()).total_seconds() / 3600
        if age_h > MAX_AGE_HOURS:
            issues.append(f"{name}: stale by {age_h:.1f}h (max {MAX_AGE_HOURS}h)")
    if issues:
        print("FRESHNESS CHECK FAILED - refusing to write arb_data.js:")
        for issue in issues:
            print(f"  - {issue}")
        raise SystemExit(
            "One or more scraper CSVs are stale or missing. The cron will retry; "
            "live dashboard keeps the last good snapshot."
        )


def run():
    _assert_scrape_freshness()

    pairs_path = PROCESSED / "matched_pairs.csv"
    if not pairs_path.exists():
        print("No matched_pairs.csv found — run scripts/matcher.py first")
        return

    # Polymarket market_ids are 78-digit token ids — must read as str or
    # pandas corrupts them to float (scientific notation), breaking depth lookup.
    df = pd.read_csv(pairs_path, dtype={"market_id_a": str, "market_id_b": str})
    df = df[df["implied_prob_a"].notna() & df["implied_prob_b"].notna()].copy()
    print(f"Processing {len(df)} matched pairs...")

    # Volume is exposed in the JSON so the dashboard can filter live.
    df["volume_a"] = pd.to_numeric(df.get("volume_a"), errors="coerce")
    df["volume_b"] = pd.to_numeric(df.get("volume_b"), errors="coerce")

    rows = []
    for _, r in df.iterrows():
        pa, pb = r["platform_a"], r["platform_b"]
        fee_a = FEES.get(pa, 0.05)
        fee_b = FEES.get(pb, 0.05)

        arb = compute_arb(r["implied_prob_a"], r["implied_prob_b"], fee_a, fee_b)

        # Fill in platform names in action strings
        arb["action"] = arb["action"].replace("{pa}", pa.title()).replace("{pb}", pb.title())

        raw_gap_pp = arb["raw_gap_pp"]
        # Flag gaps > 20pp as suspicious — large gaps usually indicate a
        # scrape staleness, mismatched outcome, or broken book somewhere.
        # Real arbs typically have gaps under 10pp.
        suspicious = bool(raw_gap_pp is not None and raw_gap_pp > 20)

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
            "volume_a": None if pd.isna(r.get("volume_a")) else round(float(r.get("volume_a")), 2),
            "volume_b": None if pd.isna(r.get("volume_b")) else round(float(r.get("volume_b")), 2),
            "fuzzy_score": r.get("fuzzy_score", 100),
            "suspicious": suspicious,
            **arb,
        })

    result = pd.DataFrame(rows)

    # ── Append 2026 US-election rows produced by scripts/elections.py ──
    # The election module runs before the scanner (see run_all.py) and
    # writes data/processed/election_pairs.csv with pred-arb-shaped rows
    # (same column names as `rows` above, plus a match_type tag of
    # 'general' / 'general_candidate' / 'primary_candidate' and
    # category='Elections'). We just concat — no dedup, since duplicates
    # with the fuzzy matcher are acceptable today (user decision
    # 2026-06-21). Missing file = elections step didn't run, treated as
    # zero rows so the rest of the scanner survives.
    election_path = PROCESSED / "election_pairs.csv"
    if election_path.exists():
        try:
            elec = pd.read_csv(election_path,
                               dtype={"market_id_a": str, "market_id_b": str})
            if not elec.empty:
                # Align column set with `result` so concat doesn't widen
                # unexpectedly. Missing columns become NaN; extras get dropped.
                for col in result.columns:
                    if col not in elec.columns:
                        elec[col] = None
                elec = elec[list(result.columns)]
                result = pd.concat([result, elec], ignore_index=True)
                print(f"Appended {len(elec)} election pairs from election_pairs.csv")
        except pd.errors.EmptyDataError:
            print("election_pairs.csv is empty — skipping append")
    else:
        print("No election_pairs.csv — skipping (run scripts/elections.py first)")

    # ── Tag every row with a top-level category for the dashboard tabs ──
    # 'Elections' rows come from scripts/elections.py with the tag
    # already set. Fuzzy-matcher rows have whatever the matcher put in
    # the category column (e.g. 'Sports', 'Politics', 'Crypto'). For
    # the new tab layout we collapse these into three buckets:
    #   Elections — explicit category=='Elections' (only from elections.py)
    #   Sports    — anything the matcher tagged 'Sports'
    #   Other     — everything else
    # The 'All' tab shows every row regardless.
    def _bucket(cat):
        if not isinstance(cat, str):
            return "Other"
        c = cat.strip().lower()
        if c == "elections":
            return "Elections"
        if c == "sports":
            return "Sports"
        return "Other"
    result["category_bucket"] = result["category"].apply(_bucket)

    # Emit depth_targets.csv for fetch_depth.py. Only kalshi/polymarket
    # markets have queryable orderbooks.
    targets = []
    for _, r in result.iterrows():
        for side in ("a", "b"):
            plat = r.get(f"platform_{side}")
            mid = r.get(f"market_id_{side}")
            if plat in ("kalshi", "polymarket") and mid and not (isinstance(mid, float) and pd.isna(mid)):
                targets.append({"platform": plat, "market_id": str(mid)})
    if targets:
        tdf = pd.DataFrame(targets).drop_duplicates(subset=["platform", "market_id"])
        tdf["market_id"] = tdf["market_id"].astype(str)
        tpath = PROCESSED / "depth_targets.csv"
        tdf.to_csv(tpath, index=False)
        print(f"Emitted {len(tdf)} unique depth targets to {tpath}")

    # Join orderbook depth if fetch_depth.py has already run.
    depth_path = ROOT / "data" / "raw" / "orderbook_depth.csv"
    if depth_path.exists():
        depth = pd.read_csv(depth_path, dtype={"market_id": str})
        depth_cols = ["best_bid", "best_ask", "best_bid_size", "best_ask_size",
                      "depth_bid_at_1pp", "depth_ask_at_1pp", "max_buy_size_at_3pp_edge"]
        result["market_id_a"] = result["market_id_a"].astype(str)
        result["market_id_b"] = result["market_id_b"].astype(str)
        for side in ("a", "b"):
            d = depth[["platform", "market_id"] + depth_cols].rename(
                columns={"platform": f"platform_{side}",
                         "market_id": f"market_id_{side}",
                         **{c: f"depth_{side}_{c}" for c in depth_cols}}
            )
            result = result.merge(d, on=[f"platform_{side}", f"market_id_{side}"], how="left")
        # Short alias used by the dashboard
        result["depth_a_max_at_3pp"] = result.get("depth_a_max_buy_size_at_3pp_edge")
        result["depth_b_max_at_3pp"] = result.get("depth_b_max_buy_size_at_3pp_edge")
        joined = result["depth_a_max_at_3pp"].notna().sum()
        print(f"Joined depth onto {joined}/{len(result)} pairs (A side)")

        # When live orderbook depth is available, override the implied_prob
        # we got from the scraper with the depth-time midpoint. The depth
        # fetch runs minutes AFTER the scrape and hits the live CLOB book
        # directly (not the gamma snapshot for Polymarket, not a cached
        # market summary for Kalshi). For markets that move during a refresh
        # cycle, the depth-time price is materially fresher.
        #
        # Why this matters: Polymarket's gamma /markets endpoint reports
        # bid/ask that can lag the live CLOB by minutes to hours on
        # low-volume markets. Example incident (2026-06-21): the NZ
        # recognizes-Palestine market scraped at midpoint=24% (gamma:
        # bb=0.16 / ba=0.34) and shipped as a 20pp arb vs Kalshi's 14%.
        # Live depth at the same instant showed bb=0.16 / ba=0.24, midpoint
        # 20%. The arb was 6pp not 20pp. This override would have surfaced
        # the right number; the scraper-side wide-spread drop catches it
        # too but only if the spread is also wide AT scrape time.
        #
        # We only override when BOTH depth_best_bid and depth_best_ask are
        # present and form a sane two-sided book; missing/NaN depth keeps
        # the scraper price. Recompute raw_gap / net_gap / arb_type / etc.
        # afterward since the price changed.
        def _depth_mid(row, side):
            bb = row.get(f"depth_{side}_best_bid")
            ba = row.get(f"depth_{side}_best_ask")
            if pd.isna(bb) or pd.isna(ba) or bb is None or ba is None:
                return None
            try:
                bb, ba = float(bb), float(ba)
            except (TypeError, ValueError):
                return None
            if not (0 < bb <= ba < 1):
                return None
            return round((bb + ba) / 2, 4)
        n_overridden = 0
        for idx, row in result.iterrows():
            ma = _depth_mid(row, "a")
            mb = _depth_mid(row, "b")
            if ma is not None and ma != row.get("implied_prob_a"):
                result.at[idx, "implied_prob_a"] = ma
                n_overridden += 1
            if mb is not None and mb != row.get("implied_prob_b"):
                result.at[idx, "implied_prob_b"] = mb
        if n_overridden:
            print(f"Overrode {n_overridden} prices with live depth midpoints")
            # Recompute arb math on the new prices.
            for idx, row in result.iterrows():
                pa = row.get("implied_prob_a")
                pb = row.get("implied_prob_b")
                if pd.isna(pa) or pd.isna(pb):
                    continue
                fa = FEES.get(row.get("platform_a"), 0.05)
                fb = FEES.get(row.get("platform_b"), 0.05)
                arb = compute_arb(pa, pb, fa, fb)
                arb["action"] = arb["action"].replace("{pa}", row.get("platform_a", "").title()) \
                                              .replace("{pb}", row.get("platform_b", "").title())
                for k, v in arb.items():
                    result.at[idx, k] = v
                # Suspicious flag was based on raw_gap > 20; the post-override
                # value already lives in arb["raw_gap_pp"], so the flag in the
                # row dict is stale. Re-stamp it.
                rg = arb.get("raw_gap_pp")
                result.at[idx, "suspicious"] = bool(rg is not None and rg > 20)

        # Defense in depth: even if a scraper missed a wide-spread market
        # at scrape time, the fetch_depth pull is fresh. Drop any pair
        # where the depth-derived spread on EITHER side exceeds 25pp —
        # those quotes can't be filled at the implied midpoint price.
        def wide(row, side):
            bb = row.get(f"depth_{side}_best_bid")
            ba = row.get(f"depth_{side}_best_ask")
            if pd.isna(bb) or pd.isna(ba) or bb is None or ba is None:
                return False
            return (ba - bb) > 0.25
        before = len(result)
        result = result[~result.apply(lambda r: wide(r, "a") or wide(r, "b"), axis=1)]
        if before != len(result):
            print(f"Dropped {before - len(result)} pairs with wide depth-derived spread (>25pp)")

        # Drop pairs where the live orderbook on either side has no bid.
        # The scraper already filters these out at scrape time, but a
        # market can go one-sided in the minutes between scrape and the
        # fetch_depth pull — catch that here. User explicitly asked for
        # "only live props" (2026-06-21): if you can't exit, it's not
        # really a tradeable position.
        # Note: the depth columns are NaN for any pair we didn't fetch
        # depth on (PredictIt legs, markets that 404'd, etc.). Those
        # stay in — we don't drop on missing data, only on confirmed
        # missing bid.
        def one_sided(row, side):
            bb = row.get(f"depth_{side}_best_bid")
            ba = row.get(f"depth_{side}_best_ask")
            return pd.notna(ba) and (pd.isna(bb) or bb is None or bb <= 0)
        before = len(result)
        result = result[~result.apply(lambda r: one_sided(r, "a") or one_sided(r, "b"), axis=1)]
        if before != len(result):
            print(f"Dropped {before - len(result)} pairs with one-sided orderbook (no bid - can't exit)")

        # Compute suspicion_reasons. >20pp gap, wide depth spread, thin
        # depth all warrant manual verification. (one_sided is no longer
        # a suspicion code — pairs that fail it are dropped above.)
        def reasons(row):
            rs = []
            if (row.get("raw_gap_pp") or 0) > 20:
                rs.append("wide_gap")
            for side in ("a", "b"):
                bb = row.get(f"depth_{side}_best_bid")
                ba = row.get(f"depth_{side}_best_ask")
                if pd.notna(bb) and pd.notna(ba) and (ba - bb) > 0.15:
                    rs.append(f"wide_spread_{side}")
                m3 = row.get(f"depth_{side}_max_at_3pp")
                if pd.notna(m3) and m3 < 20:
                    rs.append(f"thin_depth_{side}")
            return rs
        result["suspicion_reasons"] = result.apply(reasons, axis=1)
        result["suspicious"] = result["suspicion_reasons"].apply(lambda rs: len(rs) > 0)

        # Scrutinize >30pp pairs by fetching each market's resolution rules
        # and comparing. Pairs where the rules diverge (low similarity)
        # describe different outcomes — they're not real arbs, just markets
        # with similar-sounding questions.
        try:
            from scripts.scrutiny import scrutinize as _scrutinize
        except ImportError:
            try:
                import sys as _sys
                _sys.path.insert(0, str(ROOT))
                from scripts.scrutiny import scrutinize as _scrutinize
            except Exception:
                _scrutinize = None
        if _scrutinize is not None:
            scrut = _scrutinize(result.to_dict(orient="records"), threshold_pp=30)
            def apply_scrut(row):
                k = (str(row.get("market_id_a")), str(row.get("market_id_b")))
                return scrut.get(k)
            result["_scrut"] = result.apply(apply_scrut, axis=1)
            # Drop pairs marked 'drop'
            drop_mask = result["_scrut"].apply(lambda s: bool(s) and s.get("action") == "drop")
            n_drop = int(drop_mask.sum())
            if n_drop:
                print(f"Dropped {n_drop} pairs after rules-text scrutiny (criteria_score < {50})")
            result = result[~drop_mask].copy()
            # Append warn reason + score for the survivors
            def merge_scrut(row):
                s = row.get("_scrut")
                rs = list(row.get("suspicion_reasons") or [])
                if s and s.get("action") == "warn":
                    rs.append(f"criteria_warn:{s.get('reason')}")
                return rs
            result["suspicion_reasons"] = result.apply(merge_scrut, axis=1)
            result["criteria_score"] = result["_scrut"].apply(lambda s: s.get("criteria_score") if s else None)
            result["suspicious"] = result["suspicion_reasons"].apply(lambda rs: len(rs) > 0)
            result = result.drop(columns=["_scrut"])

    # Drop pairs whose settle_date is in the past. Upstream APIs sometimes
    # keep already-resolved markets in their "active" feed for a few days
    # (Kalshi event-of-the-week markets, Polymarket weekly Netflix charts);
    # those pairs can't be traded and just clutter the dashboard. Rows
    # with no settle_date or with sentinel '9999-99-99' are KEPT so we
    # don't silently lose rows that are tradeable but date-less.
    today_iso = datetime.now(timezone.utc).date().isoformat()
    def _is_past(s):
        if not isinstance(s, str) or s in ("", "9999-99-99"):
            return False
        return s < today_iso
    before = len(result)
    result = result[~result["settle_date"].apply(_is_past)].copy()
    dropped_past = before - len(result)
    if dropped_past:
        print(f"Dropped {dropped_past} pairs with settle_date in the past (today is {today_iso})")

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
        if isinstance(v, list):
            return v  # lists (e.g. suspicion_reasons) are valid JSON, pass through
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
