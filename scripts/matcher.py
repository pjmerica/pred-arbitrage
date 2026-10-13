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
import sys
import pandas as pd
from pathlib import Path
from rapidfuzz import fuzz, process as fuzz_process
from datetime import datetime, timezone

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
from utils.links import polymarket_url
from utils.election_shapes import is_derivative, party_win_side
from utils.proposition import incompatibility, kalshi_game_date, slug_game_date
from scripts.elections import special_senate, title_is_2026_us_race
RAW = ROOT / "data" / "raw"
PROCESSED = ROOT / "data" / "processed"

FUZZY_THRESHOLD = 88   # token_sort_ratio score — raised to reduce candidate-vs-party false positives
MAX_CANDIDATES = 3     # fuzzy candidates to check per market

# Per-category threshold overrides. Defaults to FUZZY_THRESHOLD when a
# category isn't listed. Lower values let more pairs through; we use
# lower thresholds for non-political categories where false positives
# are cheaper (no candidate-vs-party confusion) and where the same
# event tends to be phrased differently across platforms (e.g. Kalshi
# "How high will BTC get in June" vs Polymarket "Will Bitcoin hit
# $150k by June 30"). Scrutiny + the year-overlap guard still apply.
PER_CATEGORY_THRESHOLD = {
    "crypto":      72,
    "finance":     74,
    "other":       74,   # Kalshi 'Commodities' lands here
    "science":     74,
    "companies":   74,
    "world":       74,
    "climate":     76,
    "health":      76,
    "esports":     78,
    "sports":      80,   # huge volume; keep tight
    "culture":     80,
    "politics":    FUZZY_THRESHOLD,  # unchanged
}

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
    # Other cycles (2027/2028 markets), Baja California, and the FL/OH
    # special-only Senate races: see scripts/elections.py.
    if not title_is_2026_us_race(title):
        return None
    return special_senate(_infer_race_id(title))


