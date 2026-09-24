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
