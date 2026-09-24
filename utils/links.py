"""Market URL builders shared by the scrapers, matcher and elections module.

Polymarket: an event page (`/event/{event_slug}`) lists EVERY market in the
event — a 20-strike XRP ladder, a 12-contestant DWTS winner list, a full
set of margin-of-victory buckets — so users landed on the right page but
couldn't tell which option we priced. `/event/{event_slug}/{market_slug}`
opens the exact market (verified live 2026-09-23: the page's og:title is
the market question; an unknown market slug falls back to the generic
title). Single-market events use the same slug for both, so the event URL
is already exact there.
"""

import pandas as pd


def _s(v) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    return str(v).strip()


def polymarket_url(event_slug, market_slug=None) -> str | None:
    ev, mk = _s(event_slug), _s(market_slug)
    if ev and mk and mk != ev:
        return f"https://polymarket.com/event/{ev}/{mk}"
    if ev or mk:
        return f"https://polymarket.com/event/{ev or mk}"
    return None