def _infer_race_id(title: str) -> str | None:
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
    # Old substring version matched "red" inside "hundred" and "dem " in
    # any sentence, and happily sided margin-of-victory buckets.
    side = party_win_side(question)
    if side:
        return side
    q = question.lower()
    if "which party" in q and not is_derivative(question):
        # PredictIt: "Which party will win ...? — Republican"
        tail = re.split(r"[—–]", q)[-1]
        if "democrat" in tail:
            return "dem"
        if "republican" in tail:
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
      'derivative'     — margin / closeness / round markets ("win by 0%-3%",
                         "within 5%", "closest race") — never matched
      'other'          — unrecognised; never matched
    """
    q = question.lower()

    if is_derivative(question):
        return "derivative"

    # Primary / nomination questions
    if any(k in q for k in ["nomination", "nominee", "primary", "republican nominee",
                             "democratic nominee", "gop nominee"]):
        return "primary"

    # Party-winner questions (allowlist — see utils/election_shapes.py)
    if party_side(question):
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
    # Fixture date for game markets (close_date is game + ~3 days).
    df["game_date"] = df["ticker"].map(kalshi_game_date)
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
    df["game_date"] = df["event_slug"].map(slug_game_date) if "event_slug" in df.columns else None
    # Rebuild from slugs so even a CSV scraped before the deep-link fix
    # links to the exact market rather than the multi-market event page.
    if "event_slug" in df.columns:
        df["url"] = [polymarket_url(e, m) for e, m in
                     zip(df["event_slug"], df.get("market_slug", pd.Series(index=df.index)))]
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
            # Only party-winner / candidate markets are matchable. Filter
            # BEFORE picking the best market per race: otherwise the
            # highest-liquidity market wins regardless of what it asks,
            # and since mid-2026 that's a Polymarket margin-of-victory
            # bucket (8 fake guaranteed arbs, 2026-09-23). 'other' is
            # dropped too — other↔other used to pass the type-equality
            # check below and paired "Kansas Senate winner — Hamilton"
            # with "Will the Kansas race be within 5%?".
            MATCHABLE = ("party_winner", "candidate")
            a = a[a["question"].astype(str).map(political_contract_type).isin(MATCHABLE)]
            b = b[b["question"].astype(str).map(political_contract_type).isin(MATCHABLE)]
            if a.empty or b.empty:
                continue
            filtered = {pa: a, pb: b}

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
                        sum_a = party_sum(filtered[pa], r["race_id"])
                        sum_b = party_sum(filtered[pb], r["race_id"])
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

    def _binary_only(df, platform):
        """Drop Polymarket markets whose outcomes aren't Yes/No.

        Team-vs-team moneylines (KBO / MLB / T20 / NBA …) have outcomes
        like ["Doosan Bears","Kiwoom Heroes"]; their scraped yes/no tokens
        are just tokens[0]/[1], so a YES+NO basket against them is a
        double bet on one team, not a hedge. The 2026-07-04 coverage
        expansion surfaced dozens of these as fake 10-15% "guaranteed"
        arbs before this filter.
        """
        if platform != "polymarket" or "outcomes_binary" not in df.columns:
            return df
        before = len(df)
        out = df[df["outcomes_binary"] == True].copy()  # noqa: E712 (NaN-safe)
        if before != len(out):
            print(f"    ({platform}: excluded {before - len(out)} non-Yes/No markets from fuzzy input)")
        return out

    for i, pa in enumerate(platform_names):
        for pb in platform_names[i+1:]:
            a_full = _binary_only(dfs[pa][dfs[pa]["race_id"].isna()].copy(), pa)
            b_full = _binary_only(dfs[pb][dfs[pb]["race_id"].isna()].copy(), pb)

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
                # Per-category threshold (defaults to FUZZY_THRESHOLD).
                # Non-political categories get a looser threshold because
                # the same event is often phrased differently and the
                # false-positive cost is lower than for elections.
                threshold = PER_CATEGORY_THRESHOLD.get(group, FUZZY_THRESHOLD)

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
                    results = [(b_norms[j], float(row_scores[j]), j) for j in top_idx if row_scores[j] >= threshold]

                    for norm_b_match, score, idx in results:
                        row_b = b.iloc[idx]
                        if row_a.get("market_id") == row_b.get("market_id"):
                            continue

                        # Outcome-signature gate (2026-09-23): reject when
                        # the titles disagree on placement, period,
                        # division, fixture teams/date, day-vs-window,
                        # storm category or the named person — see
                        # utils/proposition.py. This single check covers
                        # every fake-pair class in MATCHING_REVIEW.md that
                        # the per-incident guards below missed.
                        if incompatibility(str(row_a.get("question", "")),
                                           str(row_b.get("question", "")),
                                           row_a.get("game_date"), row_b.get("game_date")):
                            continue

                        # Drop if deadline years don't overlap
                        years_a = set(re.findall(r"\b(20\d{2})\b", str(row_a.get("question", ""))))
                        years_b = set(re.findall(r"\b(20\d{2})\b", str(row_b.get("question", ""))))
                        if years_a and years_b and not years_a.intersection(years_b):
                            continue

                        # Subject-flip guard (2026-07-04): when BOTH sides
                        # name a specific subject, they must agree. Kalshi
                        # "Canada vs Morocco: First Team to Score — Morocco"
                        # was fuzzy-pairing to Polymarket "Canada to score
                        # first vs. Morocco?" — OPPOSITE outcomes that score
                        # high on token_sort_ratio because both titles carry
                        # both team names. Kalshi subject = post-em-dash;
                        # Polymarket subject = leading noun phrase before
                        # "to score/win/…". Token overlap required.
                        qa_str = str(row_a.get("question", ""))
                        qb_str = str(row_b.get("question", ""))
                        m_dash = re.search(r"[—–]\s*([^—–?]+?)\s*\??\s*$", qa_str)
                        m_lead = re.match(r"^([A-Za-z0-9 .'\-]{2,40}?)\s+to\s+(?:score|win|beat|advance|qualify|lead)\b",
                                          qb_str, re.IGNORECASE)
                        if not (m_dash and m_lead):
                            # Try the reverse orientation (Polymarket as side a)
                            m_dash = re.search(r"[—–]\s*([^—–?]+?)\s*\??\s*$", qb_str)
                            m_lead = re.match(r"^([A-Za-z0-9 .'\-]{2,40}?)\s+to\s+(?:score|win|beat|advance|qualify|lead)\b",
                                              qa_str, re.IGNORECASE)
                        if m_dash and m_lead:
                            subj_dash = set(re.findall(r"[a-z0-9']+", m_dash.group(1).lower()))
                            subj_lead = set(re.findall(r"[a-z0-9']+", m_lead.group(1).lower()))
                            subj_lead -= {"the", "a", "an"}
                            subj_dash -= {"the", "a", "an"}
                            if subj_dash and subj_lead and not (subj_dash & subj_lead):
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

                        # Common-prefix subject guard. When two titles
                        # share a long common prefix, the diverging
                        # portion IS the subject (e.g. "Will Trump
                        # recognize <SUBJECT> ..."). extract_subject
                        # above only handles a fixed list of verbs
                        # (win/be/become/...) and misses Trump-recognize,
                        # Trump-pardon, Trump-fire, etc. — produced fake
                        # Taiwan/Somaliland pair in production 2026-06-24.
                        #
                        # Algorithm: find the longest common prefix of
                        # the two normalised titles (word-by-word). The
                        # NEXT word on each side is the "discriminator"
                        # — if both discriminators are alphabetic, of
                        # length >=3, and are different, the markets
                        # are about different subjects.
                        #
                        # Skips when the common prefix is too short
                        # (<3 words = no shared template, this guard
                        # isn't applicable).
                        def _tokens(s):
                            return re.findall(r"[A-Za-z0-9]+", s.lower())

                        # Key-noun / qualifier guard. Fuzzy scores
                        # 'release a new album' vs 'release a new song'
                        # at 83 — high enough to cross sports/culture
                        # thresholds — because token_sort_ratio ignores
                        # the ONE token that flips the meaning. Same for
                        # 'Fed rate cut' vs 'Fed emergency rate cut',
                        # 'Trump pardon' with vs without a year window,
                        # 'IPO first' as candidate vs binary. Each of
                        # these token sets is asymmetric: one side has a
                        # keyword the other doesn't, and that keyword
                        # changes what the market resolves on. Drop if:
                        #   - one side contains any of these keywords
                        #   - AND the other side doesn't
                        # Keywords chosen from actual false-pairs found
                        # 2026-06-28.
                        set_a = set(_tokens(q_a))
                        set_b = set(_tokens(q_b))
                        DIVERGING_KEYWORDS = {
                            # Album / song / EP / mixtape are distinct
                            # releases; markets track them separately.
                            "album", "song", "single", "ep", "mixtape",
                            # 'Emergency' rate cut is a distinct Fed
                            # action from an ordinary rate cut.
                            "emergency",
                            # 'First' / 'first-to' framing changes
                            # candidate markets (Kalshi 'A or B — A')
                            # into binaries (Polymarket 'A or B' YES for
                            # either).
                            "first",
                            # Semi/final/quarter-final ≠ tournament winner.
                            "final", "finalist", "semifinal", "semifinals",
                            "quarterfinal", "quarterfinals",
                            # Trade / buyout / merger are different
                            # corporate events from IPO / acquisition.
                            "merger", "buyout", "acquired", "traded",
                            # 'End in a draw' is a different outcome from
                            # a team winning — Kalshi moneyline "— Team"
                            # fuzzy-paired to PM "end in a draw?" at 82
                            # (fake 7% arb, 2026-07-04).
                            "draw",
                            # 'Global' vs 'US' Netflix charts are
                            # different rankings that often disagree —
                            # "#2 Global Netflix Movie" fuzzy-paired to
                            # "#2 US Netflix movie" at 82-85 (fake 3-4%
                            # arbs, 2026-07-04). 'global' alone suffices:
                            # the US-chart title is the one lacking it.
                            "global",
                        }
                        one_side_only = (set_a & DIVERGING_KEYWORDS) ^ (set_b & DIVERGING_KEYWORDS)
                        if one_side_only:
                            continue

                        # Settle-date drift guard. If both platforms
                        # report a settle date and they're more than
                        # 180 days apart, the resolution windows are
                        # different enough that the markets probably
                        # aren't asking the same thing (Kalshi 'Who
                        # will Trump pardon?' closes 2029-01-21 —
                        # end of Trump's term — vs Polymarket
                        # 'Will Trump pardon Elon Musk in 2026?'
                        # closing 2026-12-31; same subject, different
                        # window, non-tradeable as a pair).
                        try:
                            sd_a = str(row_a.get("settle_date", "") or "")[:10]
                            sd_b = str(row_b.get("settle_date", "") or "")[:10]
                            if len(sd_a) == 10 and len(sd_b) == 10:
                                from datetime import date as _date
                                da = _date.fromisoformat(sd_a)
                                db = _date.fromisoformat(sd_b)
                                if abs((da - db).days) > 180:
                                    continue
                        except (ValueError, TypeError):
                            pass  # unparseable dates fall through

                        # Year-token asymmetry guard. If one side's title
                        # mentions a specific year and the other side
                        # doesn't mention ANY year, the resolution window
                        # is different (Kalshi 'Who will Trump pardon?'
                        # has no year token but closes 2029; PredictIt
                        # 'Will Trump pardon Elon Musk in 2026?' is
                        # explicitly bounded). Same problem as the
                        # settle-date drift guard above, but works when
                        # one side has no settle_date (PredictIt often
                        # doesn't).
                        years_a = set(re.findall(r"\b(20\d{2})\b", q_a))
                        years_b = set(re.findall(r"\b(20\d{2})\b", q_b))
                        if years_a and not years_b:
                            continue
                        if years_b and not years_a:
                            continue

                        # Candidate-vs-binary subject guard. Kalshi's
                        # "candidate-style" markets put the subject
                        # AFTER an em-dash ('Who will recognize Palestine
                        # before 2027? — Italy'). Polymarket puts the
                        # subject INSIDE the question ('Will Israel
                        # recognize Palestine before 2027?'). If the
                        # subject after the em-dash doesn't share ANY
                        # significant token with the other side's title,
                        # the markets are about different subjects.
                        # Applies in both directions since either
                        # platform can carry the candidate-style
                        # phrasing.
                        _SUBJ_STOP = {'the','and','for','with','from','over','usa','uk'}
                        _subject_mismatch = False
                        for candidate_q, other_q in [(q_a, q_b), (q_b, q_a)]:
                            sep = '—' if '—' in candidate_q else (' - ' if ' - ' in candidate_q else None)
                            if not sep:
                                continue
                            subject = candidate_q.rsplit(sep, 1)[-1].strip()
                            if len(subject) < 3:
                                continue
                            subject_tokens = {t for t in _tokens(subject)
                                              if len(t) >= 3 and t not in _SUBJ_STOP}
                            if not subject_tokens:
                                continue
                            if not (subject_tokens & set(_tokens(other_q))):
                                _subject_mismatch = True
                                break
                        if _subject_mismatch:
                            continue

                        # Range-vs-point count guard. Kalshi 'How many
                        # ... — 5' vs Polymarket 'Will 5-6 ... successfully
                        # reach' are different resolution rules (Polymarket
                        # bucket covers 5-or-6; Kalshi contract covers
                        # exactly 5). Detect: one side has an "N-M" range
                        # (or "N or more", "N to M") and the other has
                        # just a single integer.
                        _has_range_a = bool(re.search(r'\b\d+\s*[-–—]\s*\d+\b|\b\d+\s+or\s+more\b|\b\d+\s+to\s+\d+\b', q_a))
                        _has_range_b = bool(re.search(r'\b\d+\s*[-–—]\s*\d+\b|\b\d+\s+or\s+more\b|\b\d+\s+to\s+\d+\b', q_b))
                        if _has_range_a != _has_range_b:
                            # One side is a range, other is a point.
                            # Only enforce when the pair looks like a
                            # "counting" market (mentions 'how many'
                            # or 'launches' or similar) — otherwise
                            # legit matches like 'FL-19' district codes
                            # would false-trip.
                            combined_q = (q_a + ' ' + q_b).lower()
                            if any(kw in combined_q for kw in ['how many','launches','deliver','vehicles','contracts']):
                                continue

                        ta = _tokens(q_a)
                        tb = _tokens(q_b)
                        common = 0
                        while common < min(len(ta), len(tb)) and ta[common] == tb[common]:
                            common += 1
                        if common >= 3 and common < min(len(ta), len(tb)):
                            disc_a = ta[common]
                            disc_b = tb[common]
                            # Stopwords that don't carry subject info even
                            # when they happen to be the diverging token.
                            stop = {"a","an","the","be","is","will","do","does",
                                    "on","in","at","to","for","by","of","before",
                                    "after","or","and","with","next","this","that"}
                            if (disc_a != disc_b
                                    and disc_a.isalpha() and disc_b.isalpha()
                                    and len(disc_a) >= 3 and len(disc_b) >= 3
                                    and disc_a not in stop and disc_b not in stop):
                                # Different discriminator tokens after a
                                # 3+ word common prefix — different subjects.
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


# ── Threshold-comparison matcher (crypto/commodities) ──────────────────────
# Both Kalshi and Polymarket publish "will <asset> hit/reach $X by <date>"
# style markets. The semantics are identical but titles differ enough that
# fuzzy matching misses them. This matcher parses (asset, direction,
# strike, settlement_month) from each title, then pairs Kalshi+Polymarket
# markets sharing the (asset, direction, month) tuple with strikes within
# tolerance. Tolerance is 0.1% of strike (min 1¢) — just enough for
# Kalshi's "$149,999.99" vs Polymarket's "$150,000"; anything wider spans
# real ladder rungs and pairs different questions.

_PRICE_ASSETS = {
    'bitcoin': 'BTC',  'btc': 'BTC',
    'ethereum': 'ETH', 'eth': 'ETH',
    'solana': 'SOL',   'sol': 'SOL',
    'xrp': 'XRP', 'bnb': 'BNB', 'hype': 'HYPE',
    'oil': 'OIL', 'wti': 'OIL', 'crude': 'OIL', 'brent': 'BRENT',
    'gold': 'GOLD', 'silver': 'SILVER', 'copper': 'COPPER',
    'natural gas': 'NATGAS', 'nat gas': 'NATGAS',
}
_MONTHS_FULL = ['january','february','march','april','may','june',
                'july','august','september','october','november','december']


def _extract_threshold_key(text: str, src: str):
    """Parse (asset, direction, strike, month) from a price-threshold title.
    Returns None for titles that don't match the threshold pattern."""
    if not text or pd.isna(text):
        return None
    t = str(text).lower()
    if src == 'kalshi':
        if 'how high' in t or 'or above' in t: direction = 'above'
        elif 'how low' in t or 'or below' in t: direction = 'below'
        else: return None
    else:  # polymarket
        if 'hit' in t:
            if '(high)' in t: direction = 'above'
            elif '(low)' in t: direction = 'below'
            else: return None
        elif 'reach' in t: direction = 'above'
        elif 'dip to' in t: direction = 'below'
        else: return None
    asset = next((v for k, v in _PRICE_ASSETS.items() if k in t), None)
    if not asset:
        return None
    # Strike: prefer $-prefixed, else any number after the em-dash
    m = re.search(r'\$\s*([\d,]+(?:\.\d+)?)', text)
    if not m:
        m = re.search(r'[—–\-]\s*\$?\s*([\d,]+(?:\.\d+)?)', text)
    if not m:
        return None
    try:
        strike = float(m.group(1).replace(',', ''))
    except ValueError:
        return None
    # Window bucket (2026-09-25). Year-to-date / year-end windows ("in 2026",
    # "by December 31, 2026", "by end of December", "before 2027", "this
    # year") get their OWN bucket 'year'. They used to share 'dec' with a
    # December-ONLY market ("reach $X in December?"), so a full-year Kalshi
    # market could pair with a December-only Polymarket one: a strike hit
    # in October wins Kalshi YES and can lose the December leg (both legs
    # lose). Month names now match on word boundaries — the old substring
    # test counted "market" as March and "decline" as December.
    if re.search(r'\b(?:dec(?:ember)?\.?\s+31|end of dec(?:ember)?|this year|(?:in|by|before) 20\d\d)\b', t):
        month = 'year'
    else:
        m_mo = re.search(r'\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|'
                         r'aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\b\.?', t)
        month = m_mo.group(1)[:3] if m_mo else None
    if not month:
        return None
    # Day-of-month anchor (2026-07-04): month-only matching paired Kalshi
    # "BTC price on Jul 10 at 5pm" with Polymarket "reach $64,000 on
    # July 4?" — same month, different days, fake 12.5% arb. If a
    # "<month> <day>" mention exists, it becomes part of the key; a
    # point-in-time daily market (has day) never matches a month/year
    # bucket (day=None). Exception: "december 31" year-end phrasing is
    # the same as the year bucket — normalize it to None.
    day = None
    m_day = re.search(r'\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})\b', t)
    if m_day:
        day = int(m_day.group(2))
        if month == 'year':
            day = None   # "December 31, 2026" is the year-end, not a daily market
    # Touch vs level semantics (2026-07-04): Kalshi "BTC price on Jul 4
    # at 5pm — $63,750 or above" settles on the price AT a timestamp;
    # Polymarket "reach $64,000 on July 4" settles if the price TOUCHES
    # the strike at any point. Touch-in-morning + fall-by-5pm loses both
    # legs of the basket (fake 11.6% arb). "how high/low will X get" and
    # reach/dip/hit are all touch; "price on/at <time>" is level.
    kind = 'level' if re.search(r'\bprice (?:on|at)\b', t) else 'touch'
    return (asset, direction, strike, month, day, kind)


