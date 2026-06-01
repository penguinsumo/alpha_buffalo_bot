"""
alpha_buffalo_signal.py — Alpha Buffalo v5 Cloud-Driven
(อัปเดตรองรับระบบ Sniper Trap & Dynamic SL)
"""
import os, requests, time, threading, uuid
from datetime import datetime, timezone, timedelta
from typing import Optional
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
import uvicorn

# Import โมดูลเดิมของคุณทั้งหมด
from signal_engine import compute_signal, signal_to_dict
from trend_monitor import (analyze_trend, format_trend_message,
                            format_signal_message, format_welcome_message,
                            should_send_trend_alert)

BKK = timezone(timedelta(hours=7))

# ── FastAPI ───────────────────────────────────────────────
app = FastAPI(title="Alpha Buffalo v5")
latest_signal: dict = {}
signal_history: list = []

VALID_LICENSES = set(os.getenv("VALID_LICENSES", "DEMO123").split(","))
def chk(key): return key in VALID_LICENSES

class SP(BaseModel):
    direction: str; signal_type: str
    entry: float; sl: float; be_price: float
    trail_from: float; tp_final: float
    partial: list; pattern: str = ""
    score: int; layer: int; session: str
    fallback_sl: float; fallback_tp: float
    signal_id: str = ""; action: str = "OPEN"
    visual_sl:     float = 0.0
    zone_valid:    bool  = False
    reentry_ok:    bool  = False
    vsa_bias:      str   = ""
    gps_confirmed: bool  = False

@app.get("/")
def root(): return "OK"

@app.get("/health")
def health(): return {"status": "ok", "time": datetime.now(BKK).isoformat()}

# === 🚀 [NEW] Endpoint ลับสำหรับส่งแผนที่ให้ Sniper EA ===
@app.get("/signal/sniper")
def get_sniper_trap(key: str = ""):
    """EA ขอรับข้อมูลกับดัก (Sniper Payload แบบแบน)"""
    if not chk(key): raise HTTPException(403, "Invalid license")
    try:
        from scenario_scanner import scenario_scanner
        trap_data = scenario_scanner.generate_sniper_payload()
        return trap_data
    except Exception as e:
        return {"status": "ERROR", "message": str(e)}

@app.get("/signal/scenarios")
def scenarios(key: str = ""):
    """คืน active VSA zones สำหรับ EA / dashboard / Telegram"""
    if not chk(key): raise HTTPException(403, "Invalid license")
    try:
        from scenario_scanner import scenario_scanner
        return {"ok": True, "zones": scenario_scanner.get_summary()}
    except Exception as e:
        return {"ok": False, "zones": [], "error": str(e)}

@app.get("/signal/latest")
def get_latest(key: str = ""):
    if not chk(key): raise HTTPException(403, "Invalid license")
    return latest_signal if latest_signal else {"action": "WAIT"}

@app.post("/webhook/signal")
def receive_signal(sp: SP, key: str = ""):
    if not chk(key): raise HTTPException(403, "Invalid license")
    global latest_signal
    sp.signal_id = str(uuid.uuid4())
    latest_signal = sp.model_dump()
    signal_history.append(latest_signal)
    if len(signal_history) > 100: signal_history.pop(0)
    return {"ok": True, "signal_id": sp.signal_id}

# ── Telegram Bot (โค้ดเดิม) ──────────────────────────────────
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
def send_telegram(msg, chat_id):
    if not TELEGRAM_TOKEN or not chat_id: return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try: requests.post(url, json={"chat_id": chat_id, "text": msg, "parse_mode": "HTML"}, timeout=5)
    except: pass

def command_loop():
    if not TELEGRAM_TOKEN: return
    offset = 0
    while True:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates?timeout=30&offset={offset}"
            r = requests.get(url, timeout=35).json()
            if r.get("ok"):
                for upd in r["result"]:
                    offset = upd["update_id"] + 1
                    msg = upd.get("message")
                    if msg and "text" in msg:
                        t = msg["text"].strip()
                        chat_id = msg["chat"]["id"]
                        
                        # Command Handling
                        if t == "/status":
                            try:
                                from basket_engine import basket_engine
                                stat = basket_engine.summary()
                            except: stat = "Manager Error"
                            send_telegram(f"<b>System Status</b>\n{stat}", chat_id)
                        elif t == "/price":
                            send_telegram("<b>Price:</b> Manual check via MT5", chat_id)
                        elif t == "/context":
                            try:
                                from trend_monitor import format_context
                                msg_txt = format_context()
                            except: msg_txt = "Context not available"
                            send_telegram(msg_txt, chat_id)
                        elif t == "/setup":
                            send_telegram("Alpha Buffalo v5 is running.", chat_id)
                        elif t == "/session":
                            try:
                                from session_weight import format_session_status, should_close_asia_positions
                                msg_txt = format_session_status()
                                close = should_close_asia_positions()
                                if close["should_close"]:
                                    msg_txt += "\n" + ("URGENT" if close["urgent"] else "INFO") + ": " + close["reason"]
                            except Exception as e:
                                msg_txt = "Session error: " + str(e)
                            send_telegram(msg_txt, chat_id)
                        elif t in ("/help", "/?"):
                            send_telegram("/status /price /health /context /setup /session", chat_id)
        except Exception as e:
            print(f"Telegram loop error: {e}")
        time.sleep(1)

if __name__ == "__main__":
    print("🐃 ALPHA BUFFALO v5 (Sniper Mode Enabled) started\n")
    threading.Thread(target=command_loop, daemon=True).start()
    port = int(os.getenv("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")
