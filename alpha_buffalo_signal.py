"""
alpha_buffalo_signal.py — Alpha Buffalo v11.2 (New V4 Hybrid)
- Uses  from signal_composer
- No‑None pipeline with DecisionValidator
"""
import os, logging, sys
from datetime import datetime, timezone
from typing import Optional
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import uvicorn

from scenario_scanner import scanner as scenario_scanner
from data_provider_twelvedata import fetch_twelvedata
from signal_composer import compose_signal

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
NOTIFY_IDS = [x.strip() for x in os.getenv("NOTIFY_IDS", "").split(",") if x.strip()]
TV_PASSPHRASE = os.getenv("TV_WEBHOOK_PASSPHRASE", "TV_SECRET_2026")
SIGNAL_THRESHOLD = int(os.getenv("SIGNAL_THRESHOLD", "4"))

telegram_broadcaster = None
if TELEGRAM_TOKEN:
    try:
        from telegram_broadcaster import TelegramBroadcaster
        telegram_broadcaster = TelegramBroadcaster(TELEGRAM_TOKEN, NOTIFY_IDS)
    except Exception as e:
        logger.error(f"Telegram init error: {e}")

class SimpleLicenseManager:
    def validate_key(self, key: str) -> bool:
        return key in os.getenv("VALID_LICENSES", "DEMO123").split(",")

license_manager = SimpleLicenseManager()

class CloudSignal(BaseModel):
    timestamp: str
    direction: str
    score: float
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

def broadcast_signal(text: str):
    if telegram_broadcaster:
        try:
            telegram_broadcaster.send_message_sync(text)
        except Exception as e:
            logger.error(f"Broadcast failed: {e}")

def format_signal_message(signal: CloudSignal) -> str:
    emoji = "🟢" if signal.direction == "BUY" else "🔴" if signal.direction == "SELL" else "⚪"
    v5_tag = " [V5]" if signal.is_v5 else ""
    msg = (
        f"{emoji} Alpha Buffalo{v5_tag}\n"
        f"📊 {signal.direction}\n"
        f"🎯 Score: {signal.score:.1f}\n"
        f"💰 Entry: {signal.entry:.2f}\n"
        f"📈 TP1: {signal.tp1:.2f}  TP2: {signal.tp2:.2f}\n"
        f"🛡️ SL: {signal.sl:.2f} (Visual: {signal.visual_sl:.2f})"
    )
    if signal.is_v5 and signal.v5_tp1:
        msg += f"\n🦋 V5 TP1: {signal.v5_tp1:.2f}  TP2: {signal.v5_tp2:.2f}"
    msg += f"\n⏱️ {signal.timestamp}"
    return msg

def fetch_market_data():
    try:
        df_15m = fetch_twelvedata('XAU/USD', '15min', 60)
        df_1h  = fetch_twelvedata('XAU/USD', '1h', 60)
        df_4h  = fetch_twelvedata('XAU/USD', '4h', 60)
        logger.info(f"Fetched 15m:{len(df_15m)} 1h:{len(df_1h)} 4h:{len(df_4h)}")
        return df_15m, df_1h, df_4h
    except Exception as e:
        logger.error(f"fetch_market_data failed: {e}")
        return None, None, None

# ── Decision Validator ─────────────────────────────────
def validate_decision(decision: dict) -> bool:
    if decision["direction"] != "SIGNAL":
        return False
    if None in (decision['entry_price'], decision['sl_price'], decision['tp1_price'], decision['tp2_price']):
        logger.error(f"Decision has None prices: {decision}")
        return False
    if decision['direction'] == "BUY":
        if decision['sl_price'] >= decision['entry_price']:
            logger.error(f"Invalid SL for BUY: SL={decision['sl_price']} >= Entry={decision['entry_price']}")
            return False
        if decision['tp1_price'] <= decision['entry_price']:
            logger.error(f"Invalid TP for BUY: TP={decision['tp1_price']} <= Entry={decision['entry_price']}")
            return False
    else:
        if decision['sl_price'] <= decision['entry_price']:
            logger.error(f"Invalid SL for SELL: SL={decision['sl_price']} <= Entry={decision['entry_price']}")
            return False
        if decision['tp1_price'] >= decision['entry_price']:
            logger.error(f"Invalid TP for SELL: TP={decision['tp1_price']} >= Entry={decision['entry_price']}")
            return False
    return True

app = FastAPI(title="Alpha Buffalo Signal Bot", version="11.2")

@app.get("/health")
async def health():
    return {"status": "alive", "version": "11.2", "timestamp": datetime.now(timezone.utc).isoformat()}

@app.get("/signal/latest")
async def latest_signal(key: str):
    if not license_manager.validate_key(key):
        return JSONResponse(status_code=403, content={"error": "Invalid license"})

    df_15m, df_1h, df_4h = fetch_market_data()
    if df_15m is None:
        return JSONResponse(status_code=500, content={"error": "Market data fetch failed"})

    try:
        blueprint = scenario_scanner.scan(df_4h, df_1h, df_15m)
    except Exception as e:
        logger.warning(f"Blueprint failed, continuing without: {e}")
        blueprint = None

    try:
        decision = compose_signal(df_4h, df_1h, df_15m)
    except Exception as e:
        logger.error(f"Signal engine error: {e}", exc_info=True)
        return JSONResponse(status_code=500, content={"error": "Signal engine failure"})

    if decision["direction"] != "SIGNAL":
        return {
            "status": decision["direction"],
            "score": decision['score'],
            "reason": decision['reason'],
            "debug": decision['debug']
        }

    # Validate before broadcasting
    if not validate_decision(decision):
        return JSONResponse(status_code=500, content={"error": "Decision validation failed"})

    # Build CloudSignal
    is_v5 = decision['signal_type'] == "V5_SNIPER"
    signal = CloudSignal(
        timestamp=datetime.now(timezone.utc).isoformat(),
        direction=decision['direction'],
        score=decision['score'],
        entry=safe_float(decision['entry_price']),
        tp1=safe_float(decision['tp1_price']),
        tp2=safe_float(decision['tp2_price']),
        sl=safe_float(decision['sl_price']),
        visual_sl=safe_float(decision['sl_price']),
        zone_valid=True,
        vsa_bias="NEUTRAL",
        is_v5=is_v5,
        v5_tp1=safe_float(decision['tp1_price']) if is_v5 else None,
        v5_tp2=safe_float(decision['tp2_price']) if is_v5 else None,
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
    logger.info(f"Starting Alpha Buffalo v11.2 on port {port}")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")

def safe_float(val):
    try:
        return float(val) if val is not None else 0.0
    except (ValueError, TypeError):
        return 0.0
