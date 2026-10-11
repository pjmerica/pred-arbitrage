"""Board health: what needs a human, posted as one GitHub issue.

Usage:  py tools/health.py [--issue] [--summary FILE]

Collects the things the pipeline can't settle by itself:
  - unreviewed series families with pairs (data/processed/series_review.csv)
    — someone must read both rules texts and approve/reject;
  - series-map approvals lapsing within 14 days;
  - links the link check had to repair, or pages that don't show the priced
    market (data/processed/linkcheck.json) — usually a platform URL change;
  - stale data: last full scrape > 14 h, or board not re-priced in > 4 h;
  - unverified baskets with a positive return (held back, worth a look).
Informational only (no issue): how many guaranteed baskets the live re-check
found still open — closing baskets are normal market movement.

--issue keeps ONE open GitHub issue titled "Board health: needs attention"
in sync (gh CLI, GH_TOKEN): created when something actionable appears (the
creation is what notifies the owner), edited quietly while it persists,
closed with a comment when everything clears. Always exits 0. Added
2026-10-11 so the board can run unattended.
"""
import json
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
PROCESSED = ROOT / "data" / "processed"
TITLE = "Board health: needs attention"
STALE_HOURS = 4      # --stale-hours N: polling-agg refreshes every 2 h, so it passes 8


def _board():
    t = (ROOT / "docs" / "arb_data.js").read_text(encoding="utf8")
    return json.loads(t[t.index("{"):t.rindex("}") + 1])


def _age_h(ts):
    try:
        t = datetime.fromisoformat(str(ts))
        return (datetime.now(timezone.utc) - t).total_seconds() / 3600
    except ValueError:
        return None


def _json(name):
    try:
        return json.loads((PROCESSED / name).read_text(encoding="utf8"))
    except (OSError, ValueError):
        return None


def collect():
    act, info = [], []
    board = _board()
    # pred-arb keeps rows under "races"; polling-agg under its first list.
    rows = board.get("races") or next((v for v in board.values() if isinstance(v, list)), [])

    a = _age_h(board.get("markets_scraped_at"))
    if a is not None and a > 14:
        act.append(f"**Stale scrape:** the last full scrape was {a:.0f} h ago (limit 14 h). "
                   "Check the Daily refresh workflow; the re-price watchdog should have dispatched one.")
    a = _age_h(board.get("updated_at"))
    if a is not None and a > STALE_HOURS:
        act.append(f"**Board not re-priced in {a:.0f} h.** GitHub cron may be skipping runs.")

    try:
        q = pd.read_csv(PROCESSED / "series_review.csv")
    except (OSError, pd.errors.EmptyDataError):
        q = pd.DataFrame()
    if len(q):
        # Soccer BTTS / correct-score families are checked automatically from
        # the rules text each scan; one in the queue means that check couldn't
        # fetch the rules this time (rate limit). Info only — the next scan
        # retries — so it doesn't open an issue and notify for nothing.
        try:
            from utils.rules_template import SOCCER_SERIES
            auto = q[q["kalshi_series"].astype(str).str.match(SOCCER_SERIES)]
        except ImportError:
            auto = q.iloc[0:0]
        q = q[~q.index.isin(auto.index)]
        if len(auto):
            info.append(f"{len(auto)} soccer families waiting for an automatic rules-text check "
                        f"(rules fetch failed this run): " + ", ".join(auto["kalshi_series"].astype(str)[:10]))
    if len(q):
        lines = [f"  - `{r.kalshi_series}` / `{r.polymarket_family}`: {int(r.pairs)} pairs, "
                 f"{int(r.baskets) if pd.notna(r.baskets) else 0} baskets — e.g. {str(r.example_a)[:70]!r}"
                 for r in q.itertuples()]
        act.append(f"**{len(q)} unreviewed market families** (read both rules texts, then approve or "
                   "reject in `data/series_map.json`):\n" + "\n".join(lines[:25])
                   + (f"\n  - … {len(lines) - 25} more in data/processed/series_review.csv" if len(lines) > 25 else ""))

    try:
        sys.path.insert(0, str(ROOT))
        from scripts.arb_scanner import _lapsing_approvals
        lap = _lapsing_approvals()
    except Exception:
        lap = []
    if lap:
        act.append("**Approvals lapsing within 14 days** (re-read the rules, then extend `review_by` "
                   "or change the status):\n" + "\n".join(
                       f"  - `{f['kalshi_series']}` lapses {f['review_by']}. {f.get('caveat', '')}" for f in lap))

    lc = _json("linkcheck.json")
    if lc:
        if lc.get("wrong"):
            act.append(f"**{lc['wrong']} links were wrong and repaired** — a platform may have changed its "
                       "URL format; check `utils/links.py`:\n" + "\n".join("  " + x for x in lc.get("wrong_rows", [])[:10]))
        if lc.get("page_mismatches"):
            act.append(f"**{lc['page_mismatches']} market pages don't show the priced market:**\n"
                       + "\n".join("  " + x for x in lc.get("page_rows", [])[:10]))
        if lc.get("unknown", 0) > 50:
            act.append(f"**{lc['unknown']} links couldn't be resolved through the platform APIs** "
                       "(rate limiting, or an API change).")

    held = [r for r in rows if r.get("arb_type") == "unverified" and (r.get("guaranteed_return_pct") or 0) > 0]
    if held:
        act.append(f"**{len(held)} profitable baskets held back as unverified:**\n" + "\n".join(
            f"  - {r.get('guaranteed_return_pct')}% — {str(r.get('question_a'))[:60]!r} vs "
            f"{str(r.get('question_b'))[:60]!r} ({', '.join(r.get('suspicion_reasons') or []) or r.get('series_status')})"
            for r in held[:15]))

    lv = _json("livecheck.json")
    if lv:
        info.append(f"Live re-check: {lv.get('open')} of {lv.get('guaranteed')} guaranteed baskets still open.")
    info.append(f"Board {board.get('updated_at')} ({board.get('mode') or 'market refresh'}), "
                f"{sum(r.get('arb_type') == 'guaranteed' for r in rows)} guaranteed of {len(rows)} pairs.")
    return act, info


