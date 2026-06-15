"""
Deep check of pred-arbitrage's top arbs.

For each candidate arb (sorted by guaranteed_return desc):
  1. Fetch full orderbooks on both platforms (Kalshi + Polymarket).
  2. For three slippage budgets (0pp, 1pp, 3pp), compute the maximum
     basket size where the cross-platform arb still nets > 0 after fees.
  3. Print question text on both sides so we can spot-check alignment.
  4. Categorize: REAL (net > 0 at >= 50 contracts within 1pp), THIN
     (net > 0 only at zero slippage or tiny size), GONE (live closes
     the gap), WRONG_PAIR (questions describe different outcomes).
"""

import json
import re
import sys
import time
import urllib.request
import urllib.parse
from pathlib import Path

# Force UTF-8 stdout so emoji/arrows in question text don't crash Windows cp1252.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ARB_PATH = Path(__file__).parent.parent / "docs" / "arb_data.js"

KALSHI_OB = "https://api.elections.kalshi.com/trade-api/v2/markets/{t}/orderbook?depth=50"
POLY_OB   = "https://clob.polymarket.com/book?token_id={t}"
HEADERS   = {"User-Agent": "Mozilla/5.0 (deep-audit)", "Accept": "application/json"}
FEES      = {"kalshi": 0.03, "polymarket": 0.03, "predictit": 0.12}

TOP_N = 50


def http_json(url, timeout=12):
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except Exception as e:
        return {"_error": str(e)}


def kalshi_book(ticker):
    """Returns (yes_asks, no_asks): two sorted-ascending lists of (price, size).
    yes_asks[i] = (price, size) you can BUY YES at this price (cheapest first)
    no_asks[i] = (price, size) you can BUY NO at this price (cheapest first)
    """
    if not ticker:
        return [], []
    d = http_json(KALSHI_OB.format(t=urllib.parse.quote(str(ticker))))
    if "_error" in d:
        return [], []
    ob = d.get("orderbook_fp") or d.get("orderbook") or {}
    # yes_dollars = bids on YES (someone wants to BUY yes at p) — by HITTING
    # them I sell YES = buy NO at (1 - p). So yes_dollars become NO-asks at (1-p).
    # no_dollars = bids on NO. By hitting them I sell NO = buy YES at (1 - p).
    yes_buy_orders = sorted([(float(p), float(s)) for p, s in (ob.get("yes_dollars") or [])])
    no_buy_orders  = sorted([(float(p), float(s)) for p, s in (ob.get("no_dollars")  or [])])
    # YES asks: derived from no_dollars (NO buyers), priced ascending in YES terms
    yes_asks = sorted([(round(1 - p, 4), s) for p, s in no_buy_orders])
    no_asks  = sorted([(round(1 - p, 4), s) for p, s in yes_buy_orders])
    return yes_asks, no_asks


def poly_book(token_id):
    """Returns (yes_asks, no_asks_via_yes_bid)."""
    if not token_id:
        return [], []
    d = http_json(POLY_OB.format(t=str(token_id)))
    if "_error" in d:
        return [], []
    asks_raw = d.get("asks") or []
    bids_raw = d.get("bids") or []
    yes_asks = sorted([(float(a["price"]), float(a["size"])) for a in asks_raw])
    # BUY NO on polymarket = sell YES = hit a YES bid. The price you pay for NO
    # is (1 - yes_bid_price). The size available is the yes_bid size.
    no_asks_synthetic = sorted([(round(1 - float(b["price"]), 4), float(b["size"])) for b in bids_raw])
    return yes_asks, no_asks_synthetic


def fetch_books(platform, market_id):
    if platform == "kalshi":
        return kalshi_book(market_id)
    if platform == "polymarket":
        return poly_book(market_id)
    return [], []


