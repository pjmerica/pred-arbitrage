"""
US 2026 election-specific arb pair builder.

Ported from polling-agg-2026's scripts/arb_scanner.py (the election-only
sibling repo). Three matching paths for 2026 federal races:

  general            — party-level Dem/Rep win markets, joined on canonical
                       race_id (2026-SEN-OH etc). Supports guaranteed arb
                       baskets when same-platform Dem+Rep partition the
                       outcome within ~3pp.
  general_candidate  — per-candidate general markets ("Will Dan Sullivan
                       win the 2026 Alaska Senate race?"). One-sided only;
                       no Dem-yes vs Rep-yes complement exists in this
                       template.
  primary_candidate  — per-candidate primary markets ("Will Zach Wahls be
                       the Democratic nominee for Senate in Iowa?").
                       One-sided.

This module is a DROP-IN ADDITION to pred-arb's existing fuzzy matcher.
The fuzzy matcher still emits its Politics category rows; the election
rows produced here are appended on top of those. Duplicates with the
fuzzy matcher are acceptable for now (user explicit decision 2026-06-21);
they can be deduplicated downstream later if needed.

Output:
  data/processed/election_pairs.csv

Consumed by:
  scripts/arb_scanner.py — appends these rows to docs/arb_data.js.

Field shape matches pred-arb's arb_scanner output (implied_prob_a/b,
profitable_onesided, etc.) so the merge in arb_scanner is a simple
concat. Each row is tagged category="Elections".

Why it lives in pred-arb rather than being imported from polling-agg:
keeping the repos independent avoids a runtime dependency. The cost is
that the two implementations can drift; see AUDIT.md cross-repo to-do.
"""

import re
import sys
import pandas as pd
import numpy as np
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

RAW = ROOT / "data" / "raw"
PROCESSED = ROOT / "data" / "processed"

# Single source of truth for fees, kept in sync with scripts/arb_scanner.py.
# DO NOT add a separate FEES dict here — import from the scanner module
# instead so changes propagate.
from scripts.arb_scanner import FEES


# ── URL helpers ───────────────────────────────────────────────────────────────

def kalshi_url(series_ticker, market_ticker=None):
    """Build a Kalshi market URL that lands on the SPECIFIC event, not
    just the series. Kalshi market_tickers are shaped as
    `{series}-{event_specifier}-{market_specifier}` (e.g. KXNHPRIMARY-
    01R26-HNOV). The event-specific URL is two segments:

        kalshi.com/markets/{series_lower}/{event_ticker_lower}

    Without the second segment, Kalshi's SPA picks an event from the
    series arbitrarily — for KXNHPRIMARY-01R26-HNOV (NH-01 Republican
    Noveletsky) it was landing on KXNHPRIMARY-02R26 (NH-02). See
    scrapers/kalshi.py's parse_market for the matching scraper-side fix
    and the HANDOFF.md entry."""
    if pd.isna(series_ticker) or not series_ticker:
        return None
    series_lc = str(series_ticker).lower()
    if market_ticker and not pd.isna(market_ticker):
        # event_ticker = market_ticker with the trailing market segment
        # stripped. "KXNHPRIMARY-01R26-HNOV" -> "KXNHPRIMARY-01R26".
        parts = str(market_ticker).split("-")
        if len(parts) >= 3:
            event_ticker = "-".join(parts[:-1]).lower()
            return f"https://kalshi.com/markets/{series_lc}/{event_ticker}"
    return f"https://kalshi.com/markets/{series_lc}"


def predictit_url(market_id):
    if pd.isna(market_id) or not market_id:
        return None
    return f"https://www.predictit.org/markets/detail/{int(market_id)}"


def polymarket_url(slug):
    if pd.isna(slug) or not slug:
        return None
    return f"https://polymarket.com/event/{slug}"


def _safe_read_csv(path: Path, **kw) -> pd.DataFrame:
    """Defensive CSV read. Returns empty DataFrame on missing/empty file
    so a scraper outage doesn't crash the election pipeline."""
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path, **kw)
    except pd.errors.EmptyDataError:
        print(f"  WARNING: {path.name} is empty — treating as no data")
        return pd.DataFrame()


# Pred-arb's scrapers don't tag rows with race_id (the polling-agg ones do).
# These adapters normalize column names + derive race_id from titles so the
# ported election logic can run unchanged.

def _load_kalshi_normalized() -> pd.DataFrame:
    """Pred-arb kalshi CSV has 'title' / 'ticker'; polling-agg used
    'market_title' / 'market_ticker' / 'race_id'. Add the missing
    columns and derive race_id by parsing the title."""
    df = _safe_read_csv(RAW / "kalshi_markets.csv")
    if df.empty:
        return df
    if "market_title" not in df.columns and "title" in df.columns:
        df["market_title"] = df["title"]
    if "market_ticker" not in df.columns and "ticker" in df.columns:
        df["market_ticker"] = df["ticker"]
    if "race_id" not in df.columns:
        df["race_id"] = df["market_title"].apply(_race_id_from_title)
    return df


def _load_polymarket_normalized() -> pd.DataFrame:
    """Pred-arb polymarket CSV is mostly identical to polling-agg's
    minus the race_id. Derive race_id from the question text."""
    df = _safe_read_csv(RAW / "polymarket_markets.csv",
                       dtype={"yes_token_id": str, "no_token_id": str})
    if df.empty:
        return df
    if "race_id" not in df.columns and "question" in df.columns:
        df["race_id"] = df["question"].apply(_race_id_from_title)
    return df


