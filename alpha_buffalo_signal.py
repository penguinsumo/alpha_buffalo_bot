"""
alpha_buffalo_signal.py — Alpha Buffalo v5.3 (Production)
- Integrated with signal_engine (Bridge to Composer)
- Robust error handling, data fetching placeholder, and FastAPI serving
"""

import os
import logging
from datetime import datetime, timezone
from typing import Optional, Dict, Any
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import uvicorn

# ── Engine bridge (v5.3) ──────────────────────────────
from signal_engine import get_trade_signal

# ── Logging ───────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ── Environment Variables ────────────────────────────
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
NOTIFY_IDS = [x.strip() for x in os.getenv("NOTIFY_IDS", "").split(",") if x.strip()]
SIGNAL_THRESHOLD = int(os.getenv("SIGNAL_THRESHOLD", "4"))   # Additional filter (optional)
TV_PASSPHRASE = os.getenv("TV_WEBHOOK_PASSPHRASE", "TV_SECRET_2026")

# ── Telegram Broadcaster (optional) ──────────────────
telegram_broadcaster = None
if TELEGRAM_TOKEN:
    try:
        from telegram_broadcaster import TelegramBroadcaster
        telegram_broadcaster = TelegramBroadcaster(TELEGRAM_TOKEN, NOTIFY_IDS)
        logger.info("TelegramBroadcaster initialized")
    except Exception as e:
        logger.error(f"Failed to init Telegram: {e}")
else:
    logger.warning("TELEGRAM_TOKEN missing – Telegram notifications disabled")

# ── License Manager (simple mock) ────────────────────
class SimpleLicenseManager:
    def validate_key(self, key: str) -> bool:
        return key in os.getenv("VALID_LICENSES", "DEMO123").split(",")
    def check_and_increment_quota(self, key: str) -> bool:
        return True

license_manager = SimpleLicenseManager()

# ── Data Models ──────────────────────────────────────
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

class TVWebhookPayload(BaseModel):
    passphrase: str
    direction: Optional[str] = None
    price: Optional[float] = None
    timestamp: Optional[str] = None

# ── Helper Functions ─────────────────────────────────
def broadcast_signal(text: str) -> None:
    if telegram_broadcaster:
        try:
            telegram_broadcaster.send_message_sync(text)
        except Exception as e:
            logger.error(f"Failed to broadcast: {e}")

def format_signal_message(signal: CloudSignal) -> str:
    emoji = "🟢" if signal.direction == "BUY" else "🔴" if signal.direction == "SELL" else "⚪"
    v5_tag = " [V5]" if signal.is_v5 else ""
    msg = (
        f"{emoji} Alpha Buffalo{v5_tag}\n"
        f"📊 {signal.direction}\n"
        f"🎯 Score: {signal.score}\n"
        f"💰 Entry: {signal.entry:.2f}\n"
        f"📈 TP1: {signal.tp1:.2f}  TP2: {signal.tp2:.2f}\n"
        f"🛡️ SL: {signal.sl:.2f} (Visual: {signal.visual_sl:.2f})"
    )
    if signal.is_v5 and signal.v5_tp1:
        msg += f"\n🦋 V5 TP1: {signal.v5_tp1:.2f}  TP2: {signal.v5_tp2:.2f}"
    msg += f"\n⏱️ {signal.timestamp}"
    return msg

# ── Data Fetching (Placeholder – implement real source) ──
def fetch_market_data():
    """
    ดึงข้อมูลตลาดจาก MT5, CSV, API หรือแหล่งอื่น ๆ
    คืน df_15m, df_1h, df_4h (pandas DataFrame)
    ถ้าดึงไม่ได้ให้ raise exception หรือคืน (None, None, None)
    """
    # TODO: ใส่ logic ดึงข้อมูลจริงของคุณที่นี่
    # ตัวอย่าง: ใช้ MetaTrader5, yfinance, ccxt หรือ pandas.read_csv(...)
    # ขณะนี้จะ raise NotImplementedError เพื่อให้คุณแทนที่ได้
    raise NotImplementedError("Please implement fetch_market_data() with real data source")
    # return df_15m, df_1h, df_4h

