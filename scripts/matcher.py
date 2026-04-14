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

FUZZY_THRESHOLD = 88   # token_sort_ratio score — raised to reduce candidate-vs-party false positives
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
    # Sort by length descending so "west virginia" matches before "virginia"
    for name, abbrev in sorted(STATE_NAME_TO_ABBREV.items(), key=lambda x: -len(x[0])):
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


# ── contract-type classification for political markets ───────────────────────

def party_side(question: str) -> str | None:
    """
    For party_winner contracts, return which party the question is asking about
    winning ('dem', 'rep', or None if unclear).
    """
    q = question.lower()
    dem_words = ["democrat", "democratic", "dem ", "blue"]
    rep_words = ["republican", "gop", "rep ", "red"]
    is_dem = any(w in q for w in dem_words)
    is_rep = any(w in q for w in rep_words)
    if is_dem and not is_rep:
        return "dem"
    if is_rep and not is_dem:
        return "rep"
    return None


def political_contract_type(question: str) -> str:
    """
    Classify a political market question into a contract type so we only
    match like-for-like across platforms.

    Returns one of:
      'party_winner'   — "Which party will win X?" or "Will Democrats/Republicans win X?"
      'candidate'      — "Will [Name] win/be nominee for X?"
      'primary'        — "Who will win the Republican/Democratic primary/nomination?"
      'other'
    """
    q = question.lower()

    # Primary / nomination questions
    if any(k in q for k in ["nomination", "nominee", "primary", "republican nominee",
                             "democratic nominee", "gop nominee"]):
        return "primary"

    # Party-winner questions
    if any(k in q for k in ["which party will win", "which party wins",
                             "will the republican", "will the democrat",
                             "will republicans win", "will democrats win",
                             "republican party", "democratic party"]):
        return "party_winner"

    # General winner (e.g. "Who will win the 2026 Senate election in X?")
    if any(k in q for k in ["who will win", "who wins"]):
        return "candidate"

    # Named candidate (has a proper-noun-ish pattern before "win")
    # Simple heuristic: question contains "will [Firstname Lastname]"
    if re.search(r"will [a-z]+ [a-z]+ (win|be elected|become)", q):
        return "candidate"

    return "other"


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
    df["category"] = "Politics"  # PredictIt is politics-only
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
                qa = str(r.get("question_a", ""))
                qb = str(r.get("question_b", ""))
                type_a = political_contract_type(qa)
                type_b = political_contract_type(qb)

                # Only match like-for-like contract types.
                # Allow party_winner↔party_winner and candidate↔candidate.
                # Skip primary↔party_winner, candidate↔party_winner, etc.
                if type_a != type_b:
                    continue
                if type_a == "primary" or type_b == "primary":
                    continue

                prob_a = r.get("implied_prob_a")
                prob_b = r.get("implied_prob_b")

                # If both are party_winner but asking about opposite parties,
                # flip one so both probs represent the same party winning.
                if type_a == "party_winner" and type_b == "party_winner":
                    side_a = party_side(qa)
                    side_b = party_side(qb)
                    if side_a and side_b and side_a != side_b:
                        # Flip prob_b so it represents the same party as prob_a
                        if prob_b is not None:
                            prob_b = round(1.0 - float(prob_b), 4)
                        qb = f"[flipped] {qb}"

                pairs.append({
                    "match_type": "political",
                    "race_id": r["race_id"],
                    "platform_a": pa,
                    "platform_b": pb,
                    "question_a": qa,
                    "question_b": qb,
                    "market_id_a": r.get("market_id_a", ""),
                    "market_id_b": r.get("market_id_b", ""),
                    "implied_prob_a": prob_a,
                    "implied_prob_b": prob_b,
                    "settle_date": r.get("settle_date_a", "") or r.get("settle_date_b", ""),
                    "category": "Elections",
                    "url_a": r.get("url_a", ""),
                    "url_b": r.get("url_b", ""),
                    "fuzzy_score": 100,
                })
    return pd.DataFrame(pairs)