def _load_predictit_normalized() -> pd.DataFrame:
    """Pred-arb predictit CSV has no race_id. Derive from market_name."""
    df = _safe_read_csv(RAW / "predictit_markets.csv")
    if df.empty:
        return df
    if "race_id" not in df.columns and "market_name" in df.columns:
        df["race_id"] = df["market_name"].apply(_race_id_from_title)
    return df


def _race_id_from_title(title: str) -> str | None:
    """Derive canonical race_id (e.g. 2026-SEN-OH) from a market title.
    Returns None when the title doesn't reference a 2026 federal race.
    Used to fill in pred-arb scraper output, which doesn't pre-tag rows."""
    state, office, district = _extract_state_office(title)
    return _race_id_from(state, office, district)


# ── state / office / candidate parsing ────────────────────────────────────────

_STATES = {
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
_STATE_ABBREVS = set(_STATES.values())


def _extract_state_office(title: str) -> tuple[str | None, str | None, str | None]:
    """Parse (state_abbrev, office, district) from a market title.

    office is one of {'SEN', 'GOV', 'H'} or None. district is zero-padded
    for House races, else None.

    State search is anchored to the substring AFTER the office word
    (Senate / governor / house) — surnames like "Washington" or "Carolina"
    used to false-match the state. Example bug: "Will Wayne Lonny
    Washington be the Republican nominee for the Senate in Oklahoma?"
    matched state=WA on the surname before reaching "oklahoma".
    """
    if not isinstance(title, str):
        return (None, None, None)
    t = title.lower()

    # House first: "FL-6", "NY-16", "CA-12".
    m = re.search(r"\b([A-Z]{2})[-\s](\d{1,2})\b", title)
    if m and m.group(1) in _STATE_ABBREVS:
        return (m.group(1), "H", str(int(m.group(2))).zfill(2))

    def find_state(text):
        # Longest-first so "west virginia" beats "virginia".
        for name, abbrev in sorted(_STATES.items(), key=lambda x: -len(x[0])):
            if name in text:
                return abbrev
        return None

    office_word = None
    if "senate" in t or "senator" in t:
        office_word = "SEN"
        suffix = t[max(t.rfind("senate"), t.rfind("senator")):]
    elif "governor" in t or "gubernatorial" in t:
        office_word = "GOV"
        suffix = t[max(t.rfind("governor"), t.rfind("gubernatorial")):]
    elif "house" in t or "congress" in t:
        office_word = "H"
        suffix = t[max(t.rfind("house"), t.rfind("congress")):]
    else:
        suffix = t

    state_ab = find_state(suffix) or find_state(t)
    if not state_ab:
        return (None, None, None)
    if office_word in ("SEN", "GOV", "H"):
        return (state_ab, office_word, None)
    return (state_ab, None, None)


def _race_id_from(state, office, district):
    if not state or not office:
        return None
    if office == "H":
        if not district:
            return None
        return f"2026-H-{state}-{district}"
    return f"2026-{office}-{state}"


def _canonical_last_name(name: str) -> str | None:
    if not isinstance(name, str):
        return None
    n = re.sub(r"[^\w\s'\-]", " ", name).strip()
    parts = [p for p in n.split() if p.lower() not in ("jr", "sr", "jr.", "sr.", "ii", "iii", "iv")]
    if not parts:
        return None
    return parts[-1].lower()


def _first_initial(name: str) -> str | None:
    """First initial, stripping prefixes (Dr/Mr/...). Combined with last
    name to disambiguate Chris Sununu vs John E. Sununu."""
    if not isinstance(name, str):
        return None
    n = re.sub(r"[^\w\s'\-]", " ", name).strip()
    parts = [p for p in n.split()
             if p.lower() not in ("jr", "sr", "jr.", "sr.", "ii", "iii", "iv",
                                   "dr", "mr", "mrs", "ms", "the", "rep", "sen", "gov")]
    if not parts:
        return None
    return parts[0][0].lower() if parts[0] else None


# ── general (party-level) loaders ─────────────────────────────────────────────

def _load_kalshi_general():
    """Per-race Kalshi Dem/Rep prob, picking the highest-OI market per side."""
    df = _load_kalshi_normalized()
    if df.empty or "race_id" not in df.columns or "implied_prob" not in df.columns:
        return pd.DataFrame()
    df = df[df["race_id"].notna() & df["implied_prob"].notna()].copy()

    # Match the dem/rep side. Kalshi serves general-election party
    # markets under two different title shapes depending on which event
    # template it is:
    #   1. polling-agg-flavor: "Will Democratic win the House race for WI-1?"
    #      (regex needs "Democratic" + "win" together)
    #   2. pred-arb-flavor:    "WI-01 House winner? — Democratic party"
    #      (pred-arb's parse_market joins event_title with yes_sub_title
    #      using ' — '; the "party" suffix is the giveaway)
    # If we only match shape (1) we miss every pred-arb House market
    # (Wisconsin WI-01 was reported on 2026-06-22 as the symptom).
    dem_mask = df["market_title"].str.contains(
        r"Democrat(?:ic)?s?\s+win|Will Democrat(?:ic)?s?\s+win|Democrat(?:ic)?\s+party\b",
        case=False, na=False,
    ) & ~df["market_title"].str.contains("nominee|primary|nominate", case=False, na=False)
    rep_mask = df["market_title"].str.contains(
        r"Republican(?:s)?\s+win|Will Republican(?:s)?\s+win|Republican\s+party\b",
        case=False, na=False,
    ) & ~df["market_title"].str.contains("nominee|primary|nominate", case=False, na=False)

    cols = ["implied_prob", "open_interest", "volume", "series_ticker", "market_ticker", "market_title"]
    if "market_ticker" not in df.columns:
        df["market_ticker"] = None

    def best(g):
        oi = pd.to_numeric(g["open_interest"], errors="coerce").fillna(0)
        return g.loc[oi.idxmax(), cols]

    dem = df[dem_mask].groupby("race_id").apply(best, include_groups=False).reset_index()
    dem = dem.rename(columns={
        "implied_prob": "kalshi_dem", "open_interest": "kalshi_oi",
        "volume": "kalshi_volume", "series_ticker": "kalshi_series_ticker",
        "market_ticker": "kalshi_dem_ticker",
    })
    rep = df[rep_mask].groupby("race_id").apply(best, include_groups=False).reset_index()
    rep = rep.rename(columns={
        "implied_prob": "kalshi_rep", "series_ticker": "kalshi_rep_series",
        "market_ticker": "kalshi_rep_ticker",
    })

    # Inner join: only races with both sides explicitly priced. Inferring
    # one side from 1 - other_side was ripped out in polling-agg
    # 2026-06-18; inferred prices aren't tradeable.
    merged = dem[["race_id", "kalshi_dem", "kalshi_oi", "kalshi_volume",
                  "kalshi_series_ticker", "kalshi_dem_ticker"]].merge(
        rep[["race_id", "kalshi_rep", "kalshi_rep_ticker"]], on="race_id", how="inner"
    )
    # Pass the dem market_ticker so kalshi_url() can derive the event
    # ticker and build the two-segment URL (lands on the right event,
    # not just the series page — see kalshi_url docstring).
    merged["kalshi_url"] = merged.apply(
        lambda r: kalshi_url(r.get("kalshi_series_ticker"), r.get("kalshi_dem_ticker")),
        axis=1,
    )
    return merged


def _load_predictit_general():
    """PredictIt Democratic/Republican party contracts per race."""
    df = _load_predictit_normalized()
    if df.empty or "race_id" not in df.columns or "implied_prob" not in df.columns:
        return pd.DataFrame()
    df = df[df["race_id"].notna() & df["implied_prob"].notna()].copy()
    df = df[df["contract_name"].str.strip().isin(["Democratic", "Republican"])].copy()

    dem = df[df["contract_name"].str.strip() == "Democratic"][
        ["race_id", "implied_prob", "best_buy_yes", "best_sell_yes", "market_id"]
    ].rename(columns={"implied_prob": "pi_dem", "best_buy_yes": "pi_dem_buy",
                      "best_sell_yes": "pi_dem_sell"})
    rep = df[df["contract_name"].str.strip() == "Republican"][
        ["race_id", "implied_prob"]
    ].rename(columns={"implied_prob": "pi_rep"})
    merged = dem.merge(rep, on="race_id", how="outer")
    merged["pi_url"] = merged["market_id"].apply(predictit_url)
    return merged


def _load_polymarket_general():
    """Polymarket Dem/Rep win Yes prices per race."""
    df = _load_polymarket_normalized()
    if df.empty or "race_id" not in df.columns:
        return pd.DataFrame()
    df = df[df["race_id"].notna() & df["implied_prob"].notna()].copy()
    df["implied_prob"] = pd.to_numeric(df["implied_prob"], errors="coerce")
    df = df.dropna(subset=["implied_prob"])

    q = df["question"].str.lower()
    df["is_dem"] = q.str.contains(r"democrat|democratic", na=False) & ~q.str.contains(r"republican", na=False)
    df["is_rep"] = q.str.contains(r"republican", na=False) & ~q.str.contains(r"democrat|democratic", na=False)
    df = df[~q.str.contains("nominee|primary|nominate|advance", na=False)]

    slug_col = "event_slug" if "event_slug" in df.columns else ("url_slug" if "url_slug" in df.columns else None)
    if slug_col is None:
        df["_slug"] = df.get("condition_id", "")
    else:
        df["_slug"] = df[slug_col].fillna("").astype(str)
        if "market_slug" in df.columns:
            mask = df["_slug"].eq("")
            df.loc[mask, "_slug"] = df.loc[mask, "market_slug"].fillna("").astype(str)
    if "yes_token_id" not in df.columns:
        df["yes_token_id"] = None

    dem = df[df["is_dem"]][["race_id", "implied_prob", "liquidity", "volume", "_slug", "yes_token_id"]].copy()
    rep = df[df["is_rep"]][["race_id", "implied_prob", "liquidity", "volume", "_slug", "yes_token_id"]].copy()

    def best_liq(g):
        liq = pd.to_numeric(g["liquidity"], errors="coerce").fillna(0)
        return g.loc[liq.idxmax()]

    if not dem.empty:
        dem = dem.groupby("race_id").apply(best_liq, include_groups=False).reset_index()
        dem = dem.rename(columns={
            "implied_prob": "pm_dem", "liquidity": "pm_liq",
            "volume": "pm_volume", "_slug": "pm_dem_slug",
            "yes_token_id": "pm_dem_token",
        })
    if not rep.empty:
        rep = rep.groupby("race_id").apply(best_liq, include_groups=False).reset_index()
        rep = rep.rename(columns={
            "implied_prob": "pm_rep", "liquidity": "pm_rep_liq",
            "_slug": "pm_rep_slug", "yes_token_id": "pm_rep_token",
        })
    if dem.empty and rep.empty:
        return pd.DataFrame()

    dem_cols = ["race_id", "pm_dem", "pm_liq", "pm_volume", "pm_dem_slug", "pm_dem_token"]
    result = dem[dem_cols] if not dem.empty else pd.DataFrame(columns=dem_cols)
    if not rep.empty:
        result = result.merge(rep[["race_id", "pm_rep", "pm_rep_slug", "pm_rep_token"]],
                              on="race_id", how="outer")

    if "pm_rep" in result.columns:
        result = result[result["pm_dem"].notna() & result["pm_rep"].notna()].copy()
    else:
        result = result.iloc[0:0].copy()

    if "pm_rep_slug" in result.columns:
        result["pm_url"] = result["pm_dem_slug"].where(
            result["pm_dem_slug"].notna() & (result["pm_dem_slug"] != ""),
            result["pm_rep_slug"],
        ).apply(polymarket_url)
    else:
        result["pm_url"] = result["pm_dem_slug"].apply(polymarket_url)
    return result


def _get_race_meta():
    try:
        from utils.races import RACE_BY_ID
        rows = []
        for race_id, r in RACE_BY_ID.items():
            lbl = (f"{r.state_abbrev}-{str(r.district).zfill(2)}"
                   if r.office == "H" else f"{r.state_abbrev} {r.office}")
            rows.append({"race_id": race_id, "state": r.state,
                         "state_abbrev": r.state_abbrev, "office": r.office,
                         "label": lbl})
        return pd.DataFrame(rows)
    except Exception as e:
        print(f"  WARNING: could not load race metadata: {e}")
        return pd.DataFrame(columns=["race_id", "state", "state_abbrev", "office", "label"])


# ── arb math (party-level only) ───────────────────────────────────────────────

# Same-platform Dem+Rep must partition the outcome within this tolerance
# before we treat (Dem-yes, Rep-yes) as a valid guaranteed basket.
# Nebraska 2026 had an independent at ~30% on Polymarket — that's a
# 3-way race where "1 - P(Dem)" is NOT "P(Rep)", so cross-flipping is
# unsafe. 3pp is loose enough to allow rounding noise, tight enough to
# catch real 3-way races.
PARTITION_TOL = 0.03


def _compute_arb_math(prob_a, prob_b, prob_a_rep, prob_b_rep, fee_a, fee_b):
    """Determine arb type + stake sizing for a Dem-yes vs Dem-yes pair.

    Guaranteed: buying Dem on cheaper side + Rep on the expensive side
    costs <1 after fees → locks in profit regardless of outcome.

    Stake sizing: sA = (1 - rep_b) / (2 - prob_a - rep_b), sB symmetric.
    """
    result = {
        "arb_type": "one-sided",
        "guaranteed_return_pct": None,
        "stake_a_pct": None, "stake_b_pct": None,
        "stake_a_dollars": None, "stake_b_dollars": None,
        "profit_dollars": None, "stake_note": None,
    }

    def safe_rep(prob_dem, prob_rep_explicit):
        if prob_rep_explicit is not None and not pd.isna(prob_rep_explicit):
            if abs(float(prob_dem) + float(prob_rep_explicit) - 1.0) <= PARTITION_TOL:
                return float(prob_rep_explicit)
            return None  # 3-way race — unsafe
        return None

    rep_a = safe_rep(prob_a, prob_a_rep)
    rep_b = safe_rep(prob_b, prob_b_rep)
    if rep_a is None or rep_b is None:
        return result

    cost1 = prob_a + rep_b
    cost2 = prob_b + rep_a
    best_cost = min(cost1, cost2)
    if best_cost >= 1.0:
        return result

    gross_return = 1.0 - best_cost
    net_return = gross_return - fee_a - fee_b
    if net_return <= 0:
        return result

    result["arb_type"] = "guaranteed"
    result["guaranteed_return_pct"] = round(net_return * 100, 2)
    if cost1 <= cost2:
        denom = 2 - prob_a - rep_b
        sA = (1 - rep_b) / denom if denom > 0 else 0.5
        sB = (1 - prob_a) / denom if denom > 0 else 0.5
        result["stake_note"] = (f"Buy Dem on A ({round(sA*100,1)}% of bankroll) "
                                f"+ Buy Rep on B ({round(sB*100,1)}%)")
    else:
        denom = 2 - prob_b - rep_a
        sB = (1 - rep_a) / denom if denom > 0 else 0.5
        sA = (1 - prob_b) / denom if denom > 0 else 0.5
        result["stake_note"] = (f"Buy Rep on A ({round(sA*100,1)}% of bankroll) "
                                f"+ Buy Dem on B ({round(sB*100,1)}%)")
    result["stake_a_pct"] = round(sA * 100, 1)
    result["stake_b_pct"] = round(sB * 100, 1)
    result["stake_a_dollars"] = round(sA * 100, 2)
    result["stake_b_dollars"] = round(sB * 100, 2)
    result["profit_dollars"] = round(net_return * 100, 2)
    return result


# ── candidate-level matching helpers ──────────────────────────────────────────

# Title fragments that DISQUALIFY a general candidate market even if it
# matches the "Will <Name> win <thing>" template. All seen in real
# Kalshi/Polymarket data.
_GEN_CAND_EXCLUDE = re.compile(
    r"\b(county|finish\s+\d|drop\s+out|endorse|lieutenant|runoff\s+before|primary\s+runoff)\b",
    re.IGNORECASE,
)
# Subjects that look like names but aren't candidates ("the Mike Duggan
# party", "an independent").
_GEN_CAND_SUBJECT_SKIP = re.compile(r"^(the\s+|an?\s+independent\b)", re.IGNORECASE)


def _load_general_candidates():
    """Per-candidate general-election markets across Kalshi/PM/PI."""
    rows = []

    # Kalshi: "Will Dan Sullivan win the 2026 Alaska Senate race?"
    k = _load_kalshi_normalized()
    if not k.empty and "implied_prob" in k.columns and "market_title" in k.columns:
        k = k[k["implied_prob"].notna() & k["market_title"].notna()].copy()
        pat = re.compile(r"^Will\s+(.+?)\s+win\s+the\s+2026\s+(.+?)\??$", re.IGNORECASE)
        for _, r in k.iterrows():
            title = str(r["market_title"])
            if _GEN_CAND_EXCLUDE.search(title):
                continue
            m = pat.match(title.strip())
            if not m:
                continue
            name, tail = m.group(1).strip(), m.group(2).strip()
            if _GEN_CAND_SUBJECT_SKIP.match(name):
                continue
            if not re.search(r"\b(senate|senator|governor|gubernatorial|house|congress)\b",
                             tail, re.IGNORECASE):
                continue
            state, office, district = _extract_state_office(title)
            if not state or not office:
                continue
            last = _canonical_last_name(name)
            if not last:
                continue
            rows.append({
                "state": state, "office": office, "district": district,
                "candidate_last": last, "candidate_first": _first_initial(name),
                "candidate_name": name, "platform": "kalshi",
                "prob": float(r["implied_prob"]),
                "url": kalshi_url(r.get("series_ticker"), r.get("market_ticker")),
                "volume": pd.to_numeric(r.get("volume"), errors="coerce"),
                "oi": pd.to_numeric(r.get("open_interest"), errors="coerce"),
                "market_id": r.get("market_ticker"),
                "raw_title": title,
            })

    # Polymarket: "Will <Name> win the <State> Governor Election in 2026?"
    # or "Will <Name> win the 2026 <State> governor election?"
    pm = _load_polymarket_normalized()
    if not pm.empty and "implied_prob" in pm.columns:
        pm = pm[pm["implied_prob"].notna()].copy()
        pat = re.compile(
            r"^Will\s+(.+?)\s+win\s+(?:the\s+)?(?:(?:2026\s+)?(.+?)(?:\s+in\s+2026)?)\??$",
            re.IGNORECASE,
        )
        slug_col = ("event_slug" if "event_slug" in pm.columns
                    else ("market_slug" if "market_slug" in pm.columns else None))
        for _, r in pm.iterrows():
            title = str(r.get("question", ""))
            if _GEN_CAND_EXCLUDE.search(title):
                continue
            m = pat.match(title.strip())
            if not m:
                continue
            name, tail = m.group(1).strip(), m.group(2).strip()
            if _GEN_CAND_SUBJECT_SKIP.match(name):
                continue
            if re.search(r"\b(nominee|primary|nominate|advance)\b", title, re.IGNORECASE):
                continue
            if not re.search(r"\b(senate|senator|governor|gubernatorial|house|congress)\b",
                             tail, re.IGNORECASE):
                continue
            state, office, district = _extract_state_office(title)
            if not state or not office:
                continue
            last = _canonical_last_name(name)
            if not last:
                continue
            slug = r.get(slug_col) if slug_col else None
            rows.append({
                "state": state, "office": office, "district": district,
                "candidate_last": last, "candidate_first": _first_initial(name),
                "candidate_name": name, "platform": "polymarket",
                "prob": float(r["implied_prob"]),
                "url": polymarket_url(slug),
                "volume": pd.to_numeric(r.get("volume"), errors="coerce"),
                "oi": pd.to_numeric(r.get("liquidity"), errors="coerce"),
                "market_id": r.get("yes_token_id"),
                "raw_title": title,
            })

    # PredictIt: market_name = "Who will win the 2026 election for X?";
    # contract_name = candidate.
    pi = _load_predictit_normalized()
    if not pi.empty and "implied_prob" in pi.columns:
        pi = pi[pi["implied_prob"].notna()].copy()
        for _, r in pi.iterrows():
            mn = str(r.get("market_name", ""))
            cn = str(r.get("contract_name", ""))
            ml = mn.lower()
            if "nomination" in ml or "primary" in ml:
                continue
            if ml.startswith("which party"):
                continue
            if "who will win" not in ml and "who wins" not in ml:
                continue
            if cn.lower().strip() in ("any other", "any other candidate", "no nominee", "other"):
                continue
            state, office, district = _extract_state_office(mn)
            if not state or not office:
                continue
            last = _canonical_last_name(cn)
            if not last:
                continue
            rows.append({
                "state": state, "office": office, "district": district,
                "candidate_last": last, "candidate_first": _first_initial(cn),
                "candidate_name": cn.strip(), "platform": "predictit",
                "prob": float(r["implied_prob"]),
                "url": predictit_url(r.get("market_id")),
                "volume": None, "oi": None, "market_id": None,
                "raw_title": f"{mn} — {cn}",
            })

    df = pd.DataFrame(rows)
    if not df.empty:
        df["race_id"] = df.apply(
            lambda r: _race_id_from(r["state"], r["office"], r["district"]), axis=1)
    return df


def _load_primary_candidates():
    """Per-candidate PRIMARY-nominee markets across the three platforms."""
    rows = []

    # Kalshi: "Will <Name> be the <Party> nominee for the Senate in <State>?"
    k = _load_kalshi_normalized()
    if not k.empty and "implied_prob" in k.columns and "market_title" in k.columns:
        k = k[k["implied_prob"].notna() & k["market_title"].notna()].copy()
        pat = re.compile(
            r"^Wil[l]?\s+(.+?)\s+be\s+the\s+(Democratic|Republican)\s+nominee",
            re.IGNORECASE,
        )
        for _, r in k.iterrows():
            title = str(r["market_title"])
            m = pat.match(title)
            if not m:
                continue
            name = m.group(1).strip()
            party = "DEM" if m.group(2).lower().startswith("d") else "REP"
            state, office, district = _extract_state_office(title)
            if not state or not office:
                continue
            last = _canonical_last_name(name)
            if not last:
                continue
            rows.append({
                "state": state, "office": office, "district": district,
                "party": party, "candidate_last": last,
                "candidate_first": _first_initial(name),
                "candidate_name": name, "platform": "kalshi",
                "prob": float(r["implied_prob"]),
                "url": kalshi_url(r.get("series_ticker"), r.get("market_ticker")),
                "volume": pd.to_numeric(r.get("volume"), errors="coerce"),
                "oi": pd.to_numeric(r.get("open_interest"), errors="coerce"),
                "market_id": r.get("market_ticker"),
                "raw_title": title,
            })

    # PredictIt nomination contracts.
    pi = _load_predictit_normalized()
    if not pi.empty and "implied_prob" in pi.columns:
        pi = pi[pi["implied_prob"].notna()].copy()
        party_pat = re.compile(r"\b(Democratic|Republican|Democrat|Republicans?)\b", re.IGNORECASE)
        for _, r in pi.iterrows():
            mn = str(r.get("market_name", ""))
            cn = str(r.get("contract_name", ""))
            ml = mn.lower()
            if "nomination" not in ml and "primary" not in ml:
                continue
            pm_ = party_pat.search(mn)
            if not pm_:
                continue
            party = "DEM" if pm_.group(1).lower().startswith("d") else "REP"
            state, office, district = _extract_state_office(mn)
            if not state or not office:
                continue
            if cn.lower().strip() in ("any other", "any other candidate", "no nominee", "other"):
                continue
            last = _canonical_last_name(cn)
            if not last:
                continue
            rows.append({
                "state": state, "office": office, "district": district,
                "party": party, "candidate_last": last,
                "candidate_first": _first_initial(cn),
                "candidate_name": cn.strip(), "platform": "predictit",
                "prob": float(r["implied_prob"]),
                "url": predictit_url(r.get("market_id")),
                "volume": None, "oi": None, "market_id": None,
                "raw_title": f"{mn} — {cn}",
            })

    # Polymarket: "Will <Name> be the <Party> nominee for Senate in <State>?"
    pm = _load_polymarket_normalized()
    if not pm.empty and "implied_prob" in pm.columns:
        pm = pm[pm["implied_prob"].notna()].copy()
        pat = re.compile(
            r"^Will\s+(.+?)\s+be\s+the\s+(Democratic|Republican)\s+nominee",
            re.IGNORECASE,
        )
        slug_col = ("event_slug" if "event_slug" in pm.columns
                    else ("market_slug" if "market_slug" in pm.columns else None))
        for _, r in pm.iterrows():
            title = str(r.get("question", ""))
            m = pat.match(title)
            if not m:
                continue
            name = m.group(1).strip()
            if name.lower().startswith(("any other", "another person", "a candidate not")):
                continue
            party = "DEM" if m.group(2).lower().startswith("d") else "REP"
            state, office, district = _extract_state_office(title)
            if not state or not office:
                continue
            last = _canonical_last_name(name)
            if not last:
                continue
            slug = r.get(slug_col) if slug_col else None
            rows.append({
                "state": state, "office": office, "district": district,
                "party": party, "candidate_last": last,
                "candidate_first": _first_initial(name),
                "candidate_name": name, "platform": "polymarket",
                "prob": float(r["implied_prob"]),
                "url": polymarket_url(slug),
                "volume": pd.to_numeric(r.get("volume"), errors="coerce"),
                "oi": pd.to_numeric(r.get("liquidity"), errors="coerce"),
                "market_id": r.get("yes_token_id"),
                "raw_title": title,
            })

    df = pd.DataFrame(rows)
    if not df.empty:
        df["race_id"] = df.apply(
            lambda r: _race_id_from(r["state"], r["office"], r["district"]), axis=1)
    return df


# ── pair builders ─────────────────────────────────────────────────────────────

def _emit_pair(prob_a, prob_b, platform_a, platform_b, *, race_id, label,
               state, office, match_type, candidate=None, party=None,
               prob_a_rep=None, prob_b_rep=None,
               url_a=None, url_b=None,
               market_id_a=None, market_id_b=None,
               question_a="", question_b="",
               volume_a=None, volume_b=None):
    """Build one pred-arb-shaped pair dict from two cross-platform prices.

    Pred-arb's downstream (arb_scanner.py, fetch_depth.py, frontend) uses
    `implied_prob_a`/`implied_prob_b`, `profitable_onesided`, and tags
    each row with `category`. Match that schema here so the rows drop
    into the existing pipeline without translation.
    """
    if pd.isna(prob_a) or pd.isna(prob_b):
        return None
    pa, pb = float(prob_a), float(prob_b)
    raw_gap = abs(pa - pb)
    fee_a = FEES.get(platform_a, 0.05)
    fee_b = FEES.get(platform_b, 0.05)
    net_gap = raw_gap - fee_a - fee_b

    if match_type == "general":
        # Guaranteed-arb math only applies to the party-level Dem/Rep
        # path; candidate-level rows have no Rep-yes complement.
        arb = _compute_arb_math(pa, pb, prob_a_rep, prob_b_rep, fee_a, fee_b)
    else:
        arb = {
            "arb_type": "one-sided", "guaranteed_return_pct": None,
            "stake_a_pct": None, "stake_b_pct": None,
            "stake_a_dollars": None, "stake_b_dollars": None,
            "profit_dollars": None, "stake_note": None,
        }

    if pa > pb:
        action = (f"Buy on {platform_b.title()} ({pb*100:.1f}%), "
                  f"fade on {platform_a.title()} ({pa*100:.1f}%)")
    else:
        action = (f"Buy on {platform_a.title()} ({pa*100:.1f}%), "
                  f"fade on {platform_b.title()} ({pb*100:.1f}%)")

    row = {
        "match_type": match_type,
        "race_id": race_id or "",
        "category": "Elections",
        "label": label,
        "state": state,
        "office": office,
        "platform_a": platform_a,
        "platform_b": platform_b,
        "question_a": question_a,
        "question_b": question_b,
        "market_id_a": market_id_a or "",
        "market_id_b": market_id_b or "",
        "implied_prob_a": round(pa, 4),
        "implied_prob_b": round(pb, 4),
        "settle_date": "2026-11-03",  # Election day
        "url_a": url_a or "",
        "url_b": url_b or "",
        "volume_a": None if (volume_a is None or pd.isna(volume_a)) else round(float(volume_a), 2),
        "volume_b": None if (volume_b is None or pd.isna(volume_b)) else round(float(volume_b), 2),
        "fuzzy_score": 100,  # Exact match by race_id / candidate name, not fuzzy.
        "raw_gap_pp": round(raw_gap * 100, 2),
        "net_gap_pp": round(net_gap * 100, 2),
        "profitable_onesided": bool(net_gap > 0),
        "suspicious": bool(raw_gap * 100 > 20),
        "action": action,
        **arb,
    }
    if candidate:
        row["candidate"] = candidate
    if party:
        row["party"] = party
    return row


def _general_party_pairs(meta_df):
    """Party-level general-election pairs across the three platforms."""
    kalshi = _load_kalshi_general()
    pi = _load_predictit_general()
    pm = _load_polymarket_general()
    if not meta_df.empty:
        kalshi = kalshi.merge(meta_df, on="race_id", how="left") if not kalshi.empty else kalshi
        pi = pi.merge(meta_df, on="race_id", how="left") if not pi.empty else pi
        pm = pm.merge(meta_df, on="race_id", how="left") if not pm.empty else pm

    rows = []

    if not kalshi.empty and not pi.empty:
        kpi = kalshi.merge(pi, on="race_id", how="inner", suffixes=("", "_pi"))
        for _, r in kpi.iterrows():
            row = _emit_pair(
                r.get("kalshi_dem"), r.get("pi_dem"), "kalshi", "predictit",
                race_id=r["race_id"], label=r.get("label", r["race_id"]),
                state=r.get("state", ""), office=r.get("office", ""),
                match_type="general",
                prob_a_rep=r.get("kalshi_rep"), prob_b_rep=r.get("pi_rep"),
                url_a=r.get("kalshi_url"), url_b=r.get("pi_url"),
                market_id_a=r.get("kalshi_dem_ticker"),
                volume_a=r.get("kalshi_volume"),
                question_a=f"Kalshi: {r['race_id']} Dem win",
                question_b=f"PredictIt: {r['race_id']} Dem win",
            )
            if row:
                rows.append(row)

    if not kalshi.empty and not pm.empty and "pm_dem" in pm.columns:
        kpm = kalshi.merge(pm, on="race_id", how="inner", suffixes=("", "_pm"))
        for _, r in kpm.iterrows():
            row = _emit_pair(
                r.get("kalshi_dem"), r.get("pm_dem"), "kalshi", "polymarket",
                race_id=r["race_id"], label=r.get("label", r["race_id"]),
                state=r.get("state", ""), office=r.get("office", ""),
                match_type="general",
                prob_a_rep=r.get("kalshi_rep"), prob_b_rep=r.get("pm_rep"),
                url_a=r.get("kalshi_url"), url_b=r.get("pm_url"),
                market_id_a=r.get("kalshi_dem_ticker"),
                market_id_b=r.get("pm_dem_token"),
                volume_a=r.get("kalshi_volume"), volume_b=r.get("pm_volume"),
                question_a=f"Kalshi: {r['race_id']} Dem win",
                question_b=f"Polymarket: {r['race_id']} Dem win",
            )
            if row:
                rows.append(row)

    if not pi.empty and not pm.empty and "pm_dem" in pm.columns:
        pipm = pi.merge(pm, on="race_id", how="inner", suffixes=("_pi", "_pm"))
        for _, r in pipm.iterrows():
            row = _emit_pair(
                r.get("pi_dem"), r.get("pm_dem"), "predictit", "polymarket",
                race_id=r["race_id"], label=r.get("label", r["race_id"]),
                state=r.get("state", ""), office=r.get("office", ""),
                match_type="general",
                prob_a_rep=r.get("pi_rep"), prob_b_rep=r.get("pm_rep"),
                url_a=r.get("pi_url"), url_b=r.get("pm_url"),
                market_id_b=r.get("pm_dem_token"),
                volume_b=r.get("pm_volume"),
                question_a=f"PredictIt: {r['race_id']} Dem win",
                question_b=f"Polymarket: {r['race_id']} Dem win",
            )
            if row:
                rows.append(row)

    return rows


def _candidate_pairs(load_fn, match_type, meta_df, with_party):
    """Generic cross-platform pair builder for candidate-level rows.

    Shared between general_candidate (no party in key) and
    primary_candidate (party IS in key — same surname can have a Dem
    and a Rep candidate in different primary races).
    """
    cands = load_fn()
    if cands.empty:
        return []

    cands["_rank"] = cands[["volume", "oi"]].fillna(0).max(axis=1)
    cands["district"] = cands["district"].fillna("")
    cands["candidate_first"] = cands["candidate_first"].fillna("")

    if with_party:
        sort_cols = ["state", "office", "district", "party", "candidate_last",
                     "candidate_first", "platform", "_rank", "prob"]
        sort_asc = [True] * 7 + [False, False]
        dedup_key = ["state", "office", "district", "party", "candidate_last",
                     "candidate_first", "platform"]
        key_cols = ["state", "office", "district", "party", "candidate_last",
                    "candidate_first"]
    else:
        sort_cols = ["state", "office", "district", "candidate_last",
                     "candidate_first", "platform", "_rank", "prob"]
        sort_asc = [True] * 6 + [False, False]
        dedup_key = ["state", "office", "district", "candidate_last",
                     "candidate_first", "platform"]
        key_cols = ["state", "office", "district", "candidate_last",
                    "candidate_first"]

    cands = cands.sort_values(sort_cols, ascending=sort_asc).drop_duplicates(subset=dedup_key)
    meta_map = {r["race_id"]: r for _, r in meta_df.iterrows()} if not meta_df.empty else {}

    out = []
    for key, grp in cands.groupby(key_cols):
        if len(grp) < 2:
            continue
        platforms = grp.set_index("platform")
        available = list(platforms.index)
        if with_party:
            state, office, district, party, last, first = key
        else:
            state, office, district, last, first = key
            party = None
        rid = _race_id_from(state, office, district or None)
        meta = meta_map.get(rid, {}) if rid else {}
        if isinstance(meta, dict):
            label = meta.get("label") or (rid or f"{state} {office}")
            state_name = meta.get("state", state)
        else:
            label = getattr(meta, "label", None) or (rid or f"{state} {office}")
            state_name = getattr(meta, "state", state)

        for i, pa in enumerate(available):
            for pb in available[i + 1:]:
                ra = platforms.loc[pa]
                rb = platforms.loc[pb]
                row = _emit_pair(
                    ra["prob"], rb["prob"], pa, pb,
                    race_id=rid, label=label, state=state_name, office=office,
                    match_type=match_type,
                    candidate=ra["candidate_name"], party=party,
                    url_a=ra.get("url"), url_b=rb.get("url"),
                    market_id_a=ra.get("market_id"),
                    market_id_b=rb.get("market_id"),
                    volume_a=ra.get("volume"), volume_b=rb.get("volume"),
                    question_a=ra.get("raw_title", ""),
                    question_b=rb.get("raw_title", ""),
                )
                if row:
                    out.append(row)
    return out


# ── public entry point ────────────────────────────────────────────────────────

def run():
    """Build all election pair rows and write data/processed/election_pairs.csv."""
    print("Loading 2026 election markets...")
    meta = _get_race_meta()
    print(f"  race registry: {len(meta)} canonical races")

    general = _general_party_pairs(meta)
    gen_cand = _candidate_pairs(_load_general_candidates, "general_candidate",
                                meta, with_party=False)
    prim_cand = _candidate_pairs(_load_primary_candidates, "primary_candidate",
                                 meta, with_party=True)

    all_rows = general + gen_cand + prim_cand
    print(f"\nElection pair counts:")
    print(f"  general (party-level):   {len(general)}")
    print(f"  general_candidate:       {len(gen_cand)}")
    print(f"  primary_candidate:       {len(prim_cand)}")
    print(f"  total:                   {len(all_rows)}")

    if not all_rows:
        print("\nNo election pairs found — skipping CSV write.")
        return

    df = pd.DataFrame(all_rows)
    PROCESSED.mkdir(parents=True, exist_ok=True)
    out_path = PROCESSED / "election_pairs.csv"
    df.to_csv(out_path, index=False)
    print(f"\nWrote {len(df)} rows to {out_path}")


if __name__ == "__main__":
    run()
