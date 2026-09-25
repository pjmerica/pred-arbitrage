"""Regression cases for fake-arb incidents. Each case is a real title pair
that once produced a fake 'guaranteed' arb (or a real one we must keep).

Run: py -m pytest tests/
"""

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from utils.election_shapes import party_win_side
from utils.links import polymarket_url
from scripts.matcher import match_political, match_threshold_pairs, political_contract_type
from scripts.elections import race_id_agrees_with_title


# ── election party-win allowlist (2026-09-23) ──────────────────────────────

@pytest.mark.parametrize("title,side", [
    ("Will the Democratic Party win the AZ-02 House seat?", "dem"),
    ("Will the Republican Party candidate win the NY-17 House election?", "rep"),
    ("Will the Democrats win the Maine Senate race in 2026?", "dem"),
    ("Will Democratic win the House race for WI-1?", "dem"),              # Kalshi raw
    ("Will a Republican win the House race for PA-7?", "rep"),            # Kalshi raw
    ("Will the Republican party win the governorship in Nevada", "rep"),  # Kalshi raw
    ("Will Democratics win the Senate race in Alaska?", "dem"),           # Kalshi raw
])
def test_party_win_accepts_plain_winner(title, side):
    assert party_win_side(title) == side


@pytest.mark.parametrize("title", [
    # margin-of-victory buckets (fake AZ-02 / NV-GOV / TN-SEN arbs)
    "Will the Democratic Party candidate win the AZ-02 House election by 0%-3%?",
    "Will the Republican Party candidate win the 2026 Tennessee Senate election by 45% or more?",
    "Will the Democratic Party candidate win the first round of the 2026 Georgia Senate election by 0%-3%?",
    # combos (fake 43% Alaska arb)
    "Will Democrats win the Alaska Governor election and Republicans win the Alaska Senate election?",
    "Will Democrats sweep the Alaska Senate and Governor elections?",
    "Will the Alaska Democrat perform best among these tossup Senate races?",
])
def test_party_win_rejects_derivatives(title):
    assert party_win_side(title) is None


@pytest.mark.parametrize("title", [
    "Will the Kansas 2026 Senate race be within 5%?",
    "Will Ohio have the closest Governor's race in 2026?",
    "Kansas Senate winner? — Adam Hamilton",
])
def test_political_never_matches_other_or_derivative(title):
    assert political_contract_type(title) not in ("party_winner", "candidate")


def _mk(rows, platform):
    df = pd.DataFrame(rows)
    df["platform"] = platform
    df["settle_date"] = ""
    return df


def test_margin_bucket_does_not_become_party_leg():
    """The highest-liquidity market per race must not win if it's a
    margin bucket (fake TN Senate 80% arb)."""
    pm = _mk([
        {"market_id": "margin", "question": "Will the Republican Party candidate win the 2026 Tennessee Senate election by 45% or more?",
         "race_id": "2026-SEN-TN", "implied_prob": 0.1, "liquidity": 1e6},
    ], "polymarket")
    pi = _mk([
        {"market_id": "pi-r", "question": "Which party will win the 2026 US Senate election in Tennessee? — Republican",
         "race_id": "2026-SEN-TN", "implied_prob": 0.95, "open_interest": 0},
    ], "predictit")
    assert match_political({"polymarket": pm, "predictit": pi}).empty


# ── threshold strike tolerance (2026-07-31) ────────────────────────────────

def test_threshold_requires_near_equal_strike():
    k = _mk([{"market_id": "k3", "question": "How high will XRP get in 2026? — Above $3.00",
              "implied_prob": 0.3, "category": "Crypto"}], "kalshi")
    p = _mk([{"market_id": "p32", "question": "Will XRP reach $3.20 by December 31, 2026?",
              "implied_prob": 0.2, "category": "Crypto"},
             {"market_id": "p30", "question": "Will XRP reach $3.00 by December 31, 2026?",
              "implied_prob": 0.3, "category": "Crypto"}], "polymarket")
    out = match_threshold_pairs({"kalshi": k, "polymarket": p})
    assert list(out["market_id_b"]) == ["p30"]


def test_threshold_rejects_adjacent_rung():
    """ETH $3,250 paired with both $3,300 and $3,200 under a 2% window."""
    k = _mk([{"market_id": "k", "question": "How high will ETH get in September? — Above $3,250.00",
              "implied_prob": 0.3, "category": "Crypto"}], "kalshi")
    p = _mk([{"market_id": "p1", "question": "Will Ethereum reach $3,300 in September?",
              "implied_prob": 0.2, "category": "Crypto"},
             {"market_id": "p2", "question": "Will Ethereum reach $3,200 in September?",
              "implied_prob": 0.4, "category": "Crypto"}], "polymarket")
    assert match_threshold_pairs({"kalshi": k, "polymarket": p}).empty


