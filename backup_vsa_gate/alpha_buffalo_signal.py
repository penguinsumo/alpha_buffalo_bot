"""
alpha_buffalo_signal.py - Alpha Buffalo v5.3 (Twelve Data, XAU/USD)
Hybrid SL, Trend Scheduler, Telegram
"""
import os, json, pathlib, logging, datetime as dt
from typing import Optional, Dict, Any
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import uvicorn
from apscheduler.schedulers.background import BackgroundScheduler
from zoneinfo import ZoneInfo

load_dotenv()
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

from signal_engine import get_trade_signal
from trade_manager import trade_manager as buy_engine
from trade_manager import trade_manager_sell as sell_engine
from session_clock import get_market_session_info

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
NOTIFY_IDS = [x.strip() for x in os.getenv("NOTIFY_IDS", "").split(",") if x.strip()]
SIGNAL_THRESHOLD = int(os.getenv("SIGNAL_THRESHOLD", "4"))
VALID_LICENSES = os.getenv("VALID_LICENSES", "DEMO123")
HARD_SL_RISK_MULTIPLIER = float(os.getenv("HARD_SL_RISK_MULTIPLIER", "2.0"))

SIGNAL_FILE = pathlib.Path.home() / "alpha_buffalo_bot" / "AlphaSignal.json"

telegram_broadcaster = None
if TELEGRAM_TOKEN:
    try:
        from telegram_broadcaster import TelegramBroadcaster
        telegram_broadcaster = TelegramBroadcaster()
        logger.info("TelegramBroadcaster initialized")
    except Exception as e:
        logger.error(f"Failed to init Telegram: {e}")

from license_manager import get_license_manager
license_manager = get_license_manager()

class CloudSignal(BaseModel):
    timestamp: str
    direction: str
    score: int
    entry: float
    tp1: float
    tp2: float
    sl: float
    hard_sl: float
    visual_sl: float
    zone_valid: bool = True
    vsa_bias: str = "NEUTRAL"
    is_v5: bool = False
    v5_tp1: Optional[float] = None
    v5_tp2: Optional[float] = None
    session: str = "UNKNOWN"
    signal_type: str = "V4_SESSION"

def broadcast_signal(text: str) -> None:
    if telegram_broadcaster:
        try:
            telegram_broadcaster.send_message_sync(text)
        except Exception as e:
            logger.error(f"Broadcast failed: {e}")

def format_signal_message(signal: CloudSignal) -> str:
    bkk_now = dt.datetime.now(ZoneInfo("Asia/Bangkok"))
    date_str = bkk_now.strftime('%a %d %b %Y')
    time_str = bkk_now.strftime('%H:%M')
    sl_low = min(signal.sl, signal.hard_sl)
    sl_high = max(signal.sl, signal.hard_sl)
    sl_zone = f"{sl_low:.1f} - {sl_high:.1f}"
    msg = (
        "🐃 ALPHA BUFFALO V5\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        f"📌 Asset    : XAUUSD\n"
        f"📊 Type     : {signal.signal_type}\n"
        f"🎯 Entry    : ~{signal.entry:,.2f}\n"
        f"🛡️ SL Zone  : {sl_zone}\n"
        f"🎯 TP1      : {signal.tp1:,.2f}  (M15 ~30min)\n"
        f"🎯 TP2      : {signal.tp2:,.2f}  (H1  ~2hr)\n"
        f"⏰ {date_str} | {time_str}\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "✅ EA Executing\n\n"
        "⚠️ Not financial advice. Trade at your own risk."
    )
    return msg

def write_signal_to_file(signal_dict: Dict[str, Any]) -> None:
    try:
        with open(SIGNAL_FILE, "w") as f:
            json.dump(signal_dict, f, indent=2)
        logger.info(f"Signal written to {SIGNAL_FILE}")
    except Exception as e:
        logger.error(f"Failed to write signal file: {e}")

def compute_hard_sl(direction: str, entry: float, tight_sl: float) -> float:
    risk = abs(entry - tight_sl)
    if direction == "BUY":
        return round(tight_sl - risk * (HARD_SL_RISK_MULTIPLIER - 1), 2)
    else:
        return round(tight_sl + risk * (HARD_SL_RISK_MULTIPLIER - 1), 2)

# ── Twelve Data fetcher ──────────────────────────────────
def fetch_market_data():
    try:
        from data_provider_twelvedata import fetch_market_data as td_fetch
        return td_fetch("XAU/USD")
    except Exception as e:
        logger.error(f"Data fetch error: {e}")
        return None, None, None

# ── FastAPI ──────────────────────────────────────────────
app = FastAPI(title="Alpha Buffalo Signal Bot", version="5.3")

@app.get("/trade/state")
def trade_state():
    """ดูสถานะ Trade Manager"""
    return tm.state.summary()

@app.get("/trade/state/buy")
def trade_state_buy():
    """สถานะไม้ Buy"""
    return buy_engine.state.summary()

