"""Verify (and optionally repair) every market link on the board.

Usage:  py tools/linkcheck.py [docs/arb_data.js] [--pages] [--fix] [--summary FILE]

For each row it rebuilds the URL each leg SHOULD have from the platform's
own API, keyed by the ID we actually priced, and compares it with the
published link:

  Kalshi      ticker -> /markets?tickers=… (batch) -> event_ticker ->
              /events/{event} -> series_ticker. Expected:
              kalshi.com/markets/{series}/{event} (lowercase).
  Polymarket  YES token -> gamma /markets?clob_token_ids=… (batch) ->
              market slug + parent event slug. Expected:
              polymarket.com/event/{event}/{market} (utils.links).
  PredictIt   contract id -> /api/marketdata/all -> market id. Expected:
              predictit.org/markets/detail/{market}.

--pages also fetches each Polymarket link of a guaranteed/unverified row
and checks the page's og:title is that market's question (Kalshi pages
render client-side with a generic title, so only the API check applies).

--fix rewrites any wrong link in the board file to the expected one (the
IDs are what we priced, so the API-derived URL is the right one). CI runs
this before publishing, so a link-format change on either platform repairs
itself instead of shipping links to the wrong prop. Report-only otherwise.
Exits 0 unless the board can't be read; problems go to stdout / the job
summary. Added 2026-10-10.
"""
import html
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
from utils.links import polymarket_url  # noqa: E402

H = {"User-Agent": "Mozilla/5.0"}
K = "https://api.elections.kalshi.com/trade-api/v2"


def gj(url, tries=4):
    for i in range(tries):
        try:
            return json.loads(urllib.request.urlopen(urllib.request.Request(url, headers=H), timeout=30).read())
        except urllib.error.HTTPError as e:
            if e.code == 429 and i < tries - 1:
                time.sleep(2 * (i + 1)); continue
            return None
        except Exception:
            if i < tries - 1:
                time.sleep(1); continue
            return None


def chunks(xs, n):
    xs = list(xs)
    for i in range(0, len(xs), n):
        yield xs[i:i + n]


def kalshi_expected(tickers):
    """{ticker: expected url} (None when the API doesn't know the ticker)."""
    ev_of = {}
    for ch in chunks(sorted(tickers), 50):
        d = gj(f"{K}/markets?" + urllib.parse.urlencode({"tickers": ",".join(ch), "limit": 1000})) or {}
        for m in d.get("markets", []):
            ev_of[m["ticker"]] = m.get("event_ticker")
    series_of = {}
    for ev in sorted({e for e in ev_of.values() if e}):
        d = gj(f"{K}/events/{ev}") or {}
        series_of[ev] = (d.get("event") or {}).get("series_ticker")
        time.sleep(0.06)   # stay well under ~20 req/s
    out = {}
    for t in tickers:
        ev = ev_of.get(t)
        ser = series_of.get(ev)
        out[t] = f"https://kalshi.com/markets/{ser.lower()}/{ev.lower()}" if ev and ser else None
    return out


def polymarket_expected(tokens):
    """{token: (expected url, question)}."""
    out = {}
    for ch in chunks(sorted(tokens), 50):
        d = gj("https://gamma-api.polymarket.com/markets?" + urllib.parse.urlencode(
            [("clob_token_ids", t) for t in ch] + [("limit", 500)])) or []
        for m in d:
            try:
                toks = json.loads(m.get("clobTokenIds") or "[]")
            except ValueError:
                toks = []
            ev = ((m.get("events") or [{}])[0] or {}).get("slug")
            for t in toks:
                if t in tokens:
                    out[t] = (polymarket_url(ev, m.get("slug")), m.get("question"))
    return out


_PI_NAMES = {}   # PredictIt market id -> market name (for legs without a contract id)


def predictit_expected(contract_ids):
    d = gj("https://www.predictit.org/api/marketdata/all/") or {}
    _PI_NAMES.update({str(m["id"]): m.get("name", "") for m in d.get("markets", [])})
    mk = {str(c["id"]): m["id"] for m in d.get("markets", []) for c in m.get("contracts", [])}
    return {c: (f"https://www.predictit.org/markets/detail/{mk[c]}" if c in mk else None) for c in contract_ids}