def test_county_market_is_not_candidate_win():
    assert political_contract_type(
        "California Governor: which counties will Steve Hilton win? — Orange") == "derivative"


@pytest.mark.parametrize("race_id,title,ok", [
    # Kalshi SENATELA-26 is actually the Kentucky race (their ticker bug)
    ("2026-SEN-LA", "Will Republicans win the Senate race in Kentucky?", False),
    ("2026-SEN-LA", "Will Republicans win the Senate race in Louisiana?", True),
    ("2026-H-AZ-02", "Will Democratic win the House race for AZ-2?", True),
    ("2026-H-AZ-02", "Some title with no state", True),
])
def test_ticker_race_id_must_agree_with_title(race_id, title, ok):
    assert race_id_agrees_with_title(race_id, title) is ok


def test_threshold_keeps_rounding_drift():
    k = _mk([{"market_id": "k", "question": "How high will Bitcoin get in 2026? — Above $199,999.99",
              "implied_prob": 0.1, "category": "Crypto"}], "kalshi")
    p = _mk([{"market_id": "p", "question": "Will Bitcoin reach $200,000 by December 31, 2026?",
              "implied_prob": 0.1, "category": "Crypto"}], "polymarket")
    assert len(match_threshold_pairs({"kalshi": k, "polymarket": p})) == 1


# ── deep links ─────────────────────────────────────────────────────────────

def test_polymarket_url_deep_links_market():
    assert polymarket_url("what-price-will-xrp-hit-before-2027", "will-xrp-reach-3-by-dec") == \
        "https://polymarket.com/event/what-price-will-xrp-hit-before-2027/will-xrp-reach-3-by-dec"
    assert polymarket_url("single", "single") == "https://polymarket.com/event/single"
    assert polymarket_url("ev", float("nan")) == "https://polymarket.com/event/ev"
    assert polymarket_url(None, None) is None


# ── outcome-signature gate for fuzzy pairs (2026-09-23) ────────────────────

from utils.proposition import incompatibility, kalshi_game_date, slug_game_date


@pytest.mark.parametrize("a,b", [
    ("Big Brother Season 28 — 2nd place — Taylor Brown", "Will Taylor Brown win Big Brother season 28?"),
    ("Big Brother Season 28 — 2nd place — Dee Valladares", "Will Dee Valladares come in third-place on Big Brother season 28?"),
    ("Dancing with the Stars Season 35 — Top 3 Finishers — Julia Stiles", "Will Julia Stiles win Dancing With the Stars: Season 35?"),
    ("Barcelona vs Paris FC: BTTS — Both Teams To Score", "FC Barcelona vs. Paris FC: Both Teams to Score in Second Half"),
    ("Austria vs Israel: BTTS — Both Teams To Score", "Australia vs. Brazil: Both Teams to Score"),
    ("Republic of Korea vs Ecuador: BTTS — Both Teams To Score", "Israel vs. Republic of Ireland: Both Teams to Score"),
    ("Northern Ireland vs Hungary: BTTS — Both Teams To Score", "Georgia vs. Northern Ireland: Both Teams to Score"),
    ("46th FIDE Chess Olympiad Women's Tournament Winner — China", "Will China win the 46th FIDE Chess Olympiad Open Tournament?"),
    ("League Phase Top Finisher — Real Madrid", "Will Real Madrid finish last in UCL league phase?"),
    ("2028 UEFA Euros Qualifiers — Northern Ireland", "Will Northern Ireland win the UEFA EURO 2028?"),
    ("Dancing with the Stars Season 35 — Finalists — Maura Higgins", "Will Maura Higgins win Dancing With the Stars: Season 35?"),
    ("2026 FIFA World Cup Winner — Argentina", "Will Argentina reach the 2026 FIFA World Cup final?"),
    ("How high will XRP get in September? — Above $1.70", "Will the price of XRP be above $1.70 on September 25?"),
    ("Time's Person of the Year for 2026 — Mojtaba Khamenei", "Will Ali Khamenei be TIME Person of the Year 2026?"),
    ("Will a hurricane make landfall in Hawaii in 2026? — Before 2027", "Will any Category 5 hurricane make landfall in the US in before 2027?"),
])
def test_signature_rejects_different_outcomes(a, b):
    assert incompatibility(a, b) is not None


