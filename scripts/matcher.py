"""
Cross-platform market matcher.

Strategy:
  1. Political markets (US 2026 elections) → matched by canonical race_id
     using the same infer_race_id logic from the polling aggregator.
  2. Everything else → fuzzy title matching using rapidfuzz token_sort_ratio.
     Only pairs above FUZZY_THRESHOLD are kept.

Output: data/processed/matched_pairs.csv
Each row = one (platform_a, platform_b, market_a, market_b) pair with
implied probs, settlement dates, and match metadata.
"""

import re
import pandas as pd
from pathlib import Path
from rapidfuzz import fuzz, process as fuzz_process
from datetime import datetime, timezone

ROOT = Path(__file__).parent.parent
RAW = ROOT / "data" / "raw"
PROCESSED = ROOT / "data" / "processed"

FUZZY_THRESHOLD = 82   # token_sort_ratio score — tune this
MAX_CANDIDATES = 3     # fuzzy candidates to check per market

# ── race_id inference (US 2026 elections) ────────────────────────────────────

STATE_NAME_TO_ABBREV = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
    "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN", "mississippi": "MS",
    "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV",
    "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM", "new york": "NY",
    "north carolina": "NC", "north dakota": "ND", "ohio": "OH", "oklahoma": "OK",
    "oregon": "OR", "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT",
    "vermont": "VT", "virginia": "VA", "washington": "WA", "west virginia": "WV",
    "wisconsin": "WI", "wyoming": "WY",
}
STATE_ABBREVS = set(STATE_NAME_TO_ABBREV.values())

ELECTION_SIGNALS = {"senate", "senator", "governor", "gubernatorial", "house seat",
                    "congressional", "2026 midterm", "control the senate", "control the house"}
NON_ELECTION = {"nhl", "nba", "nfl", "mlb", "premier league", "stanley cup",
                "champions league", "world cup"}


def infer_race_id(title: str) -> str | None:
    q = title.lower()
    if any(k in q for k in NON_ELECTION):
        return None
    if not any(k in q for k in ELECTION_SIGNALS):
        return None
    if any(k in q for k in ["control the", "balance of power", "how many seats"]):
        return None

    # House: "NY-16", "CA-37"
    m = re.search(r"\b([A-Z]{2})-(\d{1,2})\b", title)
    if m and m.group(1) in STATE_ABBREVS:
        return f"2026-H-{m.group(1)}-{str(int(m.group(2))).zfill(2)}"

    state_abbrev = None
    for name, abbrev in STATE_NAME_TO_ABBREV.items():
        if name in q:
            state_abbrev = abbrev
            break
    if not state_abbrev:
        return None

    if "senate" in q or "senator" in q:
        if state_abbrev == "FL" and "special" in q:
            return "2026-SEN-FL-S"
        if state_abbrev == "OH" and "special" in q:
            return "2026-SEN-OH-S"
        return f"2026-SEN-{state_abbrev}"
    if "governor" in q or "gubernatorial" in q:
        return f"2026-GOV-{state_abbrev}"
    m2 = re.search(r"(\d+)(?:st|nd|rd|th)?\s*(?:congressional\s*)?district", q)
    if m2:
        return f"2026-H-{state_abbrev}-{str(int(m2.group(1))).zfill(2)}"
    return None


# ── normalise titles for fuzzy matching ──────────────────────────────────────