@app.get("/trade/state/sell")
def trade_state_sell():
    """สถานะไม้ Sell"""
    return sell_engine.state.summary()

@app.get("/trade/state")
def trade_state():
    """สถานะทั้งสองไม้"""
    return {
        "buy": buy_engine.state.summary(),
        "sell": sell_engine.state.summary()
    }


@app.get("/health")
async def health():
    """Health Check + Full Dashboard"""
    from datetime import datetime, timezone
    
    result = {
        "status": "alive",
        "version": "5.4",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    
    # Try to get trend info
    try:
        from signal_composer import get_market_summary
        summary = get_market_summary()
        result["trend"] = summary.get("trend", {})
        result["session"] = summary.get("session", {})
        result["scanner"] = summary.get("scanner", {})
        result["signals_today"] = summary.get("signals_today", 0)
    except:
        result["trend"] = {"status": "unavailable"}
    
    return result


@app.get("/signal/latest")
async def latest_signal(key: str):
    if not license_manager.validate_key(key):
        return JSONResponse(status_code=403, content={"error": "Invalid license"})

    df_15m, df_1h, df_4h = fetch_market_data()
    if df_15m is None:
        resp = {"status": "NO_SIGNAL", "score": 0}
        write_signal_to_file(resp)
        return resp

    trade_signal = get_trade_signal(df_15m, df_1h, df_4h)
    if trade_signal is None or trade_signal.get("direction") is None:
        resp = {"status": "NO_SIGNAL", "score": 0}
        write_signal_to_file(resp)
        return resp

    if trade_signal["score"] < SIGNAL_THRESHOLD:
        resp = {"status": "NO_SIGNAL", "score": trade_signal["score"]}
        write_signal_to_file(resp)
        return resp

    direction = trade_signal["direction"]
    entry = trade_signal["entry"]
    tight_sl = trade_signal["sl"]
    hard_sl = compute_hard_sl(direction, entry, tight_sl)
    session_info = get_market_session_info()
    is_v5 = trade_signal.get("signal_type") == "V5_SNIPER"

    signal = CloudSignal(
        timestamp=datetime.now(timezone.utc).isoformat(),
        direction=direction,
        score=trade_signal["score"],
        entry=entry,
        tp1=trade_signal["tp1"],
        tp2=trade_signal["tp2"],
        sl=tight_sl,
        hard_sl=hard_sl,
        visual_sl=tight_sl,
        vsa_bias="BULLISH" if trade_signal["score"] > 5 else "NEUTRAL",
        is_v5=is_v5,
        v5_tp1=trade_signal["tp1"] if is_v5 else None,
        v5_tp2=trade_signal["tp2"] if is_v5 else None,
        session=session_info['session'],
        signal_type=trade_signal.get("signal_type", "V4_SESSION"),
    )

    broadcast_signal(format_signal_message(signal))
    signal_dict = signal.dict()
    write_signal_to_file(signal_dict)
    return signal_dict

# ── Main + Scheduler ─────────────────────────────────────
if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))

    scheduler = BackgroundScheduler()
    def trend_update_job():
        try:
            df_15m, df_1h, df_4h = fetch_market_data()
            if df_15m is None: return
            session_info = get_market_session_info()
            session = session_info['session']
            price = float(df_15m["close"].iloc[-1])
            def trend(df):
                last = df["close"].iloc[-1]
                ma20 = df["close"].iloc[-20:].mean() if len(df) >= 20 else df["close"].mean()
                return "⬆️ Bullish" if last > ma20 else "⬇️ Bearish"
            msg = (
                "📊 XAUUSD TREND UPDATE\n"
                "━━━━━━━━━━━━━━━━━━━━━\n"
                f"🕐 Session : {session}\n"
                f"💰 Price   : {price:,.2f}\n\n"
                f"➡️ M15  : {trend(df_15m)}\n"
                f"➡️ H1  : {trend(df_1h)}\n"
                f"📈 H4  : {trend(df_4h)}\n"
                "━━━━━━━━━━━━━━━━━━━━━\n"
                "⏳ Wait and See...\n\n"
                "⚠️ Not financial advice. Trade at your own risk."
            )
            broadcast_signal(msg)
        except Exception as e:
            logger.error(f"Trend update error: {e}")

    scheduler.add_job(trend_update_job, 'interval', hours=1)
    
# ═══ Daily Briefing Scheduler ═══
# # removed  # removed
import asyncio

@app.on_event("startup")
async def start_daily_briefing():
    """เริ่ม Daily Automation เมื่อ server start"""
    pass  # daily_automation removed
    logger.info("📅 Daily Briefing Automation started")

    logger.info("📈 Trend update scheduler started (every 1 hour)")

    logger.info("Starting Alpha Buffalo v5.3")
if __name__ == "__main__":
    import uvicorn


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("alpha_buffalo_signal:app", host="0.0.0.0", port=8000, reload=False)