@pytest.mark.parametrize("a,b", [
    ("Big Brother Season 28 — Winner — Taylor Brown", "Will Taylor Brown win Big Brother season 28?"),
    ("Big Brother Season 28 — 3rd place  — Dee Valladares", "Will Dee Valladares come in third-place on Big Brother season 28?"),
    ("Ballon d'Or 2026: Top 3 Finishers — Lamine Yamal", "Will Lamine Yamal finish in the top 3 of the 2026 Ballon d'Or?"),
    ("Republic of Korea vs Ecuador: First Half BTTS — 1st Half: Both Teams To Score", "Korea Republic vs. Ecuador: Both Teams to Score in First Half"),
    ("New York RB vs Saint Louis: BTTS — Both Teams To Score", "New York Red Bulls vs. St. Louis City SC: Both Teams to Score"),
    ("Gibraltar vs Sao Tome and Principe: BTTS — Both Teams To Score", "Gibraltar vs. São Tomé e Príncipe: Both Teams to Score"),
    ("Toronto vs Baltimore: Extra Innings — Game goes to extra innings", "Will the game go to extra innings?: Toronto Blue Jays vs. Baltimore Orioles"),
    ("Hurricane Polo category? — Category 5 or above", "Will Hurricane Polo peak at Category 5?"),
    ("Bulgarian presidential election winner? — Vasil Terziev", "Will Vassil Terziev win the next Bulgarian presidential election?"),
    ("2028 Democratic presidential nominee — Alexandria Ocasio-Cortez", "Will Alexandria Ocasio-Cortez win the 2028 Democratic presidential nomination?"),
    ("How high will XRP get in 2026? — Above $3.00", "Will XRP reach $3.00 by December 31, 2026?"),
    ("Lightweight Title Holder on Dec 31, 2026? — Arman Tsarukyan", "Will Arman Tsarukyan be the UFC Lightweight Champion on December 31, 2026?"),
    ("Who will recognize Palestine before 2027? — USA", "Will the US recognize Palestine before 2027?"),
])
def test_signature_keeps_same_outcome(a, b):
    assert incompatibility(a, b) is None


def test_same_matchup_different_day_rejected():
    """MLB series repeat the matchup on consecutive days."""
    a = "Toronto vs Baltimore: Extra Innings — Game goes to extra innings"
    b = "Will the game go to extra innings?: Toronto Blue Jays vs. Baltimore Orioles"
    ga = kalshi_game_date("KXMLBEXTRAS-26SEP232210TORBAL-EXTRAS")
    gb = slug_game_date("mlb-tor-bal-2026-09-22")
    assert (ga, gb) == ("2026-09-23", "2026-09-22")
    assert incompatibility(a, b, ga, gb) == "game date 2026-09-23 vs 2026-09-22"
    assert incompatibility(a, b, ga, "2026-09-23") is None
    assert incompatibility(a, b, float("nan"), None) is None


# ── basket direction (threshold basis risk, 2026-09-23) ────────────────────

from scripts.arb_scanner import compute_arb


def test_compute_arb_reports_yes_leg():
    # A cheap YES on B + cheap NO on A → basket buys YES on leg b.
    r = compute_arb(0.8, 0.1, 0.02, 0.02,
                    bid_a=0.80, ask_a=0.81, bid_b=0.10, ask_b=0.12,
                    no_bid_a=0.18, no_ask_a=0.19, no_bid_b=0.88, no_ask_b=0.90)
    assert r["arb_type"] == "guaranteed" and r["yes_leg"] == "b"
    r = compute_arb(0.5, 0.5, 0.02, 0.02,
                    bid_a=0.49, ask_a=0.51, bid_b=0.49, ask_b=0.51,
                    no_bid_a=0.49, no_ask_a=0.51, no_bid_b=0.49, no_ask_b=0.51)
    assert r["arb_type"] == "price-gap" and r["yes_leg"] is None