def basket_at_slippage(yes_asks_combined, no_asks_combined, slip_pp, fees):
    """
    yes_asks_combined: list of (price, size, platform) for buying YES
    no_asks_combined: list of (price, size, platform) for buying NO
    slip_pp: max pp above the best price we'll pay
    Returns (max_size, gross_per_unit, total_fees_per_unit, net_per_unit, leg_descriptions)
    where size is how many YES+NO baskets we could fill, requiring legs
    on DIFFERENT platforms (so it's a true cross-platform arb).
    """
    if not yes_asks_combined or not no_asks_combined:
        return None
    # We need YES from one platform + NO from a DIFFERENT platform.
    # For each (yes_platform, no_platform) cross-platform pair, walk both
    # orderbooks in parallel, accumulating size until either side runs out
    # OR the marginal price > best_price + slip_pp.
    best = None
    plats = set(p for _, _, p in yes_asks_combined) | set(p for _, _, p in no_asks_combined)
    for yp in plats:
        for np in plats:
            if yp == np:
                continue
            yes_legs = sorted([(p, s) for p, s, pl in yes_asks_combined if pl == yp])
            no_legs  = sorted([(p, s) for p, s, pl in no_asks_combined  if pl == np])
            if not yes_legs or not no_legs:
                continue
            yes_cap = yes_legs[0][0] + slip_pp / 100
            no_cap  = no_legs[0][0]  + slip_pp / 100
            yes_total_size = sum(s for p, s in yes_legs if p <= yes_cap + 1e-9)
            no_total_size  = sum(s for p, s in no_legs  if p <= no_cap  + 1e-9)
            max_size = min(yes_total_size, no_total_size)
            if max_size <= 0:
                continue
            # weighted-avg fill price per side
            def weighted_fill(legs, cap, target_size):
                cost = 0; got = 0
                for p, s in legs:
                    if p > cap + 1e-9: break
                    take = min(s, target_size - got)
                    cost += take * p; got += take
                    if got >= target_size: break
                return cost / got if got > 0 else None, got
            yes_avg, _ = weighted_fill(yes_legs, yes_cap, max_size)
            no_avg, _  = weighted_fill(no_legs, no_cap, max_size)
            if yes_avg is None or no_avg is None:
                continue
            cost_per_unit = yes_avg + no_avg
            gross = 1 - cost_per_unit
            fee = fees.get(yp, 0.05) + fees.get(np, 0.05)
            net = gross - fee
            if best is None or net * max_size > best["edge_dollars"]:
                best = {
                    "max_size": round(max_size, 2),
                    "gross_pp": round(gross * 100, 2),
                    "fee_pp": round(fee * 100, 2),
                    "net_pp": round(net * 100, 2),
                    "edge_dollars": round(net * max_size, 2),
                    "yes_avg": round(yes_avg, 4), "yes_plat": yp,
                    "no_avg": round(no_avg, 4), "no_plat": np,
                }
    return best


def load_arb(path):
    txt = path.read_text(encoding="utf-8")
    m = re.search(r"=\s*(\{.*\});?\s*$", txt, re.DOTALL) or re.search(r"=\s*(\{.*\})", txt, re.DOTALL)
    return json.loads(m.group(1))


