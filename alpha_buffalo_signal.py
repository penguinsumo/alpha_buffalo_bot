import os
import logging
from datetime import datetime
from typing import Optional, Dict, Any, List
from fastapi import FastAPI, HTTPException, Request, Header
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import uvicorn

# ประกาศ logger ก่อนใช้
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ------------------------- โหลด environment -------------------------
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
NOTIFY_IDS = [x for x in os.getenv("NOTIFY_IDS", "").split(",") if x]
SIGNAL_THRESHOLD = int(os.getenv("SIGNAL_THRESHOLD", "4"))

# ------------------------- ตรวจสอบ Telegram (ถ้ามี token) -------------------------
telegram_broadcaster = None
if TELEGRAM_TOKEN:
    try:
        from telegram_broadcaster import TelegramBroadcaster
        telegram_broadcaster = TelegramBroadcaster(TELEGRAM_TOKEN, NOTIFY_IDS)
        logger.info("TelegramBroadcaster initialized")
    except Exception as e:
        logger.error(f"Failed to init Telegram: {e}")
else:
    logger.warning("TELEGRAM_TOKEN missing, Telegram disabled")

# ------------------------- License manager แบบง่าย (mock) -------------------------
class SimpleLicenseManager:
    def validate_key(self, key: str) -> bool:
        return key in os.getenv("VALID_LICENSES", "DEMO123").split(",")
    def check_and_increment_quota(self, key: str) -> bool:
        return True

license_manager = SimpleLicenseManager()

# ------------------------- Models -------------------------
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
    reentry_ok: bool = False
    next_pattern: Optional[str] = None
    d_point: Optional[float] = None

class TVWebhookPayload(BaseModel):
    passphrase: str
    direction: Optional[str] = None
    price: Optional[float] = None
    timestamp: Optional[str] = None

class ReentryRequest(BaseModel):
    key: str
    order_ticket: int
    symbol: str = "XAUUSD"

# ------------------------- Helper functions -------------------------
def broadcast_signal(text: str) -> None:
    if telegram_broadcaster:
        telegram_broadcaster.send_message_sync(text)

def format_signal_message(signal: CloudSignal) -> str:
    emoji = "🟢" if signal.direction == "BUY" else "🔴" if signal.direction == "SELL" else "⚪"
    v5_tag = " [V5]" if signal.is_v5 else ""
    msg = f"{emoji} Alpha Buffalo{v5_tag}\n📊 {signal.direction}\n🎯 Score: {signal.score}\n💰 Entry: {signal.entry:.2f}\n📈 TP1: {signal.tp1:.2f}  TP2: {signal.tp2:.2f}\n🛡️ SL: {signal.sl:.2f} (Visual: {signal.visual_sl:.2f})"
    if signal.vsa_bias != "NEUTRAL":
        msg += f"\n💧 VSA: {signal.vsa_bias}"
    if signal.is_v5 and signal.v5_tp1:
        msg += f"\n🦋 V5 TP1: {signal.v5_tp1:.2f}  TP2: {signal.v5_tp2:.2f}"
    msg += f"\n⏱️ {signal.timestamp}"
    return msg

def get_dummy_signal() -> CloudSignal:
    """ตัวอย่าง signal สำหรับทดสอบ (ส่ง BUY ทุกครั้ง)"""
    return CloudSignal(
        timestamp=datetime.utcnow().isoformat(),
        direction="BUY",
        score=8,
        entry=2350.50,
        tp1=2360.00,
        tp2=2375.00,
        sl=2345.00,
        visual_sl=2348.00,
        vsa_bias="BULLISH",
        is_v5=True,
        v5_tp1=2380.00,
        v5_tp2=2400.00,
    )

def get_no_signal() -> CloudSignal:
    return CloudSignal(
        timestamp=datetime.utcnow().isoformat(),
        direction="NO_SIGNAL",
        score=0,
        entry=0.0,
        tp1=0.0,
        tp2=0.0,
        sl=0.0,
        visual_sl=0.0,
    )

# ------------------------- FastAPI -------------------------
app = FastAPI(title="Alpha Buffalo Signal Bot", version="5.2")

@app.get("/health")
async def health():
    return {"status": "alive", "version": "5.2", "timestamp": datetime.utcnow().isoformat()}

@app.get("/signal/latest")
async def latest_signal(key: str):
    if not license_manager.validate_key(key):
        return JSONResponse(status_code=403, content={"error": "Invalid license"})
    if not license_manager.check_and_increment_quota(key):
        return JSONResponse(status_code=429, content={"error": "Quota exceeded"})

    # TODO: เปลี่ยนจาก dummy signal เป็นของจริงเมื่อ signal_engine พร้อม
    signal = get_dummy_signal()  # หรือ get_no_signal() ถ้าต้องการ no signal

    if signal.direction == "NO_SIGNAL" or signal.score < SIGNAL_THRESHOLD:
        return {"status": "NO_SIGNAL", "score": signal.score}

    broadcast_signal(format_signal_message(signal))
    return signal.dict()

@app.get("/signal/history")
async def history(key: str, limit: int = 50):
    if not license_manager.validate_key(key):
        return JSONResponse(403, {"error": "Invalid license"})
    return {"history": [], "message": "Not implemented"}

@app.post("/signal/zone_check")
async def zone_check(key: str, order_ticket: int, symbol: str = "XAUUSD"):
    if not license_manager.validate_key(key):
        return JSONResponse(403, {"error": "Invalid license"})
    return {"zone_valid": True, "vsa_ok": True}

@app.post("/signal/reentry")
async def reentry(req: ReentryRequest):
    if not license_manager.validate_key(req.key):
        return JSONResponse(403, {"error": "Invalid license"})
    return {"allowed": False, "reason": "Not supported"}

@app.post("/webhook/tv")
async def tv_webhook(payload: TVWebhookPayload):
    if payload.passphrase != os.getenv("TV_WEBHOOK_PASSPHRASE", "TV_SECRET_2026"):
        raise HTTPException(401, "Invalid passphrase")
    msg = f"📡 TV Alert\nDirection: {payload.direction}\nPrice: {payload.price}\nTime: {payload.timestamp or datetime.utcnow().isoformat()}"
    broadcast_signal(msg)
    return {"status": "ok"}

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