@pytest.mark.parametrize("a,b,same", [
    ("Poland vs Bosnia and Herzegovina: Correct Score — Poland wins 2-0",
     "Exact Score: Poland 0 - 2 Bosnia and Herzegovina?", False),   # opposite winner
    ("Poland vs Bosnia and Herzegovina: Correct Score — Poland wins 2-0",
     "Exact Score: Poland 2 - 0 Bosnia and Herzegovina?", True),
    ("Poland vs Bosnia and Herzegovina: Correct Score — Bosnia and Herzegovina wins 1-0",
     "Exact Score: Poland 0 - 1 Bosnia and Herzegovina?", True),
    ("Poland vs Bosnia and Herzegovina: Correct Score — Draw 1-1",
     "Exact Score: Poland 1 - 1 Bosnia and Herzegovina?", True),
    ("Poland vs Bosnia and Herzegovina: Correct Score — Poland wins 1-0",
     "Poland vs. Bosnia and Herzegovina: Both Teams to Score", False),
])
def test_exact_score_orientation(a, b, same):
    assert (incompatibility(a, b) is None) is same


# ── stake sizing must hedge (2026-09-23) ───────────────────────────────────

def test_stakes_buy_equal_contracts_on_both_legs():
    """YES 3.5c on B + NO 90c on A. The old inverse-odds split put $96 on
    the 3.5c leg — 2,750 vs 4 contracts, not a hedge."""
    r = compute_arb(0.9, 0.035, 0.02, 0.0,
                    bid_a=0.09, ask_a=0.10, bid_b=0.03, ask_b=0.035,
                    no_bid_a=0.89, no_ask_a=0.90, no_bid_b=0.96, no_ask_b=0.97)
    assert r["arb_type"] == "guaranteed" and r["yes_leg"] == "b"
    contracts_a = r["stake_a_dollars"] / 0.90    # NO on A
    contracts_b = r["stake_b_dollars"] / 0.035   # YES on B
    assert abs(contracts_a - contracts_b) / contracts_a < 0.01
    assert abs(r["stake_a_dollars"] + r["stake_b_dollars"] - 100) < 0.02
    # profit on $100: 100/0.935 baskets * (1 - 0.935 - 0.02) net each
    assert abs(r["profit_dollars"] - 100 * 0.045 / 0.935) < 0.01


def test_election_fallback_stakes_hedge():
    from scripts.elections import _compute_arb_math
    # Dem 40c on A, Rep 55c on B (each platform partitions to ~1).
    r = _compute_arb_math(0.40, 0.46, 0.59, 0.55, 0.02, 0.0)
    assert r["arb_type"] == "guaranteed"
    assert abs(r["stake_a_dollars"] / 0.40 - r["stake_b_dollars"] / 0.55) < 0.1


@pytest.mark.parametrize("a,b,same", [
    # Kalshi ≥ Cat 2 vs Polymarket exactly Cat 2 (99.5¢ vs 0.5¢)
    ("Hurricane Polo category? — Category 2 or above", "Will Hurricane Polo peak at Category 2?", False),
    # Cat 5 is the top of the scale: ≥5 == exactly 5
    ("Hurricane Polo category? — Category 5 or above", "Will Hurricane Polo peak at Category 5?", True),
])
def test_bound_or_above_vs_exact(a, b, same):
    assert (incompatibility(a, b) is None) is same


# ── resolution-window containment (2026-09-24) ─────────────────────────────

from utils.rules_window import window, yes_window_contains_no

KALSHI_HAWAII = "If a 74 mph or higher hurricane makes landfall in Hawaii during the 2026 hurricane season, then the market resolves to Yes."
POLY_HAWAII = ("This market will resolve to \"Yes\" if any storm makes landfall in the State of Hawaii at Category 1 "
               "strength or higher ... between market creation and December 31, 2026, 11:59:59 PM ET.")
KALSHI_BTC = "If the Bitcoin spot price according to the CF Bitcoin Real-Time Index is below $55000.00 starting Feb 5, 2026 and before Jan 1, 2027 at 12:00am ET, then the market resolves to Yes."
POLY_BTC = ("This market will immediately resolve to \"Yes\" if any Binance 1 minute candle for Bitcoin (BTC/USDT) "
            "between November 24, 2025, 14:00 and December 31, 2026, 23:59 in the ET timezone has a final \"Low\" price ...")


def test_window_parse():
    import datetime as dt
    assert window(KALSHI_HAWAII) == (None, dt.date(2026, 11, 30))
    assert window(POLY_BTC) == (dt.date(2025, 11, 24), dt.date(2026, 12, 31))
    assert window(KALSHI_BTC) == (dt.date(2026, 2, 5), dt.date(2027, 1, 1))


def test_yes_on_narrower_window_is_not_a_hedge():
    assert yes_window_contains_no(KALSHI_HAWAII, POLY_HAWAII)[0] is False   # YES Kalshi (season) + NO Poly
    assert yes_window_contains_no(POLY_HAWAII, KALSHI_HAWAII)[0] is True