CATEGORY_GROUPS = {
    # Map platform-specific category names → normalized group so we only
    # fuzzy-match within the same group. Hugely speeds up matching and cuts
    # false positives.
    "sports": "sports",
    "nfl": "sports", "nba": "sports", "nhl": "sports", "mlb": "sports",
    "soccer": "sports", "tennis": "sports", "golf": "sports",
    "ufc": "sports", "mma": "sports", "boxing": "sports",
    "esports": "esports",
    "politics": "politics",
    "elections": "politics",
    "us-elections": "politics", "us-politics": "politics",
    "geopolitics": "politics",
    "crypto": "crypto", "crypto prices": "crypto",
    "finance": "finance", "economics": "finance", "economy": "finance",
    "stocks": "finance", "business": "finance", "financials": "finance",
    "inflation": "finance",
    "entertainment": "culture", "culture": "culture",
    "pop-culture": "culture", "music": "culture",
    "movies": "culture", "tv": "culture", "mentions": "culture",
    "science": "science", "technology": "science",
    "science and technology": "science", "tech": "science",
    "ai": "science", "big tech": "science",
    "climate": "climate", "weather": "climate",
    "climate and weather": "climate", "climate & science": "climate",
    "world": "world", "health": "health",
    "companies": "companies", "social": "social",
    "transportation": "misc",
}


def normalize_category(c: str) -> str:
    if not c or not isinstance(c, str):
        return "other"
    return CATEGORY_GROUPS.get(c.strip().lower(), "other")


def match_fuzzy(dfs: dict) -> pd.DataFrame:
    """Fuzzy-match non-political markets across platform pairs.

    Only matches within the same category group for speed and precision.
    Uses rapidfuzz.process.cdist for vectorized scoring.
    """
    import numpy as np
    pairs = []
    platform_names = list(dfs.keys())

    for i, pa in enumerate(platform_names):
        for pb in platform_names[i+1:]:
            a_full = dfs[pa][dfs[pa]["race_id"].isna()].copy()
            b_full = dfs[pb][dfs[pb]["race_id"].isna()].copy()

            if a_full.empty or b_full.empty:
                continue

            a_full["cat_group"] = a_full["category"].apply(normalize_category) if "category" in a_full.columns else "other"
            b_full["cat_group"] = b_full["category"].apply(normalize_category) if "category" in b_full.columns else "other"

            shared_groups = set(a_full["cat_group"].unique()) & set(b_full["cat_group"].unique())
            print(f"  Fuzzy matching {pa} ({len(a_full)}) vs {pb} ({len(b_full)}) across {len(shared_groups)} category groups...")
            matched = 0

            for group in shared_groups:
                a = a_full[a_full["cat_group"] == group].reset_index(drop=True)
                b = b_full[b_full["cat_group"] == group].reset_index(drop=True)
                if len(a) == 0 or len(b) == 0:
                    continue

                a_norms = a["title_norm"].fillna("").tolist()
                b_norms = b["title_norm"].fillna("").tolist()

                # Vectorized pairwise scoring. Score matrix shape (len_a, len_b).
                score_matrix = fuzz_process.cdist(
                    a_norms, b_norms,
                    scorer=fuzz.token_sort_ratio,
                    workers=-1,
                )

                # For each row in a, find up to MAX_CANDIDATES top matches above threshold
                for ai in range(len(a)):
                    row_a = a.iloc[ai]
                    norm_a = a_norms[ai]
                    if not norm_a or len(norm_a) < 8:
                        continue
                    row_scores = score_matrix[ai]
                    # Top candidates above threshold
                    top_idx = np.argsort(-row_scores)[:MAX_CANDIDATES]
                    results = [(b_norms[j], float(row_scores[j]), j) for j in top_idx if row_scores[j] >= FUZZY_THRESHOLD]

                    for norm_b_match, score, idx in results:
                        row_b = b.iloc[idx]
                        if row_a.get("market_id") == row_b.get("market_id"):
                            continue

                        # Drop if deadline years don't overlap
                        years_a = set(re.findall(r"\b(20\d{2})\b", str(row_a.get("question", ""))))
                        years_b = set(re.findall(r"\b(20\d{2})\b", str(row_b.get("question", ""))))
                        if years_a and years_b and not years_a.intersection(years_b):
                            continue

                        # Drop candidate-vs-party mismatches
                        qa_type = political_contract_type(str(row_a.get("question", "")))
                        qb_type = political_contract_type(str(row_b.get("question", "")))
                        if qa_type in ("party_winner", "candidate") and qb_type in ("party_winner", "candidate"):
                            if qa_type != qb_type:
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