def match_threshold_pairs(dfs: dict) -> pd.DataFrame:
    """Match Kalshi vs Polymarket price-threshold markets (e.g. 'will BTC
    reach $X by date'). Skips PredictIt — no price-threshold markets
    there. Same row schema as match_fuzzy so the union is uniform."""
    from collections import defaultdict
    pairs = []
    if 'kalshi' not in dfs or 'polymarket' not in dfs:
        return pd.DataFrame()
    k = dfs['kalshi']
    p = dfs['polymarket']
    if k.empty or p.empty:
        return pd.DataFrame()

    # Index each platform by (asset, direction, month).
    k_idx = defaultdict(list)
    p_idx = defaultdict(list)
    for _, r in k.iterrows():
        key = _extract_threshold_key(r.get('question', ''), 'kalshi')
        if key:
            k_idx[(key[0], key[1], key[3], key[4], key[5])].append((key[2], r))
    for _, r in p.iterrows():
        key = _extract_threshold_key(r.get('question', ''), 'polymarket')
        if key:
            p_idx[(key[0], key[1], key[3], key[4], key[5])].append((key[2], r))

    print(f"  Threshold-keyed: kalshi={sum(len(v) for v in k_idx.values())}, "
          f"polymarket={sum(len(v) for v in p_idx.values())}")

    matched = 0
    for group_key in set(k_idx) & set(p_idx):
        asset, direction, month, _day, _kind = group_key
        # For each Polymarket strike, find the SINGLE closest Kalshi strike
        # within tolerance. Polymarket-first because their strike list is
        # typically smaller and uses clean round numbers.
        for p_strike, p_row in p_idx[group_key]:
            # Percentage tolerance absorbs rounding drift between the two
            # platforms' strike ladders (Kalshi "$1,250.00" vs Polymarket
            # "$1,250"). The old floor was max(2.0, …) — an absolute $2
            # window, which is wider than the ENTIRE strike ladder for a
            # low-priced asset. XRP trades ~$2-4 with $0.20 ladder steps,
            # so Kalshi "above $2.00" matched Polymarket "reach $4.00"
            # (distance $2.00 <= tolerance $2.00) and every rung between,
            # producing 8 fake guaranteed arbs that topped the board at
            # 13.6% — the two legs are genuinely different questions.
            # Floor is now relative, so it scales down with the strike.
            # 2026-09-23: 2% was still wider than real ladder steps — ETH
            # "$3,250" paired with PM "$3,300" AND "$3,200" (1.5% apart).
            # The only drift to absorb is Kalshi's cent-below convention
            # ("$149,999.99" == "$150,000"), so 0.1% (min 1¢) suffices.
            # Applies to commodities too: the old flat $1.50 oil window
            # spanned adjacent $1 WTI rungs.
            tolerance = max(abs(p_strike) * 0.001, 0.01)
            best = None
            for k_strike, k_row in k_idx[group_key]:
                d = abs(k_strike - p_strike)
                if d <= tolerance and (best is None or d < best[0]):
                    best = (d, k_strike, k_row)
            if not best:
                continue
            _, k_strike, k_row = best
            pairs.append({
                "match_type": "threshold",
                "race_id": None,
                "platform_a": "kalshi",
                "platform_b": "polymarket",
                "question_a": k_row.get("question", ""),
                "question_b": p_row.get("question", ""),
                "market_id_a": k_row.get("market_id", ""),
                "market_id_b": p_row.get("market_id", ""),
                "implied_prob_a": k_row.get("implied_prob"),
                "implied_prob_b": p_row.get("implied_prob"),
                "settle_date": p_row.get("settle_date") or k_row.get("settle_date") or "",
                "category": k_row.get("category", ""),
                "url_a": k_row.get("url", ""),
                "url_b": p_row.get("url", ""),
                "volume_a": k_row.get("volume"),
                "volume_b": p_row.get("volume"),
                "fuzzy_score": 100,  # exact-key match in our taxonomy
            })
            matched += 1

    print(f"  Threshold pairs matched: {matched}")
    return pd.DataFrame(pairs)


# ── Tournament-winner matcher ──────────────────────────────────────────────
# Tennis Kalshi:  "Wimbledon Men's Singles Winner — Jannik Sinner"
# Tennis Poly:    "Will Jannik Sinner be the 2026 Men's Wimbledon Winner?"
# Soccer Kalshi:  "2026 FIFA World Cup Winner — Mexico"
# Soccer Poly:    "Will Spain win the 2026 FIFA World Cup?"
# Same (event_class, gender_or_country, contestant) → match. Fuzzy alone
# can't reliably catch these — tennis scores 76-78 (below sports threshold
# 80), and the naming styles are too divergent for token_sort_ratio to
# bridge ('US Open Men's Singles Winner' vs "Men's US Open"). Dedicated
# extractors give 100% precision when they match.

def _strip_diacritics(s: str) -> str:
    """Normalize a name so 'Iga Świątek' matches 'Iga Swiatek' and
    'felix auger-aliassime' matches 'felix auger aliassime'.

    Polymarket keeps the diacritics ('Świątek', 'João Fonseca'),
    Kalshi drops them. Kalshi keeps the hyphens ('Auger-Aliassime'),
    Polymarket sometimes drops them. NFD + strip combining marks +
    normalize hyphens to spaces + collapse whitespace handles both."""
    import unicodedata
    if not s:
        return s
    s = ''.join(
        c for c in unicodedata.normalize('NFD', s)
        if unicodedata.category(c) != 'Mn'
    )
    s = s.replace('-', ' ').replace('—', ' ').replace('–', ' ')
    return re.sub(r'\s+', ' ', s).strip()


def _kalshi_tennis_key(title):
    if not isinstance(title, str):
        return None
    m = re.match(
        r'^(.+?)\s+(Men|Women)(?:\'s)?\s+Singles\s+Winner\s*[—\-]\s*(.+)$',
        title,
    )
    if not m:
        return None
    tournament = _strip_diacritics(m.group(1).strip().lower())
    gender = m.group(2).lower()
    player = _strip_diacritics(m.group(3).strip().lower())
    return (tournament, gender, player)


def _poly_tennis_key(question):
    if not isinstance(question, str):
        return None
    # Two phrasings: "Will X win the 2026 Men's Y?" and "Will X be the
    # 2026 Men's Y Winner?". Strip the optional "winner" suffix in the
    # tournament group below.
    m = re.search(
        r'^Will\s+(.+?)\s+(?:win|be)\s+(?:the\s+)?(?:2026\s+)?(Men|Women)(?:\'s)?\s+(.+?)\??$',
        question, re.IGNORECASE,
    )
    if not m:
        return None
    player = _strip_diacritics(m.group(1).strip().lower())
    gender = m.group(2).lower()
    tournament = _strip_diacritics(
        re.sub(r'\s+winner\s*$', '', m.group(3).strip(), flags=re.IGNORECASE).lower()
    )
    return (tournament, gender, player)


