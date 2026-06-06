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

def _safe_read_csv(path, **kw):
    """Read a CSV, returning an empty DataFrame if the file is missing,
    zero-byte, or has no columns. Lets us survive transient scraper
    outages (Kalshi/Polymarket APIs occasionally return nothing) without
    crashing the whole pipeline."""
    if not path.exists():
        print(f"  WARNING: {path.name} does not exist — using empty DataFrame")
        return pd.DataFrame()
    try:
        return pd.read_csv(path, **kw)
    except pd.errors.EmptyDataError:
        print(f"  WARNING: {path.name} is empty — using empty DataFrame")
        return pd.DataFrame()


def load_kalshi():
    df = _safe_read_csv(RAW / "kalshi_markets.csv")
    if df.empty or "implied_prob" not in df.columns:
        return pd.DataFrame(columns=["market_id", "question", "race_id", "platform"])
    df = df[df["implied_prob"].notna()].copy()
    df["platform"] = "kalshi"
    df["title_norm"] = df["title"].apply(normalise)
    df["race_id"] = df["title"].apply(infer_race_id)
    # Use close_date as settle_date
    df["settle_date"] = df["close_date"].astype(str)
    return df.rename(columns={"ticker": "market_id", "title": "question"})


def load_polymarket():
    # yes_token_id / no_token_id are 78-digit ints — must read as str or
    # pandas will silently corrupt them to floats (scientific notation),
    # breaking CLOB orderbook lookups.
    df = _safe_read_csv(
        RAW / "polymarket_markets.csv",
        dtype={"yes_token_id": str, "no_token_id": str},
    )
    if df.empty or "implied_prob" not in df.columns:
        return pd.DataFrame(columns=["market_id", "question", "race_id", "platform"])
    df = df[df["implied_prob"].notna()].copy()
    df["platform"] = "polymarket"
    df["title_norm"] = df["question"].apply(normalise)
    df["race_id"] = df["question"].apply(infer_race_id)
    df["settle_date"] = df["end_date"].astype(str)
    # Use yes_token_id as market_id so fetch_depth can query CLOB orderbook
    # directly. Fall back to condition_id for rows missing token ids.
    df["market_id"] = df["yes_token_id"].where(
        df["yes_token_id"].notna() & (df["yes_token_id"] != ""),
        df["condition_id"],
    )
    return df