def normalise(title: str) -> str:
    """Strip filler words and punctuation for better fuzzy matching."""
    t = title.lower()
    # Remove common filler
    for pat in [r"will\s+", r"^who\s+will\s+", r"^what\s+will\s+", r"^which\s+",
                r"\?$", r"in\s+\d{4}", r"by\s+\w+\s+\d+,?\s*\d*",
                r"before\s+\w+\s+\d+", r"^\s*the\s+"]:
        t = re.sub(pat, " ", t)
    t = re.sub(r"[^\w\s]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


# ── loaders ──────────────────────────────────────────────────────────────────

def load_kalshi():
    df = pd.read_csv(RAW / "kalshi_markets.csv")
    df = df[df["implied_prob"].notna()].copy()
    df["platform"] = "kalshi"
    df["title_norm"] = df["title"].apply(normalise)
    df["race_id"] = df["title"].apply(infer_race_id)
    # Use close_date as settle_date
    df["settle_date"] = df["close_date"].astype(str)
    return df.rename(columns={"ticker": "market_id", "title": "question"})


def load_polymarket():
    df = pd.read_csv(RAW / "polymarket_markets.csv")
    df = df[df["implied_prob"].notna()].copy()
    df["platform"] = "polymarket"
    df["title_norm"] = df["question"].apply(normalise)
    df["race_id"] = df["question"].apply(infer_race_id)
    df["settle_date"] = df["end_date"].astype(str)
    df["market_id"] = df["condition_id"]
    return df


def load_predictit():
    df = pd.read_csv(RAW / "predictit_markets.csv")
    df = df[df["implied_prob"].notna()].copy()
    df["platform"] = "predictit"
    # Use contract name as question for matching (more specific)
    df["question"] = df["market_name"] + " — " + df["contract_name"]
    df["title_norm"] = df["market_name"].apply(normalise)
    df["race_id"] = df["market_name"].apply(infer_race_id)
    df["settle_date"] = ""
    df["market_id"] = df["contract_id"].astype(str)
    return df


# ── matching ─────────────────────────────────────────────────────────────────

def match_political(dfs: dict) -> pd.DataFrame:
    """Match by race_id for political markets."""
    pairs = []
    platform_names = list(dfs.keys())
    for i, pa in enumerate(platform_names):
        for pb in platform_names[i+1:]:
            a = dfs[pa][dfs[pa]["race_id"].notna()].copy()
            b = dfs[pb][dfs[pb]["race_id"].notna()].copy()

            # For each race_id present on both, pick highest open_interest/liquidity market
            def best_prob(g):
                oi = pd.to_numeric(g.get("open_interest", g.get("liquidity", pd.Series([0]*len(g), index=g.index))), errors="coerce").fillna(0)
                return g.iloc[oi.values.argmax()]

            a_best = a.groupby("race_id").apply(best_prob, include_groups=False).reset_index()
            b_best = b.groupby("race_id").apply(best_prob, include_groups=False).reset_index()

            merged = a_best.merge(b_best, on="race_id", suffixes=("_a", "_b"))
            for _, r in merged.iterrows():
                pairs.append({
                    "match_type": "political",
                    "race_id": r["race_id"],
                    "platform_a": pa,
                    "platform_b": pb,
                    "question_a": r.get("question_a", ""),
                    "question_b": r.get("question_b", ""),
                    "market_id_a": r.get("market_id_a", ""),
                    "market_id_b": r.get("market_id_b", ""),
                    "implied_prob_a": r.get("implied_prob_a"),
                    "implied_prob_b": r.get("implied_prob_b"),
                    "settle_date": r.get("settle_date_a", "") or r.get("settle_date_b", ""),
                    "category": "Elections",
                    "url_a": r.get("url_a", ""),
                    "url_b": r.get("url_b", ""),
                    "fuzzy_score": 100,
                })
    return pd.DataFrame(pairs)


def match_fuzzy(dfs: dict) -> pd.DataFrame:
    """Fuzzy-match non-political markets across platform pairs."""
    pairs = []
    platform_names = list(dfs.keys())

    for i, pa in enumerate(platform_names):
        for pb in platform_names[i+1:]:
            a = dfs[pa][dfs[pa]["race_id"].isna()].copy()
            b = dfs[pb][dfs[pb]["race_id"].isna()].copy()

            if a.empty or b.empty:
                continue

            b_norms = b["title_norm"].tolist()
            b_ids = b["market_id"].tolist()
            b_lookup = {bid: row for bid, row in zip(b_ids, b.itertuples())}

            print(f"  Fuzzy matching {pa} ({len(a)}) vs {pb} ({len(b)})...")
            matched = 0

            for _, row_a in a.iterrows():
                norm_a = row_a["title_norm"]
                if not norm_a or len(norm_a) < 8:
                    continue

                results = fuzz_process.extract(
                    norm_a, b_norms,
                    scorer=fuzz.token_sort_ratio,
                    score_cutoff=FUZZY_THRESHOLD,
                    limit=MAX_CANDIDATES,
                )

                for norm_b_match, score, idx in results:
                    row_b = b.iloc[idx]
                    # Skip if same platform somehow
                    if row_a.get("market_id") == row_b.get("market_id"):
                        continue
                    pairs.append({
                        "match_type": "fuzzy",
                        "race_id": None,
                        "platform_a": pa,
                        "platform_b": pb,
                        "question_a": row_a.get("question", ""),
                        "question_b": row_b.get("question", ""),
                        "market_id_a": row_a.get("market_id", ""),
                        "market_id_b": row_b.get("market_id", ""),
                        "implied_prob_a": row_a.get("implied_prob"),
                        "implied_prob_b": row_b.get("implied_prob"),
                        "settle_date": row_a.get("settle_date", "") or row_b.get("settle_date", ""),
                        "category": row_a.get("category", ""),
                        "url_a": row_a.get("url", ""),
                        "url_b": row_b.get("url", ""),
                        "fuzzy_score": round(score, 1),
                    })
                    matched += 1

            print(f"    -> {matched} fuzzy matches")

    return pd.DataFrame(pairs)


def run():
    PROCESSED.mkdir(parents=True, exist_ok=True)

    print("Loading markets...")
    kalshi = load_kalshi()
    polymarket = load_polymarket()
    predictit = load_predictit()
    print(f"  Kalshi: {len(kalshi)}, Polymarket: {len(polymarket)}, PredictIt: {len(predictit)}")

    dfs = {"kalshi": kalshi, "polymarket": polymarket, "predictit": predictit}

    print("\nMatching political markets by race_id...")
    political = match_political(dfs)
    print(f"  Political pairs: {len(political)}")

    print("\nFuzzy-matching non-political markets...")
    fuzzy = match_fuzzy(dfs)
    print(f"  Fuzzy pairs: {len(fuzzy)}")

    all_pairs = pd.concat([political, fuzzy], ignore_index=True)
    # Remove duplicates (same market pair, different direction)
    all_pairs = all_pairs.drop_duplicates(subset=["platform_a", "platform_b", "market_id_a", "market_id_b"])

    out = PROCESSED / "matched_pairs.csv"
    all_pairs.to_csv(out, index=False)
    print(f"\nTotal matched pairs: {len(all_pairs)} -> {out}")
    print(f"  Political: {len(political)}, Fuzzy: {len(fuzzy)}")


if __name__ == "__main__":
    run()
