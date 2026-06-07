# alpha_buffalo_signal.py
import os
import logging
import json
from datetime import datetime
from typing import Optional, Dict, Any
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import aiohttp
import uvicorn

# โมดูลภายในระบบ
from signal_engine import SignalEngine
from score_manager import ScoreManager
from vsa_gate import VSAGate
from license_manager import LicenseManager
from telegram_broadcaster import TelegramBroadcaster

# ------------------------- Config -------------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Environment variables
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
NOTIFY_IDS = os.getenv("NOTIFY_IDS", "").split(",")
VALID_LICENSES = os.getenv("VALID_LICENSES", "").split(",")
TWELVE_API_KEY = os.getenv("TWELVE_API_KEY")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
TRADE_SYMBOL = os.getenv("TRADE_SYMBOL", "XAUUSD")
TV_WEBHOOK_PASSPHRASE = os.getenv("TV_WEBHOOK_PASSPHRASE", "TV_SECRET_2026")

# ------------------------- Global Instances -------------------------
telegram_broadcaster = TelegramBroadcaster(TELEGRAM_TOKEN, NOTIFY_IDS)
license_manager = LicenseManager(supabase_url=SUPABASE_URL, supabase_key=SUPABASE_KEY)
signal_engine = SignalEngine()
score_manager = ScoreManager()
vsa_gate = VSAGate()

# ------------------------- Pydantic Models -------------------------
class CloudSignal(BaseModel):
    timestamp: str
    direction: str  # BUY / SELL / NO_SIGNAL
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
    symbol: str = TRADE_SYMBOL

# ------------------------- Helper Functions -------------------------
def broadcast_signal(signal_text: str):
    """ส่งข้อความไปยัง Telegram โดยใช้ TelegramBroadcaster (sync)"""
    if not telegram_broadcaster:
        logger.error("TelegramBroadcaster not initialized")
        return
    telegram_broadcaster.send_message_sync(signal_text)

def format_signal_message(signal: CloudSignal) -> str:
    """จัดรูปแบบข้อความให้เหมือนเดิม (ไม่ให้กระทบลูกค้า)"""
    emoji = "🟢" if signal.direction == "BUY" else "🔴" if signal.direction == "SELL" else "⚪"
    v5_tag = " [V5]" if signal.is_v5 else ""
    msg = f"{emoji} Alpha Buffalo{v5_tag}\n"
    msg += f"📊 {signal.direction}\n"
    msg += f"🎯 Score: {signal.score}\n"
    msg += f"💰 Entry: {signal.entry:.2f}\n"
    msg += f"📈 TP1: {signal.tp1:.2f}  TP2: {signal.tp2:.2f}\n"
    msg += f"🛡️ SL: {signal.sl:.2f} (Visual: {signal.visual_sl:.2f})\n"
    if signal.vsa_bias != "NEUTRAL":
        msg += f"💧 VSA: {signal.vsa_bias}\n"
    if signal.is_v5 and signal.v5_tp1:
        msg += f"🦋 V5 TP1: {signal.v5_tp1:.2f}  TP2: {signal.v5_tp2:.2f}\n"
    msg += f"⏱️ {signal.timestamp}"
    return msg

# ------------------------- FastAPI Lifespan -------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Alpha Buffalo v5.2 starting...")
    # ตรวจสอบการเชื่อมต่อ APIs (optional)
    yield
    # Shutdown
    logger.info("Shutting down...")

app = FastAPI(title="Alpha Buffalo Signal Bot", version="5.2", lifespan=lifespan)

# ------------------------- Endpoints -------------------------
@app.get("/health")
async def health_check():
    return {"status": "alive", "version": "5.2", "timestamp": datetime.utcnow().isoformat()}

