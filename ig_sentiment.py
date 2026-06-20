"""
ig_sentiment.py — Alpha Buffalo v5.4
IG Client Sentiment Fetcher (Contrarian Indicator)
"""

import os
import logging
import requests
from typing import Optional, Dict

logger = logging.getLogger(__name__)

class IGSentiment:
    def __init__(self):
        self.api_key = os.getenv("IG_API_KEY")
        self.base_url = "https://api.ig.com/gateway/deal/demos/markets"
        self.epic = "CS.D.XAUUSD.CFD.IP"  # XAUUSD epic on IG

    def fetch(self) -> Optional[Dict]:
        """ดึงข้อมูล Sentiment จาก IG"""
        if not self.api_key:
            logger.warning("IG_API_KEY not set")
            return None

        headers = {
            "X-IG-API-KEY": self.api_key,
            "Accept": "application/json"
        }
        try:
            resp = requests.get(f"{self.base_url}/{self.epic}", headers=headers, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            snapshot = data.get("snapshot", {})
            return {
                "long_pct": snapshot.get("longPositionPercentage", 50),
                "short_pct": snapshot.get("shortPositionPercentage", 50),
                "bias": snapshot.get("longPositionPercentage", 50) - snapshot.get("shortPositionPercentage", 50)
            }
        except Exception as e:
            logger.error(f"IG sentiment fetch error: {e}")
            return None

    def get_sentiment_score(self, direction: str) -> float:
        """
        คำนวณ Sentiment Score สำหรับ ScoreManager
        direction: "BUY" หรือ "SELL"
        """
        data = self.fetch()
        if data is None:
            return 0.0

        bias = data["bias"]  # positive = lots of longs, negative = lots of shorts
        if direction == "BUY" and bias < -15:
            return 2.0  # Everyone is short → contrarian buy signal
        elif direction == "SELL" and bias > 15:
            return -2.0  # Everyone is long → contrarian sell signal
        return 0.0


# Singleton
ig_sentiment = IGSentiment()
