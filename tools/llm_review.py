"""Model-assisted review of new Kalshi <-> Polymarket market families.

Hand review (read BOTH rules texts, approve or reject in data/series_map.json)
is the one step that still needs a person. A keyword heuristic can't do it
safely: on the 93 hand-reviewed families (tests/data/reviewed_families.json)
the strict version approved 4 of 80 good families and any looser version
approved a rejected one (OPEC). The real differences are semantic: "may
include" vs formal recognition only, singles/EPs vs albums, every co-host vs
the primary host, an announcement vs a formally declared date.

This asks Claude to read both rules texts and return a structured verdict.

  py tools/llm_review.py                  review the current queue (advise mode)
  py tools/llm_review.py --validate       score against the 93 reviewed families
  py tools/llm_review.py --apply          auto mode: write verdicts into the map

Modes:
  advise (default)  write data/processed/llm_reviews.json; tools/health.py puts
                    each draft (verdict, safe direction, differences, draft
                    note) in the Board health issue, so the human step becomes
                    "confirm the draft".
  --apply           write approved/rejected entries into data/series_map.json
                    (marked "reviewed_by": "model"). REFUSED unless the last
                    --validate run (data/llm_review_validation.json, committed)
                    approved ZERO rejected families. Even then an approval is
                    downgraded to "unsure" if a hard-difference phrase appears
                    on one side only (HARD_ASYMMETRIC), and only "either"-
                    direction equivalents are written.

Needs ANTHROPIC_API_KEY (CI secret). Without it this exits 0 and does nothing,
so the pipeline is unchanged. Validation costs ~93 requests (~$3 at Opus 5.5
prices); the queue review is a handful of requests per new family.
Added 2026-10-12.
"""
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
PROCESSED = ROOT / "data" / "processed"
LABELED = ROOT / "tests" / "data" / "reviewed_families.json"
VALIDATION = ROOT / "data" / "llm_review_validation.json"
MODEL = "claude-opus-5-5"

# Phrases that only ever appeared on ONE side in families a human rejected
# (never in an approved one) — a model "equivalent" with any of these present
# on one side only is downgraded to "unsure".
HARD_ASYMMETRIC = {
    "may include / not limited to": r"may include|not limited to|could include",
    "co-host / primary host": r"\bco-?host|primary host",
    "singles / EPs": r"\bsingles?\b|\bEPs?\b",
    "executive action": r"executive action",
    "consent decree / disgorgement / plea": r"consent decree|disgorgement|plea agreement",
    "geographic scope": r"conterminous|municipal boundar",
    "intention": r"\bintention",
}

SYSTEM = """You compare the resolution rules of two prediction markets, one on Kalshi and one on Polymarket, that a matcher paired as the same question. A trader will buy YES on one and NO on the other as a hedge. The hedge only works if every real-world outcome resolves the two the same way; if some outcome makes one YES and the other NO in the wrong direction, both legs lose.

Read both rules texts in full. Look for any outcome that would resolve them differently:
- different subject (person, team, country, place, category) or scope (one city vs a whole country)
- different event (album vs any release, win vs top-N, primary host vs any co-host, announcement vs formal act, appointment vs election)
- different time window or deadline (season end vs Dec 31, "after issuance" vs a fixed start, no deadline vs a deadline that resolves No/Other)
- different counting rules (extra time included or not, exactly vs or-above, inclusive vs exclusive thresholds, official vs consensus source)
- ties, joint winners, cancellations or postponements handled differently
- one side broader than the other (e.g. counts disgorgement, executive action, confirmations, "may include" lists)

Ignore boilerplate (trademark disclaimers, formatting, resolution-source URLs) and differences that cannot change the outcome.

Return:
- verdict: "equivalent" only if you find no realistic outcome that resolves them differently; "different" if you find one that matters; "unsure" otherwise.
- safe_direction: which basket survives the differences you found: "either" (no difference), "yes_kalshi" (YES on Kalshi + NO on Polymarket is safe), "yes_polymarket" (YES on Polymarket + NO on Kalshi is safe), or "neither".
- differences: each concrete difference, one sentence each (empty list if none).
- note: one sentence describing what both resolve on (for the review record).
- caveat: remote edge cases worth recording (ties, cancellations), or an empty string."""