@app.get("/signal/latest")
async def get_latest_signal(key: str):
    """EA เรียก endpoint นี้ทุก 2-3 วินาที เพื่อรับสัญญาณล่าสุด"""
    # ตรวจสอบ license
    if not license_manager.validate_key(key):
        return JSONResponse(status_code=403, content={"error": "Invalid or expired license key"})
    
    # เช็ค quota (สำหรับ TRIAL/BASIC)
    if not license_manager.check_quota(key):
        return JSONResponse(status_code=429, content={"error": "Daily quota exceeded"})
    
    # ดึง OHLCV ล่าสุด (ใช้ TwelveData หรือ cache)
    ohlcv = signal_engine.fetch_ohlcv(symbol=TRADE_SYMBOL)
    if not ohlcv:
        return JSONResponse(status_code=503, content={"error": "Cannot fetch market data"})
    
    # คำนวณสัญญาณ
    signal = signal_engine.calculate_signal(ohlcv)
    
    # ถ้าเป็น NO_SIGNAL หรือ score ไม่ถึง threshold -> ไม่ส่ง signal
    if signal.direction == "NO_SIGNAL" or signal.score < 4:
        return {"status": "NO_SIGNAL", "score": signal.score}
    
    # บันทึกการใช้ quota
    license_manager.increment_usage(key)
    
    # บันทึก signal ลงฐานข้อมูล (Supabase)
    try:
        # TODO: insert into signals table
        pass
    except Exception as e:
        logger.error(f"Failed to save signal: {e}")
    
    # Broadcast ไปยัง Telegram (เฉพาะ signal ที่ผ่าน threshold)
    if signal.score >= 4:
        msg = format_signal_message(signal)
        broadcast_signal(msg)
    
    # ส่งกลับไปยัง EA
    return signal.dict()

@app.get("/signal/history")
async def get_signal_history(key: str, limit: int = 50):
    """ดูประวัติสัญญาณ (สำหรับ debug)"""
    if not license_manager.validate_key(key):
        return JSONResponse(status_code=403, content={"error": "Invalid license"})
    # TODO: ดึงจาก Supabase
    return {"history": [], "message": "Not implemented yet"}

@app.post("/signal/zone_check")
async def check_zone_validity(key: str, order_ticket: int, symbol: str = TRADE_SYMBOL):
    """EA เรียกเพื่อตรวจสอบว่า zone ยัง valid หรือไม่ (ใช้สำหรับ reentry)"""
    if not license_manager.validate_key(key):
        return JSONResponse(status_code=403, content={"error": "Invalid license"})
    
    # ดึงข้อมูล zone ปัจจุบัน (mock)
    # จริง ๆ ต้องใช้ signal_engine หรือ cache
    ohlcv = signal_engine.fetch_ohlcv(symbol)
    zone_valid = signal_engine.is_zone_still_valid(ohlcv)
    vsa_ok = vsa_gate.check_reentry_ok(ohlcv)
    
    return {"zone_valid": zone_valid, "vsa_ok": vsa_ok}

@app.post("/signal/reentry")
async def reentry_order(req: ReentryRequest):
    """ให้ EA ขอเปิดออเดอร์ใหม่หลังจาก visual SL ถูก hit"""
    if not license_manager.validate_key(req.key):
        return JSONResponse(status_code=403, content={"error": "Invalid license"})
    
    # ตรวจสอบ zone + VSA อีกครั้ง
    ohlcv = signal_engine.fetch_ohlcv(req.symbol)
    if not signal_engine.is_zone_still_valid(ohlcv):
        return {"allowed": False, "reason": "Zone invalid"}
    if not vsa_gate.check_reentry_ok(ohlcv):
        return {"allowed": False, "reason": "VSA not ok"}
    
    # สร้างสัญญาณใหม่ (อาจใช้ cached signal)
    new_signal = signal_engine.calculate_signal(ohlcv)
    if new_signal.direction == "NO_SIGNAL":
        return {"allowed": False, "reason": "No signal"}
    
    # บันทึกการใช้ quota อีกครั้ง (optional)
    license_manager.increment_usage(req.key)
    
    return {"allowed": True, "signal": new_signal.dict()}

@app.post("/webhook/tv")
async def tradingview_webhook(payload: TVWebhookPayload):
    """รับสัญญาณจาก TradingView Alert (Forward Test)"""
    if payload.passphrase != TV_WEBHOOK_PASSPHRASE:
        raise HTTPException(status_code=401, detail="Invalid passphrase")
    
    # สร้างข้อความแจ้งเตือน
    msg = f"📡 TradingView Alert\n"
    msg += f"Direction: {payload.direction}\n"
    msg += f"Price: {payload.price}\n"
    msg += f"Time: {payload.timestamp or datetime.utcnow().isoformat()}"
    broadcast_signal(msg)
    
    return {"status": "ok", "received": True}

# ------------------------- Main -------------------------
if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)