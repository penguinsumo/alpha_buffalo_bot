"""
alpha_buffalo_signal.py - Alpha Buffalo v5.3 (Mac Server Production)
- Uses signal_engine (Bridge) to connect real Composer
- Supports .env for environment variables
- Ready for health check, polling signal, and fallback
"""

import os
import sys
import logging
from datetime import datetime, timezone
from typing import Optional, Dict, Any
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import uvicorn

# Load environment variables from .env (if exists)
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Import Engine Bridge (v5.3)
from signal_engine import get_trade_signal

# Environment Variables
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
NOTIFY_IDS = [x.strip() for x in os.getenv("NOTIFY_IDS", "").split(",") if x.strip()]
SIGNAL_THRESHOLD = int(os.getenv("SIGNAL_THRESHOLD", "4"))
TV_PASSPHRASE = os.getenv("TV_WEBHOOK_PASSPHRASE", "TV_SECRET_2026")
VALID_LICENSES = os.getenv("VALID_LICENSES", "DEMO123")

# Telegram Broadcaster (Optional)
telegram_broadcaster = None
if TELEGRAM_TOKEN:
    try:
        from telegram_broadcaster import TelegramBroadcaster
        telegram_broadcaster = TelegramBroadcaster(TELEGRAM_TOKEN, NOTIFY_IDS)
        logger.info("TelegramBroadcaster initialized")
    except Exception as e:
        logger.error(f"Failed to init Telegram: {e}")
else:
    logger.warning("TELEGRAM_TOKEN not set - Telegram disabled")

# License Manager (Mock)
class SimpleLicenseManager:
    def validate_key(self, key: str) -> bool:
        return key in VALID_LICENSES.split(",")
    def check_and_increment_quota(self, key: str) -> bool:
        return True

license_manager = SimpleLicenseManager()

# Data Models
class CloudSignal(BaseModel):
    timestamp: str
    direction: str
    score: int
    entry: float
    tp1: float
    tp2: float
    sl: float
    visual_sl: float
    zone_valid: bool = True
    vsa_bias: str = "NEUTRAL"
    is_v5: bool = False
    v5_tp1: Optional[float] = None
    v5_tp2: Optional[float] = None

# Helper Functions
def broadcast_signal(text: str) -> None:
    if telegram_broadcaster:
        try:
            telegram_broadcaster.send_message_sync(text)
        except Exception as e:
            logger.error(f"Broadcast failed: {e}")

def format_signal_message(signal: CloudSignal) -> str:
    emoji = "BUY" if signal.direction == "BUY" else ("SELL" if signal.direction == "SELL" else "NO")
    v5_tag = " [V5]" if signal.is_v5 else ""
    msg = (f"{emoji} Alpha Buffalo{v5_tag}\n"
           f"Direction: {signal.direction}\nScore: {signal.score}\n"
           f"Entry: {signal.entry:.2f}\nTP1: {signal.tp1:.2f}  TP2: {signal.tp2:.2f}\n"
           f"SL: {signal.sl:.2f} (Visual: {signal.visual_sl:.2f})")
    if signal.is_v5 and signal.v5_tp1:
        msg += f"\nV5 TP1: {signal.v5_tp1:.2f}  TP2: {signal.v5_tp2:.2f}"
    msg += f"\nTimestamp: {signal.timestamp}"
    return msg

# Market Data Fetcher (PLACEHOLDER)
def fetch_market_data():
    """
    Fetch OHLCV data from Twelve Data (or other provider).
    Returns tuple of DataFrames (df_15m, df_1h, df_4h) or None.
    """
    try:
        from data_provider_twelvedata import fetch_market_data as td_fetch
        return td_fetch("XAUUSD")
    except ImportError:
        logger.error("data_provider_twelvedata.py not found or TWELVEDATA_API_KEY missing")
        return None, None, None

# FastAPI App
app = FastAPI(title="Alpha Buffalo Signal Bot", version="5.3")

@app.get("/health")
async def health():
    return {"status": "alive", "version": "5.3", "timestamp": datetime.now(timezone.utc).isoformat()}

@app.get("/signal/latest")
async def latest_signal(key: str):
    if not license_manager.validate_key(key):
        return JSONResponse(status_code=403, content={"error": "Invalid license"})

    # 1. Fetch market data
    try:
        df_15m, df_1h, df_4h = fetch_market_data()
    except Exception as e:
        logger.error(f"Data fetch error: {e}")
        return JSONResponse(status_code=500, content={"error": "Market data fetch failed"})

    if df_15m is None or df_1h is None or df_4h is None:
        return {"status": "NO_SIGNAL", "score": 0, "reason": "Data not available"}

    # 2. Call Bridge (Composer v5.3)
    try:
        trade_signal = get_trade_signal(df_15m, df_1h, df_4h)
    except Exception as e:
        logger.error(f"Signal engine error: {e}", exc_info=True)
        return JSONResponse(status_code=500, content={"error": "Signal engine failure"})

    if trade_signal is None:
        return {"status": "NO_SIGNAL", "score": 0}

    # 3. Optional threshold check
    if trade_signal["score"] < SIGNAL_THRESHOLD:
        return {"status": "NO_SIGNAL", "score": trade_signal["score"]}

    # 4. Build signal object and broadcast
    is_v5 = trade_signal.get("signal_type") == "V5_SNIPER"
    signal = CloudSignal(
        timestamp=datetime.now(timezone.utc).isoformat(),
        direction=trade_signal["direction"],
        score=trade_signal["score"],
        entry=trade_signal["entry"],
        tp1=trade_signal["tp1"],
        tp2=trade_signal["tp2"],
        sl=trade_signal["sl"],
        visual_sl=trade_signal["sl"],
        vsa_bias="BULLISH" if trade_signal["score"] > 5 else "NEUTRAL",
        is_v5=is_v5,
        v5_tp1=trade_signal["tp1"] if is_v5 else None,
        v5_tp2=trade_signal["tp2"] if is_v5 else None,
    )

    broadcast_signal(format_signal_message(signal))
    logger.info(f"Signal sent: {signal.direction} | Score: {signal.score}")
    return signal.dict()

if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    logger.info(f"Starting Alpha Buffalo v5.3 on port {port}")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