# ── Primary nominee matcher (US elections) ─────────────────────────────────
# Polymarket has hundreds of "Will <person> be the <party> nominee for
# <race>?" markets for 2026 primaries, settling August/September 2026.
# Kalshi has matching "<state> <party> Senate nominee? — <person>" and
# "<state>-<dist> <party> nominee? — <person>" markets. Fuzzy scores
# 87-90 which is just below politics threshold (88). Dedicated matcher
# keys on (race_id, party, person) — race_id prevents false matches
# (e.g. Sharice Davids Kansas Senate vs Sharice Davids KS-03 House).

_STATE_NAME_TO_ABBR = {
    'alabama':'AL','alaska':'AK','arizona':'AZ','arkansas':'AR',
    'california':'CA','colorado':'CO','connecticut':'CT','delaware':'DE',
    'florida':'FL','georgia':'GA','hawaii':'HI','idaho':'ID',
    'illinois':'IL','indiana':'IN','iowa':'IA','kansas':'KS',
    'kentucky':'KY','louisiana':'LA','maine':'ME','maryland':'MD',
    'massachusetts':'MA','michigan':'MI','minnesota':'MN','mississippi':'MS',
    'missouri':'MO','montana':'MT','nebraska':'NE','nevada':'NV',
    'new hampshire':'NH','new jersey':'NJ','new mexico':'NM','new york':'NY',
    'north carolina':'NC','north dakota':'ND','ohio':'OH','oklahoma':'OK',
    'oregon':'OR','pennsylvania':'PA','rhode island':'RI','south carolina':'SC',
    'south dakota':'SD','tennessee':'TN','texas':'TX','utah':'UT',
    'vermont':'VT','virginia':'VA','washington':'WA','west virginia':'WV',
    'wisconsin':'WI','wyoming':'WY',
}


def _norm_person(s):
    """Lowercase, strip diacritics, normalize separators. For name
    comparison only — don't apply to race_ids since they need hyphens."""
    import unicodedata
    if not s:
        return s
    s = ''.join(c for c in unicodedata.normalize('NFD', s)
                if unicodedata.category(c) != 'Mn')
    s = s.replace('-', ' ').replace('—', ' ').replace('–', ' ')
    return re.sub(r'\s+', ' ', s).strip().lower()


def _kalshi_nominee_key(title):
    """Return (race_id, party_3char, person) or None."""
    if not isinstance(title, str):
        return None
    # Pattern A: '<State> <Party> <Senate|Governor> nominee? — <Person>'
    m = re.match(
        r'^(.+?)\s+(Democratic|Republican)\s+(Senate|Governor|Gubernatorial)\s+nominee\??\s*[—\-]\s*(.+?)$',
        title, re.IGNORECASE,
    )
    if m:
        state = m.group(1).strip().lower()
        party = m.group(2).lower()[:3]
        office = m.group(3).lower()
        abbr = _STATE_NAME_TO_ABBR.get(state)
        if not abbr:
            return None
        race = f"{abbr}-{'SEN' if office == 'senate' else 'GOV'}"
        return (race, party, _norm_person(m.group(4)))
    # Pattern B: '<XX>-<NN> <Party> nominee? — <Person>'
    m = re.match(
        r'^([A-Z]{2})-(\d+)\s+(Democratic|Republican)\s+nominee\??\s*[—\-]\s*(.+?)$',
        title, re.IGNORECASE,
    )
    if m:
        race = f"{m.group(1).upper()}-{int(m.group(2)):02d}"
        party = m.group(3).lower()[:3]
        return (race, party, _norm_person(m.group(4)))
    return None


def _poly_nominee_key(question):
    """Return (race_id, party_3char, person) or None."""
    if not isinstance(question, str):
        return None
    m = re.match(
        r'^Will\s+(.+?)\s+be\s+the\s+(Democratic|Republican)\s+nominee\s+for\s+(.+?)\??$',
        question, re.IGNORECASE,
    )
    if not m:
        return None
    person = _norm_person(m.group(1))
    party = m.group(2).lower()[:3]
    race_str = m.group(3).strip()
    # 'Senate in <State>'
    rm = re.match(r'^Senate\s+in\s+(.+?)$', race_str, re.IGNORECASE)
    if rm:
        abbr = _STATE_NAME_TO_ABBR.get(rm.group(1).strip().lower())
        if not abbr:
            return None
        return (f"{abbr}-SEN", party, person)
    # '<State> Governor'
    rm = re.match(r'^(.+?)\s+(Governor|Gubernatorial)$', race_str, re.IGNORECASE)
    if rm:
        abbr = _STATE_NAME_TO_ABBR.get(rm.group(1).strip().lower())
        if not abbr:
            return None
        return (f"{abbr}-GOV", party, person)
    # '<XX>-<NN>'
    rm = re.match(r'^([A-Z]{2})-(\d+)$', race_str, re.IGNORECASE)
    if rm:
        return (f"{rm.group(1).upper()}-{int(rm.group(2)):02d}", party, person)
    return None


def match_primary_nominee(dfs: dict) -> pd.DataFrame:
    """Match Kalshi vs Polymarket 2026 primary nominee markets on
    (race_id, party, person) tuples. Skips fuzzy — exact-key only.
    Race_id includes office + state + district so same name in
    different races (Sharice Davids Senate vs KS-03 House) doesn't
    collide."""
    if 'kalshi' not in dfs or 'polymarket' not in dfs:
        return pd.DataFrame()
    k = dfs['kalshi']; p = dfs['polymarket']
    if k.empty or p.empty:
        return pd.DataFrame()
    k_idx, p_idx = {}, {}
    for _, r in k.iterrows():
        key = _kalshi_nominee_key(r.get('question', ''))
        if key:
            k_idx[key] = r
    for _, r in p.iterrows():
        key = _poly_nominee_key(r.get('question', ''))
        if key:
            p_idx[key] = r
    overlap = set(k_idx) & set(p_idx)
    print(f"  nominee: kalshi={len(k_idx)}, polymarket={len(p_idx)}, overlap={len(overlap)}")
    pairs = []
    for key in overlap:
        k_row = k_idx[key]; p_row = p_idx[key]
        pairs.append({
            "match_type": "primary-nominee",
            "race_id": key[0],
            "platform_a": "kalshi",
            "platform_b": "polymarket",
            "question_a": k_row.get("question", ""),
            "question_b": p_row.get("question", ""),
            "market_id_a": k_row.get("market_id", ""),
            "market_id_b": p_row.get("market_id", ""),
            "implied_prob_a": k_row.get("implied_prob"),
            "implied_prob_b": p_row.get("implied_prob"),
            "settle_date": p_row.get("settle_date") or k_row.get("settle_date") or "",
            "category": k_row.get("category", ""),
            "url_a": k_row.get("url", ""),
            "url_b": p_row.get("url", ""),
            "volume_a": k_row.get("volume"),
            "volume_b": p_row.get("volume"),
            "fuzzy_score": 100,
        })
    return pd.DataFrame(pairs)


def _kalshi_worldcup_key(title):
    if not isinstance(title, str):
        return None
    m = re.match(
        r'^(?:20\d{2}\s+)?FIFA World Cup Winner\s*[—\-]\s*(.+?)$',
        title, re.IGNORECASE,
    )
    if not m:
        return None
    return ('fifa-world-cup', _strip_diacritics(m.group(1).strip().lower()))


def _poly_worldcup_key(question):
    if not isinstance(question, str):
        return None
    m = re.match(
        r'^Will\s+(.+?)\s+win\s+(?:the\s+)?(?:20\d{2}\s+)?FIFA World Cup\??$',
        question, re.IGNORECASE,
    )
    if not m:
        return None
    return ('fifa-world-cup', _strip_diacritics(m.group(1).strip().lower()))


def match_tournament_winner(dfs: dict) -> pd.DataFrame:
    """Match Kalshi vs Polymarket tournament-winner markets on
    extracted-key tuples (skips fuzzy — keys are precise enough). Covers
    tennis Grand Slams (with gender) and the FIFA World Cup (without).
    Add new tournaments by writing a (_kalshi_X_key, _poly_X_key) pair
    of extractors and appending to EXTRACTORS below."""
    if 'kalshi' not in dfs or 'polymarket' not in dfs:
        return pd.DataFrame()
    k = dfs['kalshi']; p = dfs['polymarket']
    if k.empty or p.empty:
        return pd.DataFrame()

    EXTRACTORS = [
        ("tennis", _kalshi_tennis_key, _poly_tennis_key),
        ("worldcup", _kalshi_worldcup_key, _poly_worldcup_key),
    ]

    pairs = []
    for tag, k_extract, p_extract in EXTRACTORS:
        k_idx, p_idx = {}, {}
        for _, r in k.iterrows():
            key = k_extract(r.get('question', ''))
            if key:
                k_idx[key] = r
        for _, r in p.iterrows():
            key = p_extract(r.get('question', ''))
            if key:
                p_idx[key] = r
        overlap = set(k_idx) & set(p_idx)
        print(f"  {tag}: kalshi={len(k_idx)}, polymarket={len(p_idx)}, overlap={len(overlap)}")
        for key in overlap:
            k_row = k_idx[key]; p_row = p_idx[key]
            pairs.append({
                "match_type": f"tournament-{tag}",
                "race_id": None,
                "platform_a": "kalshi",
                "platform_b": "polymarket",
                "question_a": k_row.get("question", ""),
                "question_b": p_row.get("question", ""),
                "market_id_a": k_row.get("market_id", ""),
                "market_id_b": p_row.get("market_id", ""),
                "implied_prob_a": k_row.get("implied_prob"),
                "implied_prob_b": p_row.get("implied_prob"),
                "settle_date": p_row.get("settle_date") or k_row.get("settle_date") or "",
                "category": k_row.get("category", ""),
                "url_a": k_row.get("url", ""),
                "url_b": p_row.get("url", ""),
                "volume_a": k_row.get("volume"),
                "volume_b": p_row.get("volume"),
                "fuzzy_score": 100,
            })
    print(f"  Tournament pairs matched: {len(pairs)}")
    league = match_league_futures(dfs)
    if len(league):
        return pd.concat([pd.DataFrame(pairs), league], ignore_index=True)
    return pd.DataFrame(pairs)


