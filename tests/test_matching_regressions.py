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