def test_yes_on_wider_window_is_fine():
    assert yes_window_contains_no(POLY_BTC, KALSHI_BTC)[0] is True           # the board's direction
    assert yes_window_contains_no(KALSHI_BTC, POLY_BTC)[0] is False          # starts later


# ── real fee formulas (2026-09-24) ─────────────────────────────────────────

from utils.fees import leg_fee, kalshi_spec, polymarket_spec, predictit_spec, FEE_SAFETY_MARGIN


def test_fee_formulas_match_published_examples():
    # Polymarket docs: 100 crypto shares at $0.50 -> $1.75
    assert abs(100 * leg_fee("polymarket", polymarket_spec(0.07), 0.50) - 1.75) < 1e-9
    # Kalshi: 0.07 x C x P x (1-P); at 50c = 1.75c per contract
    assert abs(leg_fee("kalshi", kalshi_spec(1), 0.50) - 0.0175) < 1e-9
    assert leg_fee("kalshi", kalshi_spec(0), 0.50) == 0            # fee-free series
    assert abs(leg_fee("kalshi", kalshi_spec(0.5), 0.50) - 0.00875) < 1e-9
    assert leg_fee("polymarket", polymarket_spec(0.0), 0.3) == 0   # feesEnabled False
    assert abs(leg_fee("predictit", predictit_spec(), 0.40) - (0.10 * 0.60 + 0.05)) < 1e-9
    # unknown -> conservative flat fallback
    assert leg_fee("kalshi", None, 0.5) == 0.02


def test_compute_arb_uses_leg_fees():
    # BTC <55k board case: YES Polymarket 0.11 + NO Kalshi 0.86 (gross 3c).
    kw = dict(bid_a=0.13, ask_a=0.15, bid_b=0.10, ask_b=0.11,
              no_bid_a=0.85, no_ask_a=0.86, no_bid_b=0.89, no_ask_b=0.90)
    flat = compute_arb(0.14, 0.105, 0.02, 0.02, **kw)
    real = compute_arb(0.14, 0.105, 0.02, 0.02, **kw,
                       leg_fees=("kalshi", kalshi_spec(1), "polymarket", polymarket_spec(0.07)))
    assert flat["arb_type"] == "pre-fee"          # 3c gross < 4c flat fees
    assert real["arb_type"] == "guaranteed" and real["yes_leg"] == "b"
    fees = 0.07 * 0.86 * 0.14 + 0.07 * 0.11 * 0.89 + FEE_SAFETY_MARGIN
    assert abs(real["guaranteed_return_pct"] - 100 * (0.03 - fees) / 0.97) < 0.01


# ── curated series map (2026-09-24) ────────────────────────────────────────

from utils.series_map import load as load_series_map, status as series_status, slug_family


def test_series_map_statuses():
    fams = load_series_map()
    assert series_status(fams, "KXFRPRESBALLOT", "2027-french-presidential-election-who-will-be-on-the-ballot")[0] == "approved"
    assert series_status(fams, "KXMLSBTTS", "mls-atl-nyc-2026-09-26-more-markets")[0] == "approved"
    assert series_status(fams, "KXHURPATHHAWAII", "will-a-hurricane-make-landfall-in-hawaii-before-2027-20260721182828397")[0] == "rejected"
    assert series_status(fams, "KXNOBELPEACE", "nobel-peace-prize-winner-2026-139")[0] == "rejected"
    assert series_status(fams, "KXSOMETHINGNEW", "some-new-event")[0] == "unreviewed"
    # wrong pairing inside an approved series is NOT approved
    assert series_status(fams, "KXMLSBTTS", "which-artists-will-release-new-albums-in-2026")[0] == "unreviewed"


def test_series_approval_lapses():
    fams = load_series_map()
    slug = "unl-tur-fra-2026-09-25-more-markets"
    assert series_status(fams, "KXUEFANLBTTS", slug, today="2026-10-01")[0] == "approved"
    assert series_status(fams, "KXUEFANLBTTS", slug, today="2026-12-15")[0] == "unreviewed"


def test_slug_family():
    assert slug_family("mls-atl-nyc-2026-09-26-more-markets") == "mls-*-<date>"
    assert slug_family("big-brother-season-28-winner-20260708173711844") == "big-brother-season-28-winner"