def main():
    d = load_arb(ARB_PATH)
    races = [r for r in d.get("races", []) if r.get("implied_prob_a") is not None
             and r.get("implied_prob_b") is not None]
    races.sort(key=lambda r: -(r.get("guaranteed_return_pct") or r.get("net_gap_pp") or 0))

    print(f"Auditing top {TOP_N} of {len(races)} pred-arb candidates")
    print("=" * 100)

    summary = {"REAL": 0, "MARGINAL": 0, "THIN": 0, "GONE": 0, "ONE_PLATFORM": 0,
               "PREDICTIT_LEG": 0, "WRONG_PAIR": 0}

    real_arbs = []  # collect REAL ones for end-of-report

    for i, r in enumerate(races[:TOP_N]):
        pa, pb = r["platform_a"], r["platform_b"]
        qa, qb = r.get("question_a") or "(no q)", r.get("question_b") or "(no q)"
        mid_a, mid_b = r.get("market_id_a"), r.get("market_id_b")
        snap_a, snap_b = r.get("implied_prob_a"), r.get("implied_prob_b")

        print(f"\n[{i+1:2d}] {pa}/{pb}  scanner says raw={r.get('raw_gap_pp')}pp net={r.get('net_gap_pp')}pp guar={r.get('guaranteed_return_pct')}%")
        print(f"     A: {qa[:95]}")
        print(f"     B: {qb[:95]}")

        # Skip if either side is predictit (can't fetch)
        if pa == "predictit" or pb == "predictit":
            print(f"     SKIP — predictit leg, no public orderbook")
            summary["PREDICTIT_LEG"] += 1
            continue

        # Fetch both books
        a_yes, a_no = fetch_books(pa, mid_a)
        b_yes, b_no = fetch_books(pb, mid_b)

        # Build combined asks tagged by platform
        yes_combined = [(p, s, pa) for p, s in a_yes] + [(p, s, pb) for p, s in b_yes]
        no_combined  = [(p, s, pa) for p, s in a_no]  + [(p, s, pb) for p, s in b_no]

        if not (a_yes or a_no) or not (b_yes or b_no):
            print(f"     ONE_PLATFORM — could not fetch one side")
            summary["ONE_PLATFORM"] += 1
            continue

        # Spot-check: do the questions describe the same thing?
        # Quick heuristic: candidate name or month must overlap
        flag = ""
        # Simple "two named candidates that differ" check
        def cap_words(q):
            return set(re.findall(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2}\b", q or ""))
        # not perfect but flags obvious mismatches
        names_a = cap_words(qa)
        names_b = cap_words(qb)

        for slip in (0, 1, 3):
            best = basket_at_slippage(yes_combined, no_combined, slip, FEES)
            if best is None:
                print(f"     {slip}pp slip: no cross-platform basket")
                continue
            tag = " <- REAL" if best["net_pp"] > 0 and best["max_size"] >= 50 and slip <= 1 else \
                  " <- thin" if best["net_pp"] > 0 and best["max_size"] >= 10 else \
                  " <- marginal" if best["net_pp"] > 0 else " (no edge)"
            print(f"     {slip}pp slip: net {best['net_pp']:+.1f}pp on {best['max_size']:.0f} contracts "
                  f"(buy YES {best['yes_plat']} @ {best['yes_avg']}, NO {best['no_plat']} @ {best['no_avg']}){tag}")

        # Categorize using the 1pp result
        b1 = basket_at_slippage(yes_combined, no_combined, 1, FEES)
        if b1 is None or b1["net_pp"] <= 0:
            summary["GONE"] += 1
        elif b1["max_size"] >= 50:
            summary["REAL"] += 1
            real_arbs.append((i+1, qa[:60], b1, r.get("url_a"), r.get("url_b")))
        elif b1["max_size"] >= 10:
            summary["MARGINAL"] += 1
        else:
            summary["THIN"] += 1

        time.sleep(0.05)  # be nice to APIs

    print("\n" + "=" * 100)
    print("SUMMARY")
    print("=" * 100)
    for k in ["REAL", "MARGINAL", "THIN", "GONE", "ONE_PLATFORM", "PREDICTIT_LEG", "WRONG_PAIR"]:
        print(f"  {k:18s} {summary[k]}")

    if real_arbs:
        print("\nREAL ARBS (>= 50 contracts within 1pp slippage):")
        print("-" * 100)
        for rank, q, b, ua, ub in real_arbs:
            print(f"  #{rank} {q}")
            print(f"      net +{b['net_pp']:.1f}pp on {b['max_size']:.0f} contracts → ${b['edge_dollars']:.2f} edge")
            print(f"      buy YES {b['yes_plat']} @ {b['yes_avg']} + NO {b['no_plat']} @ {b['no_avg']}")
            if ua: print(f"      A: {ua}")
            if ub: print(f"      B: {ub}")


if __name__ == "__main__":
    main()