def predictit_by_name(url, question):
    """For a PredictIt leg with no contract id (polling-agg rows): the URL's
    market must exist and its name must be the question's market part
    ("Which party will win ...? — Democratic" -> before the dash)."""
    m = re.search(r"/markets/detail/(\d+)", str(url or ""))
    if not m or m.group(1) not in _PI_NAMES:
        return None
    name = _PI_NAMES[m.group(1)].strip().lower()
    q = re.split(r"\s+[—–]\s+", str(question or ""))[0].strip().lower()
    return url if name == q else f"(PredictIt market {m.group(1)} is {_PI_NAMES[m.group(1)]!r})"


def norm(u):
    return (u or "").strip().rstrip("/").lower()


def page_title_ok(url, question):
    try:
        t = urllib.request.urlopen(urllib.request.Request(url, headers=H), timeout=30).read().decode("utf8", "ignore")
    except Exception as e:
        return False, f"fetch failed ({e})"
    m = re.search(r'og:title" content="([^"]*)"', t)
    og = html.unescape(m.group(1)).strip() if m else ""
    q = (question or "").strip()
    return (og == q), og


def main():
    args = sys.argv[1:]
    fix, pages = "--fix" in args, "--pages" in args
    summary = None
    if "--summary" in args:
        i = args.index("--summary"); summary = args[i + 1]; del args[i:i + 2]
    args = [a for a in args if not a.startswith("--")]
    path = Path(args[0] if args else ROOT / "docs" / "arb_data.js")
    raw = path.read_text(encoding="utf8")
    lo, hi = raw.index("{"), raw.rindex("}") + 1
    doc = json.loads(raw[lo:hi])
    rows = doc.get("races") or next(v for v in doc.values() if isinstance(v, list))

    ids = {"kalshi": set(), "polymarket": set(), "predictit": set()}
    for r in rows:
        for s in "ab":
            p, mid = r.get(f"platform_{s}"), r.get(f"market_id_{s}")
            if p in ids and mid:
                ids[p].add(str(mid))
    exp_k = kalshi_expected(ids["kalshi"])
    exp_p = polymarket_expected(ids["polymarket"])
    exp_i = predictit_expected(ids["predictit"])

    checked = wrong = fixed = unknown = 0
    lines = []
    for r in rows:
        for s in "ab":
            p, mid, url = r.get(f"platform_{s}"), str(r.get(f"market_id_{s}") or ""), r.get(f"url_{s}")
            if p == "kalshi":
                want = exp_k.get(mid)
            elif p == "polymarket":
                want = (exp_p.get(mid) or (None, None))[0]
            elif p == "predictit":
                want = exp_i.get(mid) if mid not in ("", "None", "nan") else predictit_by_name(url, r.get(f"question_{s}"))
            else:
                continue
            checked += 1
            if want is None:
                unknown += 1     # market gone from the API (settled / delisted): leave as is
                continue
            if norm(url) != norm(want):
                wrong += 1
                lines.append(f"| {r.get('arb_type')} | {p} | {mid[:24]} | {url} | {want} |")
                if fix and want.startswith("http"):
                    r[f"url_{s}"] = want; fixed += 1
    page_bad = []
    if pages:
        for r in rows:
            if r.get("arb_type") not in ("guaranteed", "unverified"):
                continue
            for s in "ab":
                if r.get(f"platform_{s}") != "polymarket":
                    continue
                tok = str(r.get(f"market_id_{s}"))
                url, q = r.get(f"url_{s}"), (exp_p.get(tok) or (None, None))[1]
                ok, og = page_title_ok(url, q)
                if not ok:
                    page_bad.append(f"| {url} | expected {q!r} | page {og!r} |")
    head = (f"Link check: {checked} links, {wrong} wrong{' (fixed)' if fix and wrong else ''}, "
            f"{unknown} no longer in the API" + (f", {len(page_bad)} Polymarket page-title mismatches" if pages else ""))
    print(head)
    for l in lines + page_bad:
        print(" ", l)
    if fix and fixed:
        path.write_text(raw[:lo] + json.dumps(doc, ensure_ascii=False, separators=(",", ":")) + raw[hi:], encoding="utf8")
    if summary:
        with open(summary, "a", encoding="utf8") as f:
            f.write(f"\n### {head}\n")
            if lines:
                f.write("\n| type | platform | id | published | expected |\n|---|---|---|---|---|\n" + "\n".join(lines) + "\n")
            if page_bad:
                f.write("\n| url | expected title | page title |\n|---|---|---|\n" + "\n".join(page_bad) + "\n")


if __name__ == "__main__":
    main()