def test_tiny_baskets_are_not_guaranteed():
    """Real fees let 0.06%-on-capital baskets through; below the 0.25%
    floor they're pre-fee, not guaranteed (TN Senate 0.8c gross, 2026-09-25)."""
    r = compute_arb(0.02, 0.03, 0.02, 0.02,
                    bid_a=0.018, ask_a=0.019, bid_b=0.026, ask_b=0.027,
                    no_bid_a=0.98, no_ask_a=0.982, no_bid_b=0.972, no_ask_b=0.973,
                    leg_fees=("kalshi", kalshi_spec(1), "polymarket", polymarket_spec(0.04)))
    assert r["arb_type"] == "pre-fee"


def test_series_map_ignores_trailing_slug_ids():
    """Anchored patterns must match Polymarket's id-suffixed slugs (approved
    families were landing in the review queue, 2026-09-25)."""
    fams = load_series_map()
    for ser, slug in (("KXCHESSOLYMPIAD", "46th-fide-chess-olympiad-open-tournament-winner-20260716211453703"),
                      ("KXBIGBROTHER", "big-brother-season-28-winner-20260708173711844"),
                      ("KXNCAAFUNDEFEATED", "ncaa-football-team-to-have-an-undefeated-season-202608042024"),
                      ("KXNETFLIXRANKSHOW", "what-will-be-the-top-us-netflix-show-this-week-20260929"),
                      ("KXESTPRES", "next-president-of-estonia-20260727225811942")):
        assert series_status(fams, ser, slug)[0] == "approved", slug


# 2026-09-25: year-end windows get their own bucket, separate from a single
# month. "in December" (December only) must NOT key-match "in 2026" (full
# year): a strike touched in October wins the full-year YES and can lose the
# December-only leg, so both legs lose. Month names match on word boundaries.
from scripts.matcher import _extract_threshold_key


@pytest.mark.parametrize("title", [
    "Will Gold (GC) hit (HIGH) $5,000 by end of December?",
    "Will Bitcoin reach $200,000 by December 31, 2026?",
    "Will Bitcoin reach $200,000 before 2027?",
    "Will Bitcoin reach $200,000 this year?",
])
def test_threshold_year_end_bucket(title):
    key = _extract_threshold_key(title, "polymarket")
    assert key is not None and key[3] == "year" and key[4] is None


def test_threshold_kalshi_year_bucket():
    key = _extract_threshold_key("How high will Bitcoin get in 2026? - Above $200,000", "kalshi")
    assert key is not None and key[3] == "year"


def test_threshold_december_only_is_not_year():
    k_month = _extract_threshold_key("Will Bitcoin reach $200,000 in December?", "polymarket")
    k_year = _extract_threshold_key("How high will Bitcoin get in 2026? - Above $200,000", "kalshi")
    assert k_month[3] == "dec" and k_year[3] == "year"
    assert k_month[:4] != k_year[:4]


def test_threshold_month_word_boundary():
    # "market" is not March; no month and no year phrase -> no key.
    assert _extract_threshold_key("Will the Bitcoin market reach $200,000?", "polymarket") is None


# 2026-09-25: race ids from titles. FL/OH hold only SPECIAL Senate races in
# 2026 (Kalshi: SENATEFLS/SENATEOHS -> -S), other cycles and Mexico's Baja
# California must get no 2026 id, and KXHOUSE{ST}{D} series need a branch.
from scripts.matcher import infer_race_id
from scripts.elections import _race_id_from_title
from scrapers.kalshi import infer_race_id_from_ticker


@pytest.mark.parametrize("title, rid", [
    ("Will the Democrats win the Florida Senate race in 2026?", "2026-SEN-FL-S"),
    ("Which party will win the 2026 US Senate special election in Ohio?", "2026-SEN-OH-S"),
    ("Will the Democrats win the Maine Senate race in 2026?", "2026-SEN-ME"),
    ("Florida Senate winner? (2028) — Democratic party", None),
    ("Kentucky governor winner? (2027) — Republican", None),
    ("Will Juan Carlos Hank win the 2027 Baja California Governor Election?", None),
])
def test_title_race_ids(title, rid):
    assert infer_race_id(title) == rid
    assert _race_id_from_title(title) == rid


def test_kalshi_kxhouse_series_race_id():
    assert infer_race_id_from_ticker("KXHOUSETX32", "KXHOUSETX32-26") == "2026-H-TX-32"
    assert infer_race_id_from_ticker("KXHOUSEWA8", "KXHOUSEWA8-26") == "2026-H-WA-08"
    assert infer_race_id_from_ticker("KXHOUSETX32", "KXHOUSETX32-28") is None
