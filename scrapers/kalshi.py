"""
Kalshi full-market scraper — uses the trade-api/v2 endpoint (modern).

The old v1/events endpoint was capped at ~100 events total. The new
trade-api/v2/events?with_nested_markets=true returns ~5000 open events
(~40k markets) across Sports, Elections, Entertainment, Politics,
Economics, Crypto, and more.

For 2026 election races (House, Senate, Governor), we still infer
canonical race_ids from the series_ticker.

Output: data/raw/kalshi_markets.csv
"""

import urllib.request
import urllib.parse
import urllib.error
import json
import time
import re
import sys
import pandas as pd
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
from utils.http_headers import browser_xhr_headers

RAW = ROOT / "data" / "raw"
BASE = "https://api.elections.kalshi.com/trade-api/v2"
# Kalshi's WAF began rejecting "Mozilla/5.0 (research/...)" with 403 in
# June 2026; pred-arb additionally needed the full Sec-Fetch/Referer/Origin
# set to look like a real kalshi.com webapp XHR. See utils/http_headers.py.
HEADERS = browser_xhr_headers("https://kalshi.com")
PAGE_SIZE = 200

STATE_ABBREVS = {
    "AL","AK","AZ","AR","CA","CO","CT","DE","FL","GA","HI","ID","IL","IN","IA","KS",
    "KY","LA","ME","MD","MA","MI","MN","MS","MO","MT","NE","NV","NH","NJ","NM","NY",
    "NC","ND","OH","OK","OR","PA","RI","SC","SD","TN","TX","UT","VT","VA","WA","WV",
    "WI","WY",
}


# HTTP status codes worth retrying. 429 = rate limit, 5xx = server error,
# 403 sometimes fires as a transient WAF/edge hiccup (saw it on Kalshi
# 2026-06-07 during a manual run; immediate retry worked).
RETRY_CODES = {403, 408, 429, 500, 502, 503, 504}

def get(path, params=None, max_retries=6):
    url = f"{BASE}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    # Backoff: 5, 10, 20, 40, 60, 60s. Kalshi's WAF blocks for 30-60s
    # after a burst of 403s; shorter backoffs blew through the budget.
    for attempt in range(max_retries):
        req = urllib.request.Request(url, headers=HEADERS)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code in RETRY_CODES and attempt < max_retries - 1:
                wait = min(60, 5 * (2 ** attempt))
                print(f"  HTTP {e.code} on {path}, retry {attempt+1}/{max_retries-1} in {wait}s...")
                time.sleep(wait)
                continue
            raise
        except urllib.error.URLError as e:
            if attempt < max_retries - 1:
                wait = min(60, 5 * (2 ** attempt))
                print(f"  Network error on {path}: {e}, retry {attempt+1}/{max_retries-1} in {wait}s...")
                time.sleep(wait)
                continue
            raise
    raise RuntimeError(f"Failed after {max_retries} retries: {url}")