def _client():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    try:
        import anthropic
    except ImportError:
        print("llm_review: the anthropic package isn't installed; skipping")
        return None
    return anthropic.Anthropic()


def _verdict_model():
    from typing import List, Literal
    from pydantic import BaseModel

    class Verdict(BaseModel):
        verdict: Literal["equivalent", "different", "unsure"]
        safe_direction: Literal["either", "yes_kalshi", "yes_polymarket", "neither"]
        differences: List[str]
        note: str
        caveat: str
    return Verdict


def review_pair(client, kalshi_q, kalshi_rules, poly_q, poly_rules):
    """Model verdict for one pair (dict), with HARD_ASYMMETRIC applied."""
    import anthropic
    user = (f"KALSHI market: {kalshi_q}\nKALSHI rules:\n{kalshi_rules}\n\n"
            f"POLYMARKET market: {poly_q}\nPOLYMARKET rules:\n{poly_rules}")
    try:
        r = client.messages.parse(
            model=MODEL, max_tokens=16000, system=SYSTEM,
            output_config={"effort": "high"},
            messages=[{"role": "user", "content": user}],
            output_format=_verdict_model(),
        )
    except anthropic.APIStatusError as e:
        return {"verdict": "unsure", "error": f"API {e.status_code}"}
    except anthropic.APIConnectionError:
        return {"verdict": "unsure", "error": "connection"}
    if r.stop_reason == "refusal" or r.parsed_output is None:
        return {"verdict": "unsure", "error": f"stop_reason {r.stop_reason}"}
    v = r.parsed_output.model_dump()
    hits = [n for n, rx in HARD_ASYMMETRIC.items()
            if bool(re.search(rx, kalshi_rules or "", re.I)) != bool(re.search(rx, poly_rules or "", re.I))]
    if hits:
        v["hard_asymmetric"] = hits
        if v["verdict"] == "equivalent":
            v["verdict"] = "unsure"
    return v


def validate(client):
    """Score the model on the hand-reviewed families; write VALIDATION."""
    data = json.loads(LABELED.read_text(encoding="utf8"))
    rows = []
    for o in data:
        v = review_pair(client, o["kalshi_q"], o["kalshi_rules"], o["poly_q"], o["poly_rules"])
        rows.append({"family": o["family"], "human": o["status"], "model": v.get("verdict"),
                     "safe_direction": v.get("safe_direction"), "differences": v.get("differences", [])})
        print(f"  {o['status']:9} -> {v.get('verdict'):10} {o['family'][:70]}")
    approve = [r for r in rows if r["model"] == "equivalent" and r["safe_direction"] == "either"]
    res = {
        "model": MODEL, "families": len(rows),
        "false_approvals": sorted(r["family"] for r in approve if r["human"] == "rejected"),
        "approved_coverage": f"{sum(r['human'] == 'approved' for r in approve)} of {sum(r['human'] == 'approved' for r in rows)}",
        "rejected_caught": f"{sum(r['human'] == 'rejected' and r['model'] == 'different' for r in rows)} of {sum(r['human'] == 'rejected' for r in rows)}",
        "rows": rows,
    }
    VALIDATION.write_text(json.dumps(res, indent=1), encoding="utf8")
    print(f"validation: false approvals {len(res['false_approvals'])}, coverage {res['approved_coverage']}, "
          f"rejected caught {res['rejected_caught']} -> {VALIDATION}")
    return res


def auto_allowed():
    try:
        v = json.loads(VALIDATION.read_text(encoding="utf8"))
    except (OSError, ValueError):
        return False, "no validation run on record (run --validate)"
    if v.get("model") != MODEL:
        return False, f"validation was for {v.get('model')}, not {MODEL}"
    if v.get("false_approvals"):
        return False, f"validation approved rejected families: {v['false_approvals']}"
    return True, ""