# ── US league championship futures (2026-10-13) ───────────────────────────
# Kalshi "2027 Pro Basketball Champion — Boston" (KXNBA-27-BOS) and
# Polymarket "Will Boston Celtics win the 2027 NBA Finals?" are the same
# proposition, but Kalshi's trademark-free wording ("Pro Basketball") and
# city-only names keep them far below the fuzzy cutoff — none of the ~100
# open futures were paired. Rules read side by side (NBA, NHL, MLB, NFL,
# WNBA, CFP): both resolve on the league's official champion for the same
# season. Polymarket alone resolves "Other" if the final is postponed past
# a stated date; rules_window reads that deadline and the Kalshi close date
# is the other leg's end, so only YES-on-Kalshi baskets pass the window
# check.
#
# Key: (league, season year, team). Kalshi's team is the ticker suffix,
# looked up in a per-league abbreviation table; Polymarket's team is the
# full name in the question. A second guard checks Kalshi's own team label
# ("Los Angeles L", "Ohio St.") against the Polymarket name, so a reused
# or reassigned abbreviation can't pair the wrong team.
_LEAGUE_TEAMS = {
    "nba": {
        "ATL": "atlanta hawks", "BOS": "boston celtics", "BKN": "brooklyn nets",
        "CHA": "charlotte hornets", "CHI": "chicago bulls", "CLE": "cleveland cavaliers",
        "DAL": "dallas mavericks", "DEN": "denver nuggets", "DET": "detroit pistons",
        "GSW": "golden state warriors", "HOU": "houston rockets", "IND": "indiana pacers",
        "LAC": "los angeles clippers", "LAL": "los angeles lakers", "MEM": "memphis grizzlies",
        "MIA": "miami heat", "MIL": "milwaukee bucks", "MIN": "minnesota timberwolves",
        "NOP": "new orleans pelicans", "NYK": "new york knicks", "OKC": "oklahoma city thunder",
        "ORL": "orlando magic", "PHI": "philadelphia 76ers", "PHX": "phoenix suns",
        "POR": "portland trail blazers", "SAC": "sacramento kings", "SAS": "san antonio spurs",
        "TOR": "toronto raptors", "UTA": "utah jazz", "WAS": "washington wizards",
    },
    "wnba": {
        "ATL": "atlanta dream", "CHI": "chicago sky", "CON": "connecticut sun",
        "DAL": "dallas wings", "GS": "golden state valkyries", "IND": "indiana fever",
        "LV": "las vegas aces", "LA": "los angeles sparks", "MIN": "minnesota lynx",
        "NY": "new york liberty", "PHX": "phoenix mercury", "SEA": "seattle storm",
        "WAS": "washington mystics", "TOR": "toronto tempo", "POR": "portland fire",
    },
    "nhl": {
        "ANA": "anaheim ducks", "BOS": "boston bruins", "BUF": "buffalo sabres",
        "CGY": "calgary flames", "CAR": "carolina hurricanes", "CHI": "chicago blackhawks",
        "COL": "colorado avalanche", "CBJ": "columbus blue jackets", "DAL": "dallas stars",
        "DET": "detroit red wings", "EDM": "edmonton oilers", "FLA": "florida panthers",
        "LA": "los angeles kings", "MIN": "minnesota wild", "MTL": "montreal canadiens",
        "NSH": "nashville predators", "NJ": "new jersey devils", "NYI": "new york islanders",
        "NYR": "new york rangers", "OTT": "ottawa senators", "PHI": "philadelphia flyers",
        "PIT": "pittsburgh penguins", "SJ": "san jose sharks", "SEA": "seattle kraken",
        "STL": "st. louis blues", "TB": "tampa bay lightning", "TOR": "toronto maple leafs",
        "UTA": "utah mammoth", "VAN": "vancouver canucks", "VGK": "vegas golden knights",
        "WSH": "washington capitals", "WPG": "winnipeg jets",
    },
    "mlb": {
        "ARI": "arizona diamondbacks", "ATL": "atlanta braves", "BAL": "baltimore orioles",
        "BOS": "boston red sox", "CHC": "chicago cubs", "CWS": "chicago white sox",
        "CIN": "cincinnati reds", "CLE": "cleveland guardians", "COL": "colorado rockies",
        "DET": "detroit tigers", "HOU": "houston astros", "KC": "kansas city royals",
        "LAA": "los angeles angels", "LAD": "los angeles dodgers", "MIA": "miami marlins",
        "MIL": "milwaukee brewers", "MIN": "minnesota twins", "NYM": "new york mets",
        "NYY": "new york yankees", "PHI": "philadelphia phillies", "PIT": "pittsburgh pirates",
        "SD": "san diego padres", "SF": "san francisco giants", "SEA": "seattle mariners",
        "STL": "st. louis cardinals", "TB": "tampa bay rays", "TEX": "texas rangers",
        "TOR": "toronto blue jays", "WSH": "washington nationals",
    },
    "nfl": {
        "ARI": "arizona cardinals", "ATL": "atlanta falcons", "BAL": "baltimore ravens",
        "BUF": "buffalo bills", "CAR": "carolina panthers", "CHI": "chicago bears",
        "CIN": "cincinnati bengals", "CLE": "cleveland browns", "DAL": "dallas cowboys",
        "DEN": "denver broncos", "DET": "detroit lions", "GB": "green bay packers",
        "HOU": "houston texans", "IND": "indianapolis colts", "JAC": "jacksonville jaguars",
        "KC": "kansas city chiefs", "LV": "las vegas raiders", "LAC": "los angeles chargers",
        "LAR": "los angeles rams", "MIA": "miami dolphins", "MIN": "minnesota vikings",
        "NE": "new england patriots", "NO": "new orleans saints", "NYG": "new york giants",
        "NYJ": "new york jets", "PHI": "philadelphia eagles", "PIT": "pittsburgh steelers",
        "SF": "san francisco 49ers", "SEA": "seattle seahawks", "TB": "tampa bay buccaneers",
        "TEN": "tennessee titans", "WAS": "washington commanders",
    },
    # College: programs Kalshi or Polymarket list now; add as more appear.
    # "Miami (FL)" is the Hurricanes (Miami (OH) is the RedHawks); Kalshi's
    # "Ole Miss" is Polymarket's "Mississippi Rebels".
    "cfp": {
        "OSU": "ohio state buckeyes", "TEX": "texas longhorns", "ND": "notre dame fighting irish",
        "IND": "indiana hoosiers", "UGA": "georgia bulldogs", "LSU": "lsu tigers",
        "TTU": "texas tech red raiders", "MIA": "miami hurricanes", "ALA": "alabama crimson tide",
        "MISS": "mississippi rebels", "UTAH": "utah utes", "UCLA": "ucla bruins",
        "ORE": "oregon ducks", "TAMU": "texas a&m aggies", "OU": "oklahoma sooners",
        "MICH": "michigan wolverines", "USC": "usc trojans", "FLA": "florida gators",
    },
}

# Kalshi series -> (league, phrase its title must contain).
_KALSHI_LEAGUE_SERIES = {
    "KXNBA": ("nba", "pro basketball champion"),
    "KXWNBA": ("wnba", "women's pro basketball champion"),
    "KXNHL": ("nhl", "stanley cup"),
    "KXMLB": ("mlb", "pro baseball champion"),
    "KXSB": ("nfl", "pro football champion"),
    "KXNCAAF": ("cfp", "college football national championship"),
}

# Polymarket question -> league, team name, season (the year the final is
# played; NHL "2026-27" -> 27).
_POLY_LEAGUE_Q = [
    ("nba", re.compile(r"^Will (?:the )?(.+?) win the (20\d\d) NBA Finals\?$", re.I)),
    ("wnba", re.compile(r"^Will (?:the )?(.+?) win the (20\d\d) WNBA Finals\?$", re.I)),
    ("nhl", re.compile(r"^Will (?:the )?(.+?) be named the 20\d\d-(\d\d) NHL Stanley Cup Champion\?$", re.I)),
    ("mlb", re.compile(r"^Will (?:the )?(.+?) win the (20\d\d) World Series\?$", re.I)),
    ("nfl", re.compile(r"^Will (?:the )?(.+?) win the (20\d\d) NFL league championship\?$", re.I)),
    ("cfp", re.compile(r"^Will (?:the )?(.+?) win the (20\d\d) CFP National Championship\?$", re.I)),
]


def _team_norm(s):
    s = _strip_diacritics(str(s or "")).lower().strip()
    return re.sub(r"\s+", " ", s)


def _kalshi_label_fits(label, full_name):
    """Kalshi's team label must describe the Polymarket team: a prefix of
    it ("Boston" / "Boston Celtics"; "Ohio St." / "Ohio State Buckeyes")
    or city + nickname initial ("Los Angeles L" / "Los Angeles Lakers")."""
    lab = _team_norm(label); full = _team_norm(full_name)
    lab = {"ole miss": "mississippi", "miami (fl)": "miami"}.get(lab, lab)
    # College "St." means State ("Ohio St."); NHL/MLB "St. Louis" is a city.
    lab = re.sub(r"(?<=\w) st\.?$", " state", lab)
    if not lab:
        return False
    if full == lab or full.startswith(lab + " "):
        return True
    m = re.match(r"^(.+) ([a-z])$", lab)
    if m and full.startswith(m.group(1) + " "):
        return full[len(m.group(1)) + 1:][:1] == m.group(2)
    return False


def _kalshi_league_key(row):
    t = str(row.get("market_id") or "")
    m = re.match(r"^(KX[A-Z]+)-(\d\d)-([A-Z]+)$", t)
    if not m or m.group(1) not in _KALSHI_LEAGUE_SERIES:
        return None
    league, phrase = _KALSHI_LEAGUE_SERIES[m.group(1)]
    title = _strip_diacritics(str(row.get("question") or "")).lower()
    if phrase not in title:
        return None
    # A year in the title ("2027 Pro Basketball", "2026-27 Stanley Cup")
    # must agree with the ticker's season.
    ty = re.search(r"\b20(\d\d)(?:[- ](\d\d))?\b", title)  # hyphen -> space above
    if ty and (ty.group(2) or ty.group(1)) != m.group(2):
        return None
    team = _LEAGUE_TEAMS[league].get(m.group(3))
    if not team or not _kalshi_label_fits(row.get("yes_sub_title"), team):
        return None
    return (league, m.group(2), team)


def _poly_league_key(question):
    q = str(question or "").strip()
    for league, rx in _POLY_LEAGUE_Q:
        m = rx.match(q)
        if m:
            name = _team_norm(m.group(1))
            if name in _LEAGUE_TEAMS[league].values():
                return (league, m.group(2)[-2:], name)
            return None
    return None


def match_league_futures(dfs: dict) -> pd.DataFrame:
    """Kalshi vs Polymarket US-league champion futures, keyed on
    (league, season, team). See the block comment above."""
    if 'kalshi' not in dfs or 'polymarket' not in dfs:
        return pd.DataFrame()
    k = dfs['kalshi']; p = dfs['polymarket']
    if k.empty or p.empty:
        return pd.DataFrame()
    k_idx, p_idx = {}, {}
    ks = k[k['market_id'].astype(str).str.match(r"^KX(?:NBA|WNBA|NHL|MLB|SB|NCAAF)-\d\d-")]
    for _, r in ks.iterrows():
        key = _kalshi_league_key(r)
        if key:
            k_idx.setdefault(key, []).append(r)
    for _, r in p.iterrows():
        key = _poly_league_key(r.get('question', ''))
        if key:
            p_idx.setdefault(key, []).append(r)
    pairs = []
    for key in sorted(set(k_idx) & set(p_idx)):
        # Exactly one market per side, or the key isn't precise enough.
        if len(k_idx[key]) != 1 or len(p_idx[key]) != 1:
            print(f"  league futures: ambiguous key {key} ({len(k_idx[key])} Kalshi, "
                  f"{len(p_idx[key])} Polymarket) - skipped")
            continue
        k_row = k_idx[key][0]; p_row = p_idx[key][0]
        pairs.append({
            "match_type": "tournament-league",
            "race_id": None,
            "platform_a": "kalshi",
            "platform_b": "polymarket",
            "question_a": k_row.get("question", ""),
            "question_b": p_row.get("question", ""),
            "market_id_a": k_row.get("market_id", ""),
            "market_id_b": p_row.get("market_id", ""),
            "implied_prob_a": k_row.get("implied_prob"),
            "implied_prob_b": p_row.get("implied_prob"),
            "settle_date": p_row.get("settle_date") or k_row.get("settle_date") or "",
            "category": k_row.get("category", ""),
            "url_a": k_row.get("url", ""),
            "url_b": p_row.get("url", ""),
            "volume_a": k_row.get("volume"),
            "volume_b": p_row.get("volume"),
            "fuzzy_score": 100,
        })
    print(f"  league futures: kalshi={len(k_idx)}, polymarket={len(p_idx)}, pairs={len(pairs)}")
    return pd.DataFrame(pairs)


# ── US-league games, tennis matches, UFC fights (2026-10-13) ─────────────
# Kalshi lists one market per side ("Oklahoma City vs San Antonio — Oklahoma
# City"); Polymarket one two-outcome market per game ("Thunder vs. Spurs",
# tokens in the order the sides are listed, so its "YES" token is the first
# side and its "NO" token the second). Titles share almost no words (city
# vs nickname), so these are keyed structurally: sport + game date + both
# sides, and the Polymarket game is paired with the Kalshi market for its
# FIRST-listed side.
#
# Rules read 2026-10-13 (NFL, NBA, MLB, NHL, ATP/WTA, UFC; both sides):
# the named side wins the game / match / fight, overtime included; NFL and
# UFC ties / draws / no contests pay $0.50 on Kalshi and resolve 50-50 on
# Polymarket, so a hedged basket still collects $1. Tail caveat: if the game
# is cancelled or not started within 48 h (tennis: walkover), Kalshi pays a
# discretionary "fair price" and Polymarket 50-50 — the same caveat as the
# approved soccer families. A tennis retirement pays the player who
# advances on both.
#
# Dates: US leagues need the exact date (playoff series play on
# consecutive days, so ±1 could pair the wrong game); tennis and UFC
# allow ±2 days (a pairing meets once per event). Exactly one Polymarket
# game must fit each Kalshi game.
_GAME_SERIES = {
    "KXNFLGAME": ("nfl", "nfl"), "KXNBAGAME": ("nba", "nba"),
    "KXMLBGAME": ("mlb", "mlb"), "KXNHLGAME": ("nhl", "nhl"),
    "KXATPMATCH": ("atp", None), "KXWTAMATCH": ("wta", None),
    "KXUFCFIGHT": ("ufc", None),
    # College football: exact school names on both sides ("Ohio St." =
    # "Ohio State"); a near-name ("Miami (FL)" vs "Miami") stays unpaired.
    # Rules read 2026-10-13: winner incl. overtime on both; same 48 h /
    # cancellation caveat as the pro leagues.
    "KXNCAAFGAME": ("cfb", None),
}
# Teams play weekly, so a day of slack can't reach another game.
_GAME_DAY_SLACK = {"atp": 2, "wta": 2, "ufc": 2, "cfb": 1}


def _league_team(league, name):
    """Polymarket game side ('Thunder', 'Golden Knights', 'Los Angeles
    Dodgers') -> full team name, or None unless exactly one team fits."""
    n = _team_norm(name)
    hits = [f for f in _LEAGUE_TEAMS[league].values() if f == n or f.endswith(" " + n)]
    return hits[0] if len(hits) == 1 else None


def _person_key(name):
    """Fighter / player / school name as a sorted token tuple ('Wang Xiyu'
    = 'Xiyu Wang'; 'Ohio St.' = 'Ohio State'); None for blanks."""
    s = _strip_diacritics(str(name or "")).lower()
    s = re.sub(r"(?<=\w) st\.?(?=\s|$)", " state", s)
    toks = re.findall(r"[a-z0-9'&]+", s)
    return tuple(sorted(toks)) or None


def _poly_game_sides(question):
    """('A', 'B') from 'A vs. B', 'Jinan: A vs B', "Dana White's Contender
    Series: A vs. B (Featherweight, Main Card)"."""
    q = str(question or "").strip()
    q = re.sub(r"\s*\([^)]*\)\s*$", "", q)
    if ":" in q:
        q = q.rsplit(":", 1)[1]
    parts = re.split(r"\s+vs\.?\s+", q.strip(), flags=re.I)
    if len(parts) != 2 or not all(x.strip() for x in parts):
        return None
    return parts[0].strip(), parts[1].strip()


