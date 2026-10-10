"""Independently re-verify every guaranteed basket against live order books.

Usage:  py tools/livecheck.py [docs/arb_data.js] [--summary FILE]

For each row with arb_type == 'guaranteed' it re-fetches both legs' books
(Kalshi YES book -> NO ask = 1 - best YES bid; Polymarket YES and NO token
books), looks up each leg's fee parameters fresh from the APIs, and recomputes
cost, fees (utils/fees.py + FEE_SAFETY_MARGIN) and return in the board's
direction (yes_leg). It prints LIVE OK / CLOSED per basket and, with --summary, appends
a Markdown table (CI passes $GITHUB_STEP_SUMMARY). Report only: always exits
0 — prices move between the scan and this check, so a CLOSED line is
information, not a failure. Moved from an ad-hoc script into the repo
2026-09-26 so the board is re-checked by CI, not just by hand.
"""
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
from scripts.fetch_depth import _kalshi_yes_book, _polymarket_yes_book  # noqa: E402
from utils.fees import leg_fee, kalshi_spec, polymarket_spec, predictit_spec, FEE_SAFETY_MARGIN  # noqa: E402

H = {"User-Agent": "Mozilla/5.0"}
_PI = None   # PredictIt contracts by id, fetched once
K = "https://api.elections.kalshi.com/trade-api/v2"


def gj(url):
    try:
        return json.loads(urllib.request.urlopen(urllib.request.Request(url, headers=H), timeout=20).read())
    except Exception:
        return {}


def load_rows(path):
    t = open(path, encoding="utf8").read()
    j = json.loads(t[t.index("{"):t.rindex("}") + 1])
    rows = j.get("races") or next(v for v in j.values() if isinstance(v, list))
    return j, rows


def leg(platform, mid, row, side):
    """(platform, yes_ask, no_ask, yes_ask_size, no_ask_size, fee_spec) or None."""
    try:
        if platform == "kalshi":
            b = _kalshi_yes_book(mid)
            m = gj(f"{K}/markets/{mid}").get("market", {})
            ser = gj(f"{K}/series/{m.get('event_ticker', '').split('-')[0]}").get("series", {})
            no_ask = (1 - b["best_bid"]) if b.get("best_bid") else None
            return (platform, b.get("best_ask"), no_ask, b.get("best_ask_size"), b.get("best_bid_size"),
                    kalshi_spec(ser.get("fee_multiplier", 1)))
        if platform == "polymarket":
            g = gj(f"https://gamma-api.polymarket.com/markets?clob_token_ids={mid}")
            g = g[0] if isinstance(g, list) and g else {}
            toks = json.loads(g.get("clobTokenIds", "[]")) if g else []
            no_tok = row.get(f"market_no_id_{side}") or (toks[1] if len(toks) > 1 else None)
            yb, nb = _polymarket_yes_book(mid), _polymarket_yes_book(no_tok)
            rate = 0.0 if g.get("feesEnabled") is False else (g.get("feeSchedule") or {}).get("rate")
            return (platform, yb.get("best_ask"), nb.get("best_ask"), yb.get("best_ask_size"),
                    nb.get("best_ask_size"), polymarket_spec(rate))
        if platform == "predictit":
            # One public call lists every contract's best Yes/No buy price
            # (no sizes: PredictIt publishes no book depth).
            global _PI
            if _PI is None:
                d = gj("https://www.predictit.org/api/marketdata/all/")
                _PI = {str(c["id"]): c for m in d.get("markets", []) for c in m.get("contracts", [])}
            c = _PI.get(str(mid))
            if not c:
                return None
            return (platform, c.get("bestBuyYesCost"), c.get("bestBuyNoCost"), None, None, predictit_spec())
    except Exception:
        return None
    return None


def check(row):
    legs = {s: leg(row[f"platform_{s}"], row.get(f"market_id_{s}"), row, s) for s in "ab"}
    best = None
    # Only the board's direction: the reverse can be unsafe even when it
    # prices better (crypto basis risk, narrower YES window).
    r_ys = row.get("yes_leg")
    if r_ys in ("a", "b"):
        dirs = [(r_ys, "b" if r_ys == "a" else "a")]
    else:
        dirs = [("a", "b"), ("b", "a")]
    for ys, ns in dirs:
        y, n = legs[ys], legs[ns]
        if not y or not n or not y[1] or not n[2]:
            continue
        cost = y[1] + n[2]
        fees = leg_fee(y[0], y[5], y[1]) + leg_fee(n[0], n[5], n[2]) + FEE_SAFETY_MARGIN
        net = 1 - cost - fees
        size = None if y[3] is None or n[4] is None else min(y[3], n[4])
        if best is None or net > best["net"]:
            best = dict(net=net, cost=cost, fees=fees, yes=y[0], size=size)
    return best


def main():
    args = sys.argv[1:]
    summary = None
    if "--summary" in args:
        i = args.index("--summary"); summary = args[i + 1]; del args[i:i + 2]
    path = args[0] if args else str(ROOT / "docs" / "arb_data.js")
    meta, rows = load_rows(path)
    g = [r for r in rows if r.get("arb_type") == "guaranteed"]
    lines, ok = [], 0
    for r in g:
        b = check(r)
        q = (r.get("question_a") or r.get("label") or "")[:70]
        if b is None:
            status, live = "NO BOOK", ""
        else:
            live_pct = 100 * b["net"] / b["cost"]
            status = "LIVE OK" if b["net"] / b["cost"] >= 0.0025 else "CLOSED"
            ok += status == "LIVE OK"
            live = f"{live_pct:.2f}% (cost {b['cost']:.3f}, fees {100 * b['fees']:.2f}c, {'size n/a' if b['size'] is None else f"{b['size']:.0f} contracts"}, YES on {b['yes']})"
        print(f"{status:8} board {r.get('guaranteed_return_pct')}%  live {live} | {q}")
        lines.append(f"| {status} | {r.get('guaranteed_return_pct')}% | {live} | {q.replace('|', '/')} |")
    head = f"Live re-check of {len(g)} guaranteed baskets (board {meta.get('updated_at', '?')}): {ok} still open"
    print(head)
    if summary:
        with open(summary, "a", encoding="utf8") as f:
            f.write(f"\n### {head}\n\n| status | board | live | basket |\n|---|---|---|---|\n")
            f.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