def review_queue(client, apply=False):
    import pandas as pd
    from scripts import scrutiny as scr
    from utils.rules_template import SOCCER_SERIES
    try:
        q = pd.read_csv(PROCESSED / "series_review.csv")
    except (OSError, pd.errors.EmptyDataError):
        q = pd.DataFrame()
    m = pd.read_csv(PROCESSED / "matched_pairs.csv", dtype=str, low_memory=False)
    cache = scr._load_cache()
    out = []
    for r in q.itertuples():
        if SOCCER_SERIES.match(str(r.kalshi_series)):
            continue                       # handled by the rules-text template
        pair = m[(m.question_a == r.example_a) & (m.question_b == r.example_b)]
        if pair.empty:
            continue
        p = pair.iloc[0]
        kt = scr.get_rules("kalshi", p.market_id_a, cache)
        pt = scr.get_rules("polymarket", p.market_id_b, cache)
        if not kt or not pt:
            continue
        v = review_pair(client, r.example_a, kt, r.example_b, pt)
        slug = re.search(r"polymarket\.com/event/([^/?#]+)", str(r.url_b))
        out.append({"kalshi_series": r.kalshi_series, "polymarket_family": r.polymarket_family,
                    "event_slug": slug.group(1) if slug else None, "example_a": r.example_a,
                    "example_b": r.example_b, **v})
        print(f"  {r.kalshi_series}: {v.get('verdict')} ({v.get('safe_direction')})")
    scr._save_cache(cache)
    (PROCESSED / "llm_reviews.json").write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf8")
    if apply:
        ok, why = auto_allowed()
        if not ok:
            print(f"::warning::llm_review --apply refused: {why}. Drafts written for the health issue instead.")
            return out
        _apply(out)
    return out


def _apply(reviews):
    from datetime import date
    from utils.series_map import strip_slug_id
    p = ROOT / "data" / "series_map.json"
    doc = json.loads(p.read_bytes().decode("utf8"))
    have = {(f["kalshi_series"], f["polymarket_event_slug_regex"]) for f in doc["families"]}
    added = 0
    for v in reviews:
        if not v.get("event_slug"):
            continue
        dated = re.match(r"^([a-z0-9]+)-.*?\d{4}-\d{2}-\d{2}", v["event_slug"])
        rx = (rf"^{dated.group(1)}-.+-\d{{4}}-\d{{2}}-\d{{2}}" if dated
              else "^" + re.escape(strip_slug_id(v["event_slug"])) + "$")
        if (v["kalshi_series"], rx) in have:
            continue
        if v.get("verdict") == "equivalent" and v.get("safe_direction") == "either":
            status = "approved"
        elif v.get("verdict") == "different":
            status = "rejected"
        else:
            continue                        # unsure / one-direction: leave for a person
        doc["families"].append({
            "kalshi_series": v["kalshi_series"], "polymarket_event_slug_regex": rx, "status": status,
            "rules_read": date.today().isoformat(), "reviewed_by": "model",
            "note": v.get("note") if status == "approved" else " ".join(v.get("differences", [])) or v.get("note"),
            **({"caveat": v["caveat"]} if v.get("caveat") else {}),
        })
        added += 1
    p.write_bytes((json.dumps(doc, indent=1, ensure_ascii=False).replace("\n", "\r\n") + "\r\n").encode("utf8"))
    print(f"llm_review: wrote {added} model-reviewed families to data/series_map.json")


def main():
    client = _client()
    if client is None:
        print("llm_review: no ANTHROPIC_API_KEY; skipping (families stay in the human queue)")
        return
    if "--validate" in sys.argv:
        validate(client)
    else:
        review_queue(client, apply="--apply" in sys.argv)


if __name__ == "__main__":
    main()
