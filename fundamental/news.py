"""
fundamental/news.py — ported from clean v5's plugin_news.py.
ForexFactory economic calendar, free, no API key. Context-only -- see
fundamental/dxy.py's module docstring. Note this plugin's own score_adj can
reach -10 in clean v5, which functioned as a near-hard block there; in
v12-core it is surfaced as diagnostic context only (see
fundamental/context.py) unless/until the project explicitly decides to wire
a news blackout into FinalGate as a market-risk permission (not a
trend/direction gate, so it would not violate the Red Lines list).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

import requests

UTC = timezone.utc

_news_cache: list = []
_cache_time: Optional[datetime] = None
CACHE_MINUTES = 60


def fetch_ff_calendar() -> list:
    global _news_cache, _cache_time

    now = datetime.now(UTC)
    if _cache_time and (now - _cache_time).total_seconds() < CACHE_MINUTES * 60:
        return _news_cache

    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
            "Referer": "https://www.forexfactory.com/",
        }
        r = requests.get(
            "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
            headers=headers,
            timeout=10,
        )
        if r.status_code != 200:
            return _news_cache

        data = r.json()
        events = []
        for item in data:
            if item.get("impact") not in ["High", "Medium"]:
                continue
            if item.get("currency") not in ["USD", "XAU"]:
                continue
            try:
                dt_str = item.get("date", "")
                dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
            except Exception:
                continue
            events.append(
                {
                    "title": item.get("title", ""),
                    "currency": item.get("currency", ""),
                    "impact": item.get("impact", ""),
                    "datetime": dt,
                }
            )

        _news_cache = events
        _cache_time = now
        return events

    except Exception:
        return _news_cache


def check_news_filter(buffer_before: int = 30, buffer_after: int = 30) -> dict:
    now = datetime.now(UTC)
    events = fetch_ff_calendar()

    for event in events:
        dt = event["datetime"]
        diff = (dt - now).total_seconds() / 60
        impact = event["impact"]
        title = event["title"]
        curr = event["currency"]

        if 0 <= diff <= buffer_before:
            return {
                "safe": False,
                "score_adj": -10,
                "reason": f"High impact in {diff:.0f}min: {title} ({curr})",
                "next_news": title,
                "impact": impact,
            }

        if -buffer_after <= diff < 0:
            return {
                "safe": False,
                "score_adj": -10,
                "reason": f"{abs(diff):.0f}min after: {title} ({curr})",
                "next_news": title,
                "impact": impact,
            }

        if 0 < diff <= 120 and impact == "High":
            return {
                "safe": True,
                "score_adj": -2,
                "reason": f"News in {diff:.0f}min: {title}",
                "next_news": title,
                "impact": impact,
            }

    return {
        "safe": True,
        "score_adj": 0,
        "reason": "No high impact news",
        "next_news": "",
        "impact": "",
    }