def infer_race_id_from_ticker(ticker: str, event_ticker: str = "", title: str = "") -> str | None:
    """Infer race_id from a Kalshi series ticker like HOUSECA47, SENATEPARTYPA.

    Critical fix: Kalshi has overlapping series tickers across cycles, e.g.
    SENATEOH-26 vs SENATEOH-28 (regular) vs SENATEOHS-26 (special).
    The year suffix is in event_ticker; we extract it and reject anything
    that isn't 2026. Specials get '-S' suffix on race_id.
    """
    t = (ticker or "").upper()
    evt = (event_ticker or "").upper()

    # Drop events from non-2026 cycles. Year usually appears as -YY at end
    # of event_ticker (SENATEOH-28) or before another segment (KXSENATEPA-26-...).
    year_match = re.search(r"-(\d{2})$", evt) or re.search(r"-(\d{2})-", evt)
    if year_match and year_match.group(1) != "26":
        return None

    is_special = "special" in (title or "").lower()
    def s_suffix(stem_match):
        # Add -S if the series stem ends in S (e.g. SENATEOHS), OR the title
        # mentions "special".
        return "-S" if (stem_match or is_special) else ""

    # HOUSE{ST}{D} e.g. HOUSECA47, HOUSENH2
    m = re.match(r"HOUSE([A-Z]{2})(\d+)$", t)
    if m and m.group(1) in STATE_ABBREVS:
        return f"2026-H-{m.group(1)}-{m.group(2).zfill(2)}"

    # SENATEPARTY{ST}[S]
    m = re.match(r"SENATEPARTY[-_]?([A-Z]{2})(S?)$", t)
    if m and m.group(1) in STATE_ABBREVS:
        return f"2026-SEN-{m.group(1)}{s_suffix(m.group(2) == 'S')}"

    # SENATE{ST}[S] — anchored so SENATEOHS doesn't match the SENATEOH branch
    m = re.match(r"SENATE[-_]?([A-Z]{2})(S?)$", t)
    if m and m.group(1) in STATE_ABBREVS:
        return f"2026-SEN-{m.group(1)}{s_suffix(m.group(2) == 'S')}"

    # KXSENATE{ST}[S]
    m = re.match(r"KXSENATE([A-Z]{2})(S?)$", t)
    if m and m.group(1) in STATE_ABBREVS:
        return f"2026-SEN-{m.group(1)}{s_suffix(m.group(2) == 'S')}"

    # GOVPARTY{ST}[S]
    m = re.match(r"GOVPARTY([A-Z]{2})(S?)$", t)
    if m and m.group(1) in STATE_ABBREVS:
        return f"2026-GOV-{m.group(1)}{s_suffix(m.group(2) == 'S')}"

    # KXGOV{ST}{suffix}
    m = re.match(r"KXGOV([A-Z]{2})[A-Z0-9]*$", t)
    if m and m.group(1) in STATE_ABBREVS:
        return f"2026-GOV-{m.group(1)}{s_suffix(False)}"

    return None


def fetch_all_events_with_markets():
    """Paginate all open events with nested markets via trade-api/v2."""
    all_events = []
    cursor = None
    page = 0
    while True:
        params = {"limit": PAGE_SIZE, "status": "open", "with_nested_markets": "true"}
        if cursor:
            params["cursor"] = cursor
        try:
            data = get("/events", params)
        except Exception as e:
            print(f"  Error at page {page}: {e}")
            break
        events = data.get("events", [])
        all_events.extend(events)
        cursor = data.get("cursor")
        page += 1
        if page % 5 == 0:
            print(f"  Fetched {len(all_events)} events so far...")
        if not cursor or len(events) < PAGE_SIZE:
            break
        time.sleep(0.25)  # be polite, avoid rate limits
    return all_events