def match_games(dfs: dict) -> pd.DataFrame:
    if 'kalshi' not in dfs or 'polymarket' not in dfs:
        return pd.DataFrame()
    k = dfs['kalshi']; p = dfs['polymarket']
    if k.empty or p.empty or "series_ticker" not in k.columns or "event_slug" not in p.columns:
        return pd.DataFrame()
    from datetime import date as _date

    def _day(s):
        try:
            return _date.fromisoformat(str(s)[:10])
        except ValueError:
            return None

    # Kalshi: event -> {side key: row}
    ks = k[k["series_ticker"].isin(list(_GAME_SERIES))]
    events = {}
    for _, r in ks.iterrows():
        sport, league = _GAME_SERIES[r["series_ticker"]]
        t = str(r["market_id"])
        if league:
            full = _LEAGUE_TEAMS[league].get(t.rsplit("-", 1)[-1])
            if not full or not _kalshi_label_fits(r.get("yes_sub_title"), full):
                continue
            side = full
        else:
            side = _person_key(r.get("yes_sub_title"))
            if not side:
                continue
        ev = events.setdefault((sport, str(r.get("event_ticker") or t.rsplit("-", 1)[0])), {"rows": {}})
        ev["day"] = _day(r.get("game_date"))
        ev["rows"][side] = r

    # Polymarket: moneyline markets of dated game events (market slug ==
    # event slug, i.e. not a spread / total / prop inside the event).
    # "-game-2" suffixes (doubleheaders) are kept so they count as a second
    # candidate and make the date ambiguous rather than being skipped.
    pm = p[p["event_slug"].astype(str).str.match(r"^(nfl|nba|mlb|nhl|atp|wta|ufc|cfb)-.+-\d{4}-\d{2}-\d{2}(?:-game-?\d+)?$")
           & (p["market_slug"].astype(str) == p["event_slug"].astype(str))]
    if "outcomes_binary" in pm.columns:
        pm = pm[pm["outcomes_binary"].astype(str) != "True"]
    games = {}
    for _, r in pm.iterrows():
        sport = str(r["event_slug"]).split("-", 1)[0]
        sides = _poly_game_sides(r.get("question"))
        day = _day(r.get("game_date"))
        if not sides or not day:
            continue
        # The scraper records the first token's outcome name when it has
        # it; it must be the first-listed side.
        yo = r.get("yes_outcome") if "yes_outcome" in pm.columns else None
        if isinstance(yo, str) and yo.strip() and _person_key(yo) != _person_key(sides[0]):
            continue
        league = sport if sport in _LEAGUE_TEAMS else None
        keyed = tuple(_league_team(league, s) if league else _person_key(s) for s in sides)
        if not all(keyed) or keyed[0] == keyed[1]:
            continue
        games.setdefault((sport, frozenset(keyed)), []).append((day, keyed, r))

    # Two Kalshi events for the same sides and day (doubleheader): skip both.
    seen = {}
    for (sport, _ev), ev in events.items():
        if len(ev["rows"]) == 2 and ev.get("day"):
            kk = (sport, frozenset(ev["rows"]), ev["day"])
            seen[kk] = seen.get(kk, 0) + 1

    pairs = []
    linked = []   # (kalshi series, kalshi event, sport, sides, polymarket event slug)
    for (sport, _ev), ev in events.items():
        if len(ev["rows"]) != 2 or not ev.get("day"):
            continue
        if seen[(sport, frozenset(ev["rows"]), ev["day"])] > 1:
            continue
        slack = _GAME_DAY_SLACK.get(sport, 0)
        cands = [g for g in games.get((sport, frozenset(ev["rows"])), [])
                 if abs((g[0] - ev["day"]).days) <= slack]
        if len(cands) != 1:
            continue
        _, keyed, p_row = cands[0]
        k_row = ev["rows"][keyed[0]]
        pairs.append(_game_pair_row(k_row, p_row))
        linked.append((str(k_row.get("series_ticker")), _ev, sport, keyed, str(p_row.get("event_slug"))))
    n_games = len(pairs)
    pairs += _match_game_lines(k, p, linked)
    print(f"  games: kalshi events={len(events)}, polymarket games={sum(len(v) for v in games.values())}, "
          f"pairs={n_games} games + {len(pairs) - n_games} spreads/totals")
    return pd.DataFrame(pairs)


def _game_pair_row(k_row, p_row):
    return {
        "match_type": "game",
        "race_id": None,
        "platform_a": "kalshi",
        "platform_b": "polymarket",
        "question_a": k_row.get("question", ""),
        "question_b": p_row.get("question", ""),
        "market_id_a": k_row.get("market_id", ""),
        "market_id_b": p_row.get("market_id", ""),
        "implied_prob_a": k_row.get("implied_prob"),
        "implied_prob_b": p_row.get("implied_prob"),
        "settle_date": p_row.get("settle_date") or k_row.get("settle_date") or "",
        "category": k_row.get("category", ""),
        "url_a": k_row.get("url", ""),
        "url_b": p_row.get("url", ""),
        "volume_a": k_row.get("volume"),
        "volume_b": p_row.get("volume"),
        "fuzzy_score": 100,
    }


# Spreads and totals of a game already paired above (2026-10-13). Kalshi
# "Green Bay wins by over 16.5 points" (KXNFLSPREAD, same event code as
# KXNFLGAME) = Polymarket "Spread: Packers (-16.5)" (first token = the
# named team, "wins by 17 or more"); Kalshi "Over 31.5 points" (KXNFLTOTAL)
# = Polymarket "O/U 31.5" (first token = Over, "32 or more"). Rules read
# 2026-10-13 for NFL / NBA / NHL / college football: full game incl.
# overtime on both; an NHL shootout adds one goal to the winner on both;
# same cancellation caveat as the game markets. Team and line must match
# exactly; first-half / first-5-innings / team totals are other series on
# Kalshi and other slug kinds on Polymarket, and are not paired.
_K_SPREAD_SUB = re.compile(r"^(.+?) wins by (?:over|more than) (\d+(?:\.\d+)?)(?: (?:points?|goals?|runs?))?$", re.I)
_K_TOTAL_SUB = re.compile(r"^over (\d+(?:\.\d+)?)(?: (?:points?|goals?|runs?))?(?: scored)?$", re.I)
_P_SPREAD_Q = re.compile(r"^Spread: (.+?) \(-(\d+(?:\.\d+)?)\)$", re.I)
_P_TOTAL_Q = re.compile(r": O/U (\d+(?:\.\d+)?)$", re.I)


def _match_game_lines(k, p, linked):
    from utils.series_map import market_kind
    out = []
    if not linked:
        return out
    k_by_event = {}
    for _, r in k[k["series_ticker"].astype(str).str.match(r"^KX[A-Z]+(?:SPREAD|TOTAL)$")].iterrows():
        k_by_event.setdefault(str(r.get("event_ticker")), []).append(r)
    slugs = {lk[4] for lk in linked}
    pm = p[p["event_slug"].astype(str).isin(slugs)]
    p_by_event = {}
    for _, r in pm.iterrows():
        p_by_event.setdefault(str(r["event_slug"]), []).append(r)

    for series, k_event, sport, keyed, slug in linked:
        if not series.endswith("GAME"):
            continue
        base = series[:-4]
        code = k_event.split("-", 1)[1] if "-" in k_event else ""
        league = sport if sport in _LEAGUE_TEAMS else None

        def side_of(label, from_kalshi):
            """Which of the game's two sides a team label names (index), or None."""
            hits = []
            for i, sd in enumerate(keyed):
                if league:
                    ok = _kalshi_label_fits(label, sd) if from_kalshi else _league_team(league, label) == sd
                else:
                    ok = _person_key(label) == sd
                if ok:
                    hits.append(i)
            return hits[0] if len(hits) == 1 else None

        k_lines = {}
        for kind in ("SPREAD", "TOTAL"):
            for r in k_by_event.get(f"{base}{kind}-{code}", []):
                sub = str(r.get("yes_sub_title") or "").strip()
                if kind == "SPREAD":
                    m = _K_SPREAD_SUB.match(sub)
                    i = side_of(m.group(1), True) if m else None
                    key = ("spread", i, float(m.group(2))) if m and i is not None else None
                else:
                    m = _K_TOTAL_SUB.match(sub)
                    key = ("total", None, float(m.group(1))) if m else None
                if key:
                    k_lines.setdefault(key, []).append(r)
        if not k_lines:
            continue
        p_lines = {}
        for r in p_by_event.get(slug, []):
            kind = market_kind(r.get("market_slug")) or ""
            q = str(r.get("question") or "").strip()
            yo = r.get("yes_outcome")
            yo = yo.strip() if isinstance(yo, str) and yo.strip() else None
            ms = re.match(r"^(spread|total)-(?:home-|away-)?(N)ptN$", kind)
            if not ms:
                continue
            if ms.group(1) == "spread":
                mk = re.search(r"-spread-(?:home|away)-(\d+)pt(\d+)$", str(r.get("market_slug")))
                mq = _P_SPREAD_Q.match(q)
                if not mk or not mq or float(f"{mk.group(1)}.{mk.group(2)}") != float(mq.group(2)):
                    continue
                if yo and _person_key(yo) != _person_key(mq.group(1)):
                    continue
                i = side_of(mq.group(1), False)
                if i is None:
                    continue
                key = ("spread", i, float(mq.group(2)))
            else:
                mk = re.search(r"-total-(\d+)pt(\d+)$", str(r.get("market_slug")))
                mq = _P_TOTAL_Q.search(q)
                if not mk or not mq or float(f"{mk.group(1)}.{mk.group(2)}") != float(mq.group(1)):
                    continue
                if yo and yo.lower() != "over":
                    continue
                key = ("total", None, float(mq.group(1)))
            p_lines.setdefault(key, []).append(r)
        for key in set(k_lines) & set(p_lines):
            if len(k_lines[key]) == 1 and len(p_lines[key]) == 1:
                out.append(_game_pair_row(k_lines[key][0], p_lines[key][0]))
    return out