def load_predictit():
    df = _safe_read_csv(RAW / "predictit_markets.csv")
    if df.empty or "implied_prob" not in df.columns:
        return pd.DataFrame(columns=["market_id", "question", "race_id", "platform"])
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

                # For candidate markets on the same race, the candidate names
                # must match. Otherwise we'd pair "Caruso wins CA-GOV" with
                # "Steyer wins CA-GOV" just because they share race_id.
                if type_a == "candidate" and type_b == "candidate":
                    def candidate_name(q):
                        m = re.search(r"will\s+([A-Z][A-Za-z'\-\.]+(?:\s+[A-Z][A-Za-z'\-\.]+){0,2})\s+(win|be\s+elected|become)", q, re.IGNORECASE)
                        if m:
                            return m.group(1).lower().strip()
                        # PredictIt "— Tom Steyer" / Kalshi "? — Name" patterns
                        m = re.search(r"[—\-]\s*([A-Z][A-Za-z'\-\.]+(?:\s+[A-Z][A-Za-z'\-\.]+){0,2})\s*$", q)
                        if m:
                            return m.group(1).lower().strip()
                        return None
                    name_a = candidate_name(qa)
                    name_b = candidate_name(qb)
                    if name_a and name_b:
                        # Last-name match is sufficient (handles "Rick Caruso" vs "Caruso")
                        last_a = name_a.split()[-1]
                        last_b = name_b.split()[-1]
                        if last_a != last_b:
                            continue

                prob_a = r.get("implied_prob_a")
                prob_b = r.get("implied_prob_b")

                # If both are party_winner but asking about opposite parties,
                # we'd normally flip one ("Will Reps win" → 1 - prob), but
                # this is only safe when the race is effectively 2-way.
                # If a serious independent / third-party candidate has
                # non-trivial probability (e.g. Osborn in NE 2026 at 30%),
                # then 1 - P(Reps win) ≠ P(Dems win), and the cross-flipped
                # pair would describe a basket that doesn't partition the
                # outcome space — producing a fake guaranteed arb.
                #
                # Safety check: require BOTH platforms to have explicit
                # Dem + Rep markets that sum to ≥0.97. If either platform's
                # Dem + Rep < 0.97, the race has third-party probability and
                # we drop the pair.
                if type_a == "party_winner" and type_b == "party_winner":
                    side_a = party_side(qa)
                    side_b = party_side(qb)
                    if side_a and side_b and side_a != side_b:
                        def party_sum(plat_df, race_id):
                            sub = plat_df[plat_df["race_id"] == race_id]
                            dem_p = rep_p = None
                            for _, row in sub.iterrows():
                                ps = party_side(str(row.get("question", "")))
                                if ps == "dem" and dem_p is None:
                                    dem_p = pd.to_numeric(row.get("implied_prob"), errors="coerce")
                                elif ps == "rep" and rep_p is None:
                                    rep_p = pd.to_numeric(row.get("implied_prob"), errors="coerce")
                            if dem_p is None or rep_p is None or pd.isna(dem_p) or pd.isna(rep_p):
                                return None
                            return float(dem_p) + float(rep_p)
                        sum_a = party_sum(dfs[pa], r["race_id"])
                        sum_b = party_sum(dfs[pb], r["race_id"])
                        if sum_a is None or sum_b is None or sum_a < 0.97 or sum_b < 0.97:
                            # Race has meaningful third-party probability —
                            # cross-flipped basket isn't a true arb.
                            continue
                        # Safe to flip
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
                    "volume_a": r.get("volume_a", None),
                    "volume_b": r.get("volume_b", None),
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

                        # For candidate/primary pairs, the candidate names must match.
                        # PredictIt's "Who will win the 2028 GOP nom? — JD Vance" was
                        # being paired with Polymarket's "Will Tom Brady win the 2028
                        # GOP nomination?" because both classify as primary and the
                        # title prefix is similar enough to score above the fuzzy
                        # threshold.
                        if qa_type in ("primary", "candidate") and qb_type in ("primary", "candidate"):
                            def cand_last(q):
                                ql = str(q)
                                # Pattern 1: "Will <Name> win/be"
                                m = re.search(r"will\s+([A-Z][A-Za-z'\-\.]+(?:\s+[A-Z][A-Za-z'\-\.]+){0,2})\s+(?:win|be\b|become)",
                                              ql, re.IGNORECASE)
                                if m: return m.group(1).split()[-1].lower().strip(".'-")
                                # Pattern 2: "— <Name>" or "- <Name>" at end
                                m = re.search(r"[—\-]\s*([A-Z][A-Za-z'\-\.]+(?:\s+[A-Z][A-Za-z'\-\.]+){0,2})\s*$", ql)
                                if m: return m.group(1).split()[-1].lower().strip(".'-")
                                return None
                            la = cand_last(row_a.get("question", ""))
                            lb = cand_last(row_b.get("question", ""))
                            if la and lb and la != lb:
                                continue

                        # Drop numeric-bucket mismatches: if both questions contain different
                        # numeric ranges (e.g. "Above 5.6M" vs "65-68%", or "57° or below"
                        # vs "67° or below"), they're different buckets of the same event,
                        # not the same market.
                        q_a = str(row_a.get("question", ""))
                        q_b = str(row_b.get("question", ""))

                        # Strip date phrases first so "May 1" / "April 13" don't pollute
                        # the comparison with the day-of-month number.
                        date_re = (r"\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|"
                                   r"jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|"
                                   r"oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?\s*\d{1,2}\b")
                        clean_a = re.sub(date_re, "", q_a, flags=re.IGNORECASE)
                        clean_b = re.sub(date_re, "", q_b, flags=re.IGNORECASE)

                        nums_a = set(re.findall(r"\d+\.?\d*[%MK]?", clean_a))
                        nums_b = set(re.findall(r"\d+\.?\d*[%MK]?", clean_b))
                        nums_a = {n for n in nums_a if n not in years_a and not re.match(r"^20\d{2}$", n)}
                        nums_b = {n for n in nums_b if n not in years_b and not re.match(r"^20\d{2}$", n)}
                        if nums_a and nums_b and not nums_a.intersection(nums_b):
                            continue

                        # Threshold-bucket mismatch: questions like "X or below" / "above X" /
                        # "between X and Y" with explicit numeric thresholds. Even if one
                        # number happens to overlap, if there's a phrase-anchored threshold
                        # on each side and they differ, they're different buckets.
                        def threshold_value(q):
                            ql = q.lower()
                            for pat in [r"(\d+\.?\d*)\s*°?\s*(?:f|c)?\s*(?:or below|or less|or under|or fewer)",
                                        r"(?:above|over|more than|greater than)\s+(\d+\.?\d*)",
                                        r"(?:below|under|less than)\s+(\d+\.?\d*)",
                                        r"(\d+\.?\d*)\s*°?\s*(?:f|c)\b"]:
                                m = re.search(pat, ql)
                                if m: return float(m.group(1))
                            return None
                        ta = threshold_value(q_a)
                        tb = threshold_value(q_b)
                        if ta is not None and tb is not None and abs(ta - tb) > 0.5:
                            continue

                        # "run for" / "announce" / "enter race" vs "win" are semantically different
                        # questions about the same candidate — filter them out.
                        run_words = ("run for", "runs for", "announce", "enter the", "file to run")
                        win_words = ("win ", "wins ", "winner", "be the ", "be confirmed", "nominated")
                        is_run_a = any(w in q_a.lower() for w in run_words)
                        is_run_b = any(w in q_b.lower() for w in run_words)
                        is_win_a = any(w in q_a.lower() for w in win_words)
                        is_win_b = any(w in q_b.lower() for w in win_words)
                        if (is_run_a and is_win_b and not is_run_b) or (is_run_b and is_win_a and not is_run_a):
                            continue

                        # Rank/position buckets: "top" / "#1" vs "#2" are different outcomes
                        def rank_token(q):
                            ql = q.lower()
                            if re.search(r"\btop\b|#1\b|\bfirst\b|\bwinner\b", ql) and not re.search(r"#2|#3|second|third", ql):
                                return "top"
                            m = re.search(r"#([2-9])", ql)
                            if m:
                                return f"rank{m.group(1)}"
                            return None
                        r_a = rank_token(q_a)
                        r_b = rank_token(q_b)
                        if r_a and r_b and r_a != r_b:
                            continue

                        # Office mismatch: "Presidential nominee" vs "Vice-Presidential
                        # nominee" — same template, different office. Same logic for
                        # candidate vs "will the nominee be a woman / under 60 / black".
                        def office_role(q):
                            ql = q.lower()
                            if "vice-president" in ql or "vice president" in ql or "v.p." in ql or re.search(r"\bvp\b", ql):
                                return "vp"
                            if "president" in ql or "presidential" in ql:
                                return "president"
                            return None
                        oa = office_role(q_a)
                        ob = office_role(q_b)
                        if oa and ob and oa != ob:
                            continue

                        # Demographic-property markets ("Will the nominee be a woman?",
                        # "Will the winner be under 60?", "Will the next president be
                        # a Republican?") describe a property of the winner, not WHICH
                        # candidate wins. Don't pair them with named-candidate markets.
                        def is_demographic(q):
                            ql = q.lower()
                            return bool(re.search(
                                r"\b(?:nominee|winner|president|senator|governor|candidate)\b"
                                r".{0,40}\bbe\s+(?:a\s+)?(?:woman|man|under|over|black|white|hispanic|"
                                r"latino|jewish|muslim|christian|catholic|gay|lgbt|democrat|republican|"
                                r"independent)\b", ql))
                        da_dem = is_demographic(q_a)
                        db_dem = is_demographic(q_b)
                        if da_dem != db_dem:  # one is demographic, the other names a candidate
                            continue

                        # Sports / event sub-bet mismatch. Kalshi often lists outcomes
                        # like "Team A vs Team B — Tie" (3-way moneyline) while
                        # Polymarket has "Team A vs Team B: O/U 2.5" (totals). They
                        # share the team prefix and score high on fuzzy match but are
                        # totally different bets. Classify each side and skip if the
                        # types differ.
                        def sub_bet_type(q):
                            ql = str(q).lower()
                            if re.search(r"\bo/u\s*\d|\bover/under|\bover\s+\d|\bunder\s+\d", ql):
                                return "totals"
                            if re.search(r"\bspread:|\bspread\s*[+-]?\d|puck\s*line|run\s*line", ql):
                                return "spread"
                            if re.search(r"\bml\s*\(|\bmoneyline\b", ql):
                                return "moneyline"
                            if re.search(r"[—\-]\s*tie\s*$|\b—\s*draw\s*$", ql) or re.search(r"\btie\s*$", ql):
                                return "3way_tie"
                            if re.search(r"first\s+(half|quarter|period)|\b1h\b|\b1q\b|\bhalftime\b", ql):
                                return "period"
                            if re.search(r"player\s+(props?|points|rebounds|assists)|to\s+score|first\s+(goal|td|basket)", ql):
                                return "player_prop"
                            return None
                        st_a = sub_bet_type(q_a)
                        st_b = sub_bet_type(q_b)
                        if (st_a or st_b) and st_a != st_b:
                            continue

                        # Date-bucket mismatch: one question is scoped to "before [date]" /
                        # "by [date]" and the other isn't (or names a different date). Kalshi
                        # often splits a single resolution into multiple date-bucket markets
                        # ("Before Mar 1", "Before Apr 1"), which must not match a generic
                        # Polymarket market for the same subject.
                        def date_bucket(q):
                            ql = q.lower()
                            m = re.search(r"(?:before|by|on or before|no later than)\s+"
                                          r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
                                          r"jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
                                          r"\s*\.?\s*(\d{1,2})?", ql)
                            if m:
                                return (m.group(1)[:3], m.group(2) or "")
                            return None
                        db_a = date_bucket(q_a)
                        db_b = date_bucket(q_b)
                        if (db_a and not db_b) or (db_b and not db_a):
                            continue
                        if db_a and db_b and db_a != db_b:
                            continue

                        # Month/day anchor mismatch: catches questions like
                        # "best coding model end of April 2026" vs "...end of December 2026"
                        # AND weekly markets like "Raw: 2026 - April 13" vs "...April 20"
                        # which share the month but not the day.
                        MONTHS_RE = (r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|"
                                     r"jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|"
                                     r"oct(?:ober)?|nov(?:ember)?|dec(?:ember)?")
                        def month_day_anchors(q):
                            """Return (months_set, dates_set) where dates_set holds
                            (mon, day) tuples extracted from "<month> <1-31>" mentions."""
                            ql = q.lower()
                            months_found = set()
                            dates_found = set()
                            for m in re.finditer(rf"\b({MONTHS_RE})\b\.?\s*(\d{{1,2}})?", ql):
                                mon = m.group(1)[:3]
                                months_found.add(mon)
                                day = m.group(2)
                                if day and 1 <= int(day) <= 31:
                                    dates_found.add((mon, int(day)))
                            return months_found, dates_found
                        ma_a, da_a = month_day_anchors(q_a)
                        ma_b, da_b = month_day_anchors(q_b)
                        # Both name months but disjoint → different settlement window
                        if ma_a and ma_b and not (ma_a & ma_b):
                            continue
                        # Exactly one names a month → date-scope mismatch
                        if (ma_a and not ma_b) or (ma_b and not ma_a):
                            continue
                        # Both name specific (month, day) dates and they're disjoint →
                        # different weekly/daily buckets even within the same month
                        if da_a and da_b and not (da_a & da_b):
                            continue

                        # Drop subject-name mismatches within the same event.
                        # Handles both phrasings:
                        #   Polymarket "Will <Name> win..." (subject after "Will")
                        #   Kalshi    "Some question? — <Name>" (subject after dash)
                        # Examples that should be dropped:
                        #   "Will Velichie win..." vs "Will Vazrazhdane win..."
                        #   "Top Global Netflix Movie this week? — The Crash" vs
                        #     "Will 'Apex' be the top global Netflix movie this week?"
                        def extract_subject(q):
                            ql = q.strip()
                            # Pattern 1: "Will <Name> win/be/become..."
                            m = re.match(r"\s*Will\s+(['\"]?[A-Z][A-Za-z0-9'\"\-\.: ]+?)\s+(?:win|be\b|become|advance|attend|finish)",
                                         ql, re.IGNORECASE)
                            if m:
                                return m.group(1).strip(" '\"").lower()
                            # Pattern 2: trailing "— <Subject>" or "- <Subject>" (Kalshi)
                            m = re.search(r"[—\-]\s*(['\"]?[A-Za-z0-9][A-Za-z0-9'\"\-\.: ]+?)\s*$", ql)
                            if m:
                                return m.group(1).strip(" '\"").lower()
                            return None
                        subj_a = extract_subject(q_a)
                        subj_b = extract_subject(q_b)
                        if subj_a and subj_b and subj_a != subj_b:
                            # Different named subjects → not the same market.
                            # Allow generic words like "the", "another" to slip through.
                            generic = {"the", "there", "a", "an", "any", "another", "no one", "none"}
                            if subj_a not in generic and subj_b not in generic:
                                # Also require the last-word (often the most distinctive
                                # part of a name) is different too — handles "Apex" vs
                                # "The Crash" but lets "Tom Brady" match "Brady".
                                last_a = subj_a.split()[-1]
                                last_b = subj_b.split()[-1]
                                if last_a != last_b:
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
                            "volume_a": row_a.get("volume", None),
                            "volume_b": row_b.get("volume", None),
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