def parse_market(event, market):
    # trade-api/v2 uses decimal dollars (0–1) not cents
    def to_float(v):
        try:
            return float(v) if v is not None and v != "" else None
        except (TypeError, ValueError):
            return None

    yes_bid = to_float(market.get("yes_bid_dollars"))
    yes_ask = to_float(market.get("yes_ask_dollars"))
    last    = to_float(market.get("last_price_dollars"))

    # implied_prob = last_price. This is the DISPLAY price — what
    # kalshi.com shows on the headline when a user clicks into the
    # market. Don't change this without reading HANDOFF.md "Price
    # semantics — READ THIS BEFORE CHANGING ANY PRICE FIELD".
    #
    # Why last_price and not midpoint: Kalshi's website surfaces the
    # last-trade number, not the bid/ask midpoint. For Somaliland on
    # 2026-06-21: bid 0.15, ask 0.21, last 0.14. Kalshi UI showed 14¢.
    # Our dashboard used to show 18¢ (midpoint) which mismatched —
    # user reported, we fixed.
    #
    # PER-PLATFORM RULE (each platform's UI surfaces something different):
    #   Kalshi:     last_price  (HERE)
    #   Polymarket: bid/ask midpoint  (in scripts/freshen_polymarket.py)
    #   PredictIt:  midpoint of bestBuyYes/bestSellYes  (in scrapers/predictit.py)
    # DO NOT unify these without verifying the UI of every platform.
    # Polymarket's UI does NOT show last_trade_price even though
    # Kalshi's does — see the Espaillat incident in HANDOFF.md.
    #
    # Fallback chain when last_price is missing/garbage:
    #   1. midpoint of bid + ask  (tight book = reasonable proxy)
    #   2. ask alone  (only sellers)
    #   3. bid alone  (only buyers)
    # Arb math STILL uses real bid/ask via fillable_ask / fillable_no_ask
    # (see compute_arb in arb_scanner.py) — implied_prob is for DISPLAY
    # only and doesn't affect whether something is flagged a guaranteed
    # arb.
    implied_prob = None
    if last is not None and 0.01 < last < 0.99:
        implied_prob = last
    elif (yes_bid is not None and yes_ask is not None
            and yes_bid > 0 and yes_ask > 0
            and (yes_ask - yes_bid) <= 0.30):
        implied_prob = (yes_bid + yes_ask) / 2
    elif yes_ask is not None and yes_ask > 0 and yes_bid in (None, 0):
        implied_prob = yes_ask
    elif yes_bid is not None and yes_bid > 0 and yes_ask in (None, 0):
        implied_prob = yes_bid

    series_ticker = event.get("series_ticker", "")
    event_ticker  = event.get("event_ticker", "")
    category      = event.get("category", "")
    event_title   = event.get("title", "")
    event_sub     = event.get("sub_title", "")
    market_ticker = market.get("ticker", "")
    yes_sub       = market.get("yes_sub_title", "") or ""
    # Kalshi's raw per-market title (e.g. "Will Democratic win the House
    # race for WI-1?"). Different from our constructed `title` below.
    # Preserved so elections.py can use it for side-detection with the
    # same regex polling-agg uses — see SCRAPER_NOTES.md and
    # elections.py:_load_kalshi_general.
    raw_market_title = market.get("title", "") or ""

    # Build the most useful "question" field. If the event has many markets
    # (e.g. "Who will win the primary" with 15 candidates), combine event
    # title + yes_sub_title so fuzzy matching has enough to work with.
    if yes_sub and yes_sub.strip() and yes_sub.strip().lower() not in ("yes", "no"):
        title = f"{event_title} — {yes_sub}"
    else:
        title = event_title

    race_id = infer_race_id_from_ticker(series_ticker, event_ticker, event_title)

    # URL construction: kalshi.com routes are SPA-driven. The path that
    # both Kalshi's own UI uses and that our users will hit when clicking
    # through is:
    #
    #   https://kalshi.com/markets/{series_ticker_lower}/{event_ticker_lower}
    #
    # The two-segment form is critical for series that contain MULTIPLE
    # events. Example incident (2026-06-21): KXNHPRIMARY covers all NH
    # primaries (NH-01 D, NH-01 R, NH-02 D, NH-02 R). The single-segment
    # URL `kalshi.com/markets/KXNHPRIMARY` makes Kalshi's SPA pick an
    # event arbitrarily — for the "NH-01 R Noveletsky" row it landed on
    # KXNHPRIMARY-02R26 (NH-02) instead of -01R26 (NH-01). Adding the
    # event_ticker pins the right one.
    #
    # We URL-lowercase both segments to match what Kalshi's own
    # navigation produces (their canonical URLs are lowercase even though
    # the API tickers are uppercase).
    if series_ticker and event_ticker:
        market_url = f"https://kalshi.com/markets/{series_ticker.lower()}/{event_ticker.lower()}"
    elif series_ticker:
        market_url = f"https://kalshi.com/markets/{series_ticker.lower()}"
    else:
        market_url = ""

    return {
        "ticker":        market_ticker,
        "event_ticker":  event_ticker,
        "series_ticker": series_ticker,
        "title":         title,
        # raw_market_title preserves Kalshi's API `title` field exactly
        # (e.g. "Will Democratic win the House race for WI-1?"). This is
        # the title shape polling-agg uses for side detection. elections.py
        # consumes it via _load_kalshi_general; downstream side-regex
        # then matches polling-agg's exactly.
        "raw_market_title": raw_market_title,
        "subtitle":      event_sub,
        # yes_sub_title is what kalshi.com displays as the "side label"
        # for a market — for general-election rows it's the party
        # ("Democratic party" / "Republican party"); for candidate rows
        # it's the candidate name. We construct `title` by joining
        # event_title + yes_sub above, but preserving the raw field on
        # its own column lets downstream code split markets by side
        # without parsing a joined string. Polling-agg's Kalshi data
        # has a different title shape; pulling yes_sub_title out as a
        # column keeps pred-arb's dem/rep logic from depending on the
        # constructed title format.
        "yes_sub_title": yes_sub,
        "category":      category,
        "tags":          "",
        "race_id":       race_id,
        "yes_bid":       yes_bid,
        "yes_ask":       yes_ask,
        "implied_prob":  round(implied_prob, 4) if implied_prob is not None else None,
        "open_interest": to_float(market.get("open_interest_fp")) or 0,
        "volume":        to_float(market.get("volume_fp")) or 0,
        "close_date":    str(market.get("close_time", market.get("expiration_time", "")))[:10],
        "url":           market_url,
        "fetched_at":    datetime.now(timezone.utc).isoformat(),
    }