# ── soccer fixtures (2026-10-12) ──────────────────────────────────────────
# Soccer prop markets are fully determined by fixture date + the two teams +
# the market kind, so they don't need fuzzy titles. Recall check on a fresh
# scrape: of 183 Kalshi full-match BTTS markets with a true Polymarket
# counterpart, the fuzzy matcher found 138 and missed 45 — Kalshi uses short
# names ("Getafe vs Vallecano"), Polymarket full ones ("Getafe CF vs. Rayo
# Vallecano de Madrid"), and the length gap pulls token_sort_ratio under the
# cutoff. This pairs them structurally; the outcome gate
# (utils.proposition.incompatibility: teams, date, period, first-scorer
# subject, exact-score orientation) must still accept the pair, and exactly
# one Polymarket candidate must survive. Rows are emitted as match_type
# "fuzzy" so the series map / rules template / scan-time re-gate all apply.
_SOCCER_KALSHI = re.compile(r"^KX[A-Z0-9]*?(1HBTTS|2HBTTS|BTTS|FTTS|SCORE|GAME|TOTAL|SPREAD)$")
_KIND_OF_SERIES = {"BTTS": "btts", "1HBTTS": "btts-first-half", "2HBTTS": "btts-second-half",
                   "FTTS": "first-to-score", "SCORE": "exact-score",
                   # 2026-10-12: winner / total goals / spread. Kalshi "Regulation Time"
                   # moneyline / totals / spreads and Polymarket's per-game winner,
                   # O/U and spread markets all resolve on 90 min + stoppage; the
                   # gate (soccer_outcome) checks team / draw / line. Non-soccer
                   # GAME/TOTAL/SPREAD series (points, runs) fail that check.
                   "GAME": "winner", "TOTAL": "total", "SPREAD": "spread"}


def _poly_soccer_kind(market_slug):
    from utils.series_map import market_kind
    k = market_kind(market_slug) or ""
    if k in ("btts", "btts-first-half", "btts-second-half"):
        return k
    if k.startswith("first-to-score"):
        return "first-to-score"
    if k.startswith("exact-score"):
        return "exact-score"
    if re.fullmatch(r"total-NptN", k):                 # match total only, not team totals
        return "total"
    if re.fullmatch(r"spread-(?:home|away)-NptN", k):
        return "spread"
    return None


def match_soccer_fixtures(dfs: dict) -> pd.DataFrame:
    from datetime import date, timedelta
    from utils.proposition import teams, _teams_compatible
    k, p = dfs.get("kalshi"), dfs.get("polymarket")
    if k is None or p is None or k.empty or p.empty or "series_ticker" not in k.columns \
            or "market_slug" not in p.columns:
        return pd.DataFrame()
    ks = k[k["series_ticker"].astype(str).str.match(_SOCCER_KALSHI)].copy()
    # Winner / total / spread series exist for every sport, and hockey also
    # counts "goals": KXNHLGAME "New York I vs Toronto" paired with MLS "New
    # York Red Bulls vs Toronto FC" on the same date (2026-10-12, caught before
    # commit). Treat a GAME/TOTAL/SPREAD series as soccer only if the same
    # league also has a both-teams-to-score series (KXMLSGAME <- KXMLSBTTS;
    # there is no KXNHLBTTS).
    _all_series = set(k["series_ticker"].astype(str))
    _soccer_leagues = {s[:-len(m.group(1))] for s in _all_series
                       for m in [re.match(r"^KX[A-Z0-9]*?(1HBTTS|2HBTTS|BTTS)$", s)] if m}
    def _is_soccer(series):
        mm = _SOCCER_KALSHI.match(series)
        return bool(mm) and (mm.group(1) not in ("GAME", "TOTAL", "SPREAD")
                             or series[:-len(mm.group(1))] in _soccer_leagues)
    ks = ks[ks["series_ticker"].astype(str).map(_is_soccer)]
    ps = p.copy()
    ps["soccer_kind"] = ps["market_slug"].map(_poly_soccer_kind)
    # Winner markets only by their exact question form ("Will Wales win on
    # 2026-10-13?" / "... end in a draw?"), never by slug: a slug-code rule
    # picked up "game1" / "nrfi" and paired an MLB game with "Will there be
    # a run scored in the first inning?" (2026-10-12, caught before commit).
    from utils.proposition import soccer_outcome as _soc
    _win = ps["question"].map(lambda q: (_soc(q) or ("",))[0] == "winner")
    ps.loc[_win & ps["soccer_kind"].isna(), "soccer_kind"] = "winner"
    ps = ps[ps["soccer_kind"].notna() & ps["game_date"].notna()]
    # Fixture teams per event: spread questions ("Spread: Getafe CF (-1.5)")
    # don't name the opponent, so take the teams from a sibling market in the
    # same event ("Getafe CF vs. Rayo Vallecano de Madrid: O/U 2.5").
    ev_teams = {}
    for ev, q in zip(p["event_slug"], p["question"]):
        if ev not in ev_teams:
            tmq = teams(q)
            if tmq:
                ev_teams[ev] = tmq
    by = {}
    for r in ps.itertuples(index=False):
        tm = teams(r.question) or ev_teams.get(r.event_slug)
        if tm:
            by.setdefault((r.soccer_kind, r.game_date), []).append((r, tm))
    pairs, ambiguous = [], 0
    for r in ks.itertuples(index=False):
        kind = _KIND_OF_SERIES[_SOCCER_KALSHI.match(str(r.series_ticker)).group(1)]
        tm = teams(r.question)
        if not tm or not isinstance(r.game_date, str):
            continue
        d = date.fromisoformat(r.game_date)
        cands = []
        for dd in (d, d - timedelta(days=1), d + timedelta(days=1)):
            for c, ctm in by.get((kind, dd.isoformat()), []):
                if not _teams_compatible(tm, ctm) or incompatibility(str(r.question), str(c.question)):
                    continue
                # winner / total / spread: BOTH sides must parse as that soccer
                # outcome (the gate only compares them when both parse).
                if kind in ("winner", "total", "spread"):
                    oa, ob = _soc(str(r.question)), _soc(str(c.question))
                    if not oa or not ob or oa[0] != kind or ob[0] != kind:
                        continue
                cands.append(c)
        if len(cands) != 1:
            ambiguous += len(cands) > 1
            continue
        c = cands[0]
        pairs.append({
            "match_type": "fuzzy", "race_id": None, "platform_a": "kalshi", "platform_b": "polymarket",
            "question_a": r.question, "question_b": c.question,
            "market_id_a": r.market_id, "market_id_b": c.market_id,
            "implied_prob_a": r.implied_prob, "implied_prob_b": c.implied_prob,
            "settle_date": r.settle_date or c.settle_date, "category": getattr(r, "category", ""),
            "url_a": r.url, "url_b": c.url, "volume_a": getattr(r, "volume", None),
            "volume_b": getattr(c, "volume", None), "fuzzy_score": 100.0,
        })
    if ambiguous:
        print(f"    ({ambiguous} soccer markets skipped: more than one Polymarket candidate)")
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

    print("\nMatching price-threshold markets (crypto / commodities)...")
    threshold = match_threshold_pairs(dfs)
    print(f"  Threshold pairs: {len(threshold)}")

    print("\nMatching tournament-winner markets (tennis, World Cup)...")
    tournament = match_tournament_winner(dfs)
    print(f"  Tournament pairs: {len(tournament)}")

    print("\nMatching primary nominee markets (US 2026)...")
    nominee = match_primary_nominee(dfs)
    print(f"  Primary nominee pairs: {len(nominee)}")

    # Before running fuzzy, exclude markets that a stricter matcher has
    # already claimed. Otherwise fuzzy can pair the same Kalshi market
    # against a wrong Polymarket market (e.g. Kalshi "2026 FIFA World Cup
    # Winner - Argentina" got fuzzy-matched to Polymarket "Will Argentina
    # reach the 2026 FIFA World Cup final?" — token_sort_ratio 81 — even
    # though the tournament matcher already correctly paired it to the
    # matching Polymarket "win" market). The wrong-subject dedup at the
    # end of run() doesn't help because it keys on (market_id_a,
    # market_id_b) which is DIFFERENT between the two matches.
    print("\nMatching soccer fixtures (date + teams + market kind)...")
    fixtures = match_soccer_fixtures(dfs)
    print(f"  Soccer fixture pairs: {len(fixtures)}")

    print("\nMatching games (US leagues, tennis, UFC: date + both sides)...")
    games = match_games(dfs)
    print(f"  Game pairs: {len(games)}")

    prior = pd.concat([political, threshold, tournament, nominee, fixtures, games], ignore_index=True) \
        if any(len(x) for x in [political, threshold, tournament, nominee, fixtures, games]) else pd.DataFrame()
    claimed_by_platform = {}
    if not prior.empty:
        for _, r in prior.iterrows():
            claimed_by_platform.setdefault(r['platform_a'], set()).add(str(r['market_id_a']))
            claimed_by_platform.setdefault(r['platform_b'], set()).add(str(r['market_id_b']))
        dfs_for_fuzzy = {}
        for plat, df in dfs.items():
            claimed = claimed_by_platform.get(plat, set())
            if claimed and 'market_id' in df.columns:
                dfs_for_fuzzy[plat] = df[~df['market_id'].astype(str).isin(claimed)].copy()
            else:
                dfs_for_fuzzy[plat] = df
    else:
        dfs_for_fuzzy = dfs

    print("\nFuzzy-matching non-political markets...")
    fuzzy = match_fuzzy(dfs_for_fuzzy)
    print(f"  Fuzzy pairs: {len(fuzzy)}")

    all_pairs = pd.concat([political, threshold, tournament, nominee, fixtures, games, fuzzy], ignore_index=True)
    # Remove duplicates (same market pair, different direction)
    all_pairs = all_pairs.drop_duplicates(subset=["platform_a", "platform_b", "market_id_a", "market_id_b"])

    out = PROCESSED / "matched_pairs.csv"
    all_pairs.to_csv(out, index=False)
    print(f"\nTotal matched pairs: {len(all_pairs)} -> {out}")
    print(f"  Political: {len(political)}, Threshold: {len(threshold)}, Tournament: {len(tournament)}, Nominee: {len(nominee)}, Fuzzy: {len(fuzzy)}")


if __name__ == "__main__":
    run()
