"""
plugin_news.py — Alpha Buffalo v5
Economic Calendar Filter (ForexFactory)
ฟรี 100% ไม่ต้อง API key

Logic:
- ดึง High Impact USD/XAU news
- ถ้ามีข่าวภายใน buffer → หยุดเทรด
- ส่ง score adjustment กลับ
"""

import requests
import json
from datetime import datetime, timezone, timedelta
from typing import Optional
import re

BKK = timezone(timedelta(hours=7))
UTC = timezone.utc

# Cache ไว้ไม่ต้องดึงบ่อย
_news_cache: list = []
_cache_time: datetime = None
CACHE_MINUTES = 60  # refresh ทุกชั่วโมง


def fetch_ff_calendar() -> list:
    """ดึง Economic Calendar จาก ForexFactory"""
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
                dt     = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
            except Exception:
                continue
            events.append({
                "title":    item.get("title", ""),
                "currency": item.get("currency", ""),
                "impact":   item.get("impact", ""),
                "datetime": dt,
            })

        _news_cache = events
        _cache_time = now
        return events

    except Exception as e:
        print(f"⚠️ News fetch error: {e}")
        return _news_cache


def check_news_filter(buffer_before: int = 30, buffer_after: int = 30) -> dict:
    """
    เช็คว่าตอนนี้ safe to trade ไหม

    Returns:
        {
            "safe": bool,
            "score_adj": int,   # ลบจาก score ถ้าไม่ safe
            "reason": str,
            "next_news": str,
        }
    """
    now    = datetime.now(UTC)
    events = fetch_ff_calendar()

    for event in events:
        dt      = event["datetime"]
        diff    = (dt - now).total_seconds() / 60  # นาที
        impact  = event["impact"]
        title   = event["title"]
        curr    = event["currency"]

        # ก่อนข่าว
        if 0 <= diff <= buffer_before:
            return {
                "safe":       False,
                "score_adj":  -10,  # บล็อกสัญญาณ
                "reason":     f"⚠️ High Impact ในอีก {diff:.0f}min: {title} ({curr})",
                "next_news":  title,
                "impact":     impact,
            }

        # หลังข่าว
        if -buffer_after <= diff < 0:
            return {
                "safe":       False,
                "score_adj":  -10,
                "reason":     f"⚠️ หลังข่าว {abs(diff):.0f}min: {title} ({curr})",
                "next_news":  title,
                "impact":     impact,
            }

        # ข่าวภายใน 2 ชั่วโมง = ลด score
        if 0 < diff <= 120 and impact == "High":
            return {
                "safe":       True,
                "score_adj":  -2,
                "reason":     f"📰 ข่าวใน {diff:.0f}min: {title}",
                "next_news":  title,
                "impact":     impact,
            }

    return {
        "safe":      True,
        "score_adj": 0,
        "reason":    "✅ No High Impact news",
        "next_news": "",
        "impact":    "",
    }