def run():
    RAW.mkdir(parents=True, exist_ok=True)

    print("Fetching all Kalshi open events via trade-api/v2...")
    events = fetch_all_events_with_markets()
    print(f"  Total events: {len(events)}")

    rows = []
    seen_tickers = set()
    # Skip multivariate parlay markets — they have no equivalent on PM/PI
    SKIP_EVENT_PREFIXES = ("KXMVE", "KXMULTIVARIATE")

    # Drop markets whose close_date is already in the past. Kalshi leaves
    # resolved markets in the "open events" feed for days/weeks; on
    # 2026-06-21 a sample of 44k markets contained ~17.8k with close_date
    # already past. Those can't be traded but were diluting matcher input
    # and producing dead-prop arbs.
    today_iso = datetime.now(timezone.utc).date().isoformat()
    n_one_sided = 0
    n_past = 0
    for event in events:
        et = (event.get("event_ticker") or "").upper()
        if any(et.startswith(p) for p in SKIP_EVENT_PREFIXES):
            continue
        for market in event.get("markets") or []:
            row = parse_market(event, market)
            # Drop already-closed markets.
            cd = (row.get("close_date") or "")[:10]
            if cd and re.match(r"^\d{4}-\d{2}-\d{2}$", cd) and cd < today_iso:
                n_past += 1
                continue
            # Skip one-sided books — markets with a yes_ask but no yes_bid
            # can be bought into but not exited. They show a "price" that
            # isn't actually two-sided and clutter the dashboard. User
            # explicitly asked to filter these out at the pipeline level
            # (2026-06-21). NOTE: we treat 0.0 as "no bid" here even
            # though Kalshi technically allows a 1¢ resting bid — those
            # 1¢ bids are usually market-maker scrapers, not real
            # exit liquidity.
            yb = row.get("yes_bid")
            if yb is None or yb <= 0:
                n_one_sided += 1
                continue
            key = row["ticker"]
            if key and key not in seen_tickers:
                seen_tickers.add(key)
                rows.append(row)
    if n_past:
        print(f"  Skipped {n_past} markets with close_date in the past")
    if n_one_sided:
        print(f"  Skipped {n_one_sided} one-sided markets (no bid - can't exit)")

    df = pd.DataFrame(rows)
    out = RAW / "kalshi_markets.csv"

    # If the Kalshi API returned nothing, FAIL THE RUN. The workflow's
    # commit step won't fire, so the live dashboard keeps showing the
    # last good data instead of partial/empty data being pushed.
    if df.empty or "implied_prob" not in df.columns:
        raise SystemExit(
            f"Kalshi API returned no usable rows ({len(df)} raw). "
            "Aborting so the workflow doesn't overwrite docs/ with partial data."
        )

    df = df[df["implied_prob"].notna() & (df["implied_prob"] > 0) & (df["implied_prob"] < 1)]
    df.to_csv(out, index=False)
    print(f"\nSaved {len(df)} markets to {out}")

    race_matched = df["race_id"].notna().sum() if "race_id" in df.columns else 0
    print(f"Race_id matched: {race_matched}/{len(df)}")
    if "category" in df.columns and len(df):
        print("\nBy category:")
        print(df.groupby("category")["ticker"].count().sort_values(ascending=False).to_string())


if __name__ == "__main__":
    run()