# ── FastAPI App ─────────────────────────────────────
app = FastAPI(title="Alpha Buffalo Signal Bot", version="5.3")

@app.get("/health")
async def health():
    return {"status": "alive", "version": "5.3", "timestamp": datetime.now(timezone.utc).isoformat()}

@app.get("/signal/latest")
async def latest_signal(key: str):
    """คืนสัญญาณล่าสุดที่ผ่าน Composer v5.3"""
    if not license_manager.validate_key(key):
        return JSONResponse(status_code=403, content={"error": "Invalid license"})

    # 1. ดึงข้อมูลตลาด (พร้อม fallback)
    try:
        df_15m, df_1h, df_4h = fetch_market_data()
    except NotImplementedError:
        logger.warning("fetch_market_data() not implemented – returning dummy no signal")
        return {"status": "NO_SIGNAL", "score": 0, "reason": "Data source not configured"}
    except Exception as e:
        logger.error(f"Failed to fetch market data: {e}")
        return JSONResponse(status_code=500, content={"error": "Market data fetch failed"})

    if df_15m is None or df_1h is None or df_4h is None:
        logger.warning("One or more DataFrames are None – returning NO_SIGNAL")
        return {"status": "NO_SIGNAL", "score": 0, "reason": "Incomplete data"}

    # 2. เรียก Bridge (Composer v5.3) พร้อม try-except
    try:
        trade_signal = get_trade_signal(df_15m, df_1h, df_4h)
    except Exception as e:
        logger.error(f"Signal engine error: {e}", exc_info=True)
        return JSONResponse(status_code=500, content={"error": "Signal engine failure"})

    # Bridge จะคืน None หากไม่ผ่าน Gate ใด ๆ
    if trade_signal is None:
        return {"status": "NO_SIGNAL", "score": 0}

    # Optional: ใช้ SIGNAL_THRESHOLD เพิ่มอีกชั้น (redundant but safe)
    if trade_signal["score"] < SIGNAL_THRESHOLD:
        return {"status": "NO_SIGNAL", "score": trade_signal["score"], "reason": "Below threshold"}

    # 3. สร้าง Model และ broadcast
    is_v5 = trade_signal.get("signal_type") == "V5_SNIPER"
    signal = CloudSignal(
        timestamp=datetime.now(timezone.utc).isoformat(),
        direction=trade_signal["direction"],
        score=trade_signal["score"],
        entry=trade_signal["entry"],
        tp1=trade_signal["tp1"],
        tp2=trade_signal["tp2"],
        sl=trade_signal["sl"],
        visual_sl=trade_signal["sl"],   # ใช้ sl เดียวกัน (หรือปรับตาม visual_sl ที่มี)
        zone_valid=True,
        vsa_bias="NEUTRAL",
        is_v5=is_v5,
        v5_tp1=trade_signal["tp1"] if is_v5 else None,
        v5_tp2=trade_signal["tp2"] if is_v5 else None,
    )

    broadcast_signal(format_signal_message(signal))
    return signal.dict()

@app.post("/webhook/tv")
async def tv_webhook(payload: TVWebhookPayload):
    if payload.passphrase != TV_PASSPHRASE:
        raise HTTPException(status_code=401, detail="Invalid passphrase")
    msg = f"📡 TV Alert\nDirection: {payload.direction}\nPrice: {payload.price}"
    broadcast_signal(msg)
    return {"status": "ok"}

# ── Main ───────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    logger.info(f"Starting Alpha Buffalo v5.3 on port {port}")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")

# v10 INTEGRATION
import sys
sys.path.insert(0, 'v10_modules')
try:
    from v10_modules.config import CONFIG as V10_CONFIG
    from v10_modules.layer9_adaptive import AdaptiveEngine
    from v10_modules.layer5_position_sizer import PositionSizer
    V10_READY = True
except ImportError:
    V10_READY = False
