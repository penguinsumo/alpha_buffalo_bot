"""
alpha_buffalo_signal.py — Alpha Buffalo v5.4 (Production)
Updated: V4 + Sweep + Visual TP/SL + BKK Session
"""
import os, logging
from datetime import datetime, timezone
from typing import Optional, Dict, Any
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import uvicorn

# ── Engine bridge ──
from signal_engine import get_trade_signal

# ── Session Clock (BKK Time) ──
from session_clock import get_market_session_info

# ── Logging ──
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ── Env ──
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
NOTIFY_IDS = [x.strip() for x in os.getenv("NOTIFY_IDS", "").split(",") if x.strip()]
TV_PASSPHRASE = os.getenv("TV_WEBHOOK_PASSPHRASE", "TV_SECRET_2026")

# ── Telegram ──
telegram_broadcaster = None
if TELEGRAM_TOKEN:
    try:
        from telegram_broadcaster import TelegramBroadcaster
        telegram_broadcaster = TelegramBroadcaster(TELEGRAM_TOKEN, NOTIFY_IDS)
    except Exception as e:
        logger.error(f"Failed to init Telegram: {e}")

# ── License ──
class SimpleLicenseManager:
    def validate_key(self, key: str) -> bool:
        return key in os.getenv("VALID_LICENSES", "DEMO123").split(",")
    def check_and_increment_quota(self, key: str) -> bool:
        return True

license_manager = SimpleLicenseManager()

# ── Models ──
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
    turbo_boost: bool = False

class TVWebhookPayload(BaseModel):
    passphrase: str
    direction: Optional[str] = None
    price: Optional[float] = None
    timestamp: Optional[str] = None

# ── Helpers ──
def broadcast_signal(text: str) -> None:
    if telegram_broadcaster:
        try:
            telegram_broadcaster.send_message_sync(text)
        except Exception as e:
            logger.error(f"Failed to broadcast: {e}")

def format_signal_message(signal: CloudSignal) -> str:
    emoji = "🟢" if signal.direction == "BUY" else "🔴" if signal.direction == "SELL" else "⚪"
    v5_tag = " [V5]" if signal.is_v5 else ""
    turbo_tag = " 🚀TURBO" if signal.turbo_boost else ""
    msg = (
        f"{emoji} Alpha Buffalo{v5_tag}{turbo_tag}\n"
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

# ── Data Fetching (ใช้ Twelve Data จริง) ──
def fetch_market_data():
    from data_provider_twelvedata import fetch_market_data as fetch_twelve
    return fetch_twelve("XAUUSD")

# ── FastAPI ──
app = FastAPI(title="Alpha Buffalo Signal Bot", version="5.4")

@app.get("/health")
async def health():
    return {"status": "alive", "version": "5.4", "timestamp": datetime.now(timezone.utc).isoformat()}

@app.get("/signal/latest")
async def latest_signal(key: str):
    if not license_manager.validate_key(key):
        return JSONResponse(status_code=403, content={"error": "Invalid license"})

    # 1. Fetch data
    try:
        df_15m, df_1h, df_4h = fetch_market_data()
    except NotImplementedError:
        logger.warning("fetch_market_data() not implemented – returning dummy no signal")
        return {"status": "NO_SIGNAL", "score": 0, "reason": "Data source not configured"}
    except Exception as e:
        logger.error(f"Failed to fetch market data: {e}")
        return JSONResponse(status_code=500, content={"error": "Market data fetch failed"})

    if df_15m is None or df_1h is None or df_4h is None:
        return {"status": "NO_SIGNAL", "score": 0, "reason": "Incomplete data"}

    # 2. Session check
    session_info = get_market_session_info()
    if session_info['session'] == 'CLOSED':
        return {"status": "NO_SIGNAL", "score": 0, "reason": f"Market closed ({session_info['bkk_time']})"}

    # 3. Get signal from engine
    try:
        trade_signal = get_trade_signal(df_15m, df_1h, df_4h)
    except Exception as e:
        logger.error(f"Signal engine error: {e}", exc_info=True)
        return JSONResponse(status_code=500, content={"error": "Signal engine failure"})

    if trade_signal is None:
        return {"status": "NO_SIGNAL", "score": 0}

    # 4. Build CloudSignal
    signal = CloudSignal(
        timestamp=datetime.now(timezone.utc).isoformat(),
        direction=trade_signal["direction"],
        score=trade_signal["score"],
        entry=trade_signal["entry"],
        tp1=trade_signal["tp1"],
        tp2=trade_signal["tp2"],
        sl=trade_signal["sl"],
        visual_sl=trade_signal.get("visual_sl", trade_signal["sl"]),
        zone_valid=True,
        vsa_bias="NEUTRAL",
        is_v5=trade_signal.get("signal_type") == "V5_SNIPER",
        v5_tp1=trade_signal.get("v5_tp1"),
        v5_tp2=trade_signal.get("v5_tp2"),
        turbo_boost=trade_signal.get("turbo_boost", False),
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

if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    logger.info(f"Starting Alpha Buffalo v5.4 on port {port}")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