def body(act, info):
    head = "Opened and kept in sync by `tools/health.py` (CI). It closes itself when everything below is resolved.\n\n"
    return head + ("\n\n".join(f"- {a}" for a in act) or "- Nothing needs attention.") + "\n\n" + "\n".join(f"_{i}_" for i in info) + "\n"


def gh(*args):
    r = subprocess.run(["gh", *args], capture_output=True, text=True)
    if r.returncode != 0:
        # Surface failures (no token, issues disabled, API error) as a CI
        # warning instead of printing a success line that didn't happen.
        print(f"::warning::gh {' '.join(args[:2])} failed: {r.stderr.strip()[:300]}")
        return None
    return r.stdout.strip() or "ok"


def sync_issue(act, info):
    num = gh("issue", "list", "--state", "open", "--search", f'in:title "{TITLE}"',
             "--json", "number", "--jq", ".[0].number")
    if num in (None, "ok"):
        num = None
    text = body(act, info)
    if act and not num:
        if gh("issue", "create", "--title", TITLE, "--body", text):
            print("health: opened issue")
    elif act and num:
        if gh("issue", "edit", num, "--body", text):
            print(f"health: updated issue #{num}")
    elif not act and num:
        if gh("issue", "close", num, "--comment", "All clear: " + " ".join(info)):
            print(f"health: closed issue #{num}")
    else:
        print("health: all clear, no issue")


def main():
    args = sys.argv[1:]
    global STALE_HOURS
    if "--stale-hours" in args:
        STALE_HOURS = float(args[args.index("--stale-hours") + 1])
    act, info = collect()
    text = body(act, info)
    print(text)
    if "--summary" in args:
        with open(args[args.index("--summary") + 1], "a", encoding="utf8") as f:
            f.write("\n### Board health\n\n" + text)
    if "--issue" in args:
        sync_issue(act, info)


if __name__ == "__main__":
    main()
