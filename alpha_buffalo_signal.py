"""
alpha_buffalo_signal.py — Alpha Buffalo v5 Cloud-Driven (v5.4.0-Sniper)
Architecture: Pre-Load Trap & Tick-Speed Execution
"""
import os, requests, time, threading, uuid
from datetime import datetime, timezone, timedelta
from typing import Optional
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel
import uvicorn
from signal_engine import compute_signal, signal_to_dict
from trend_monitor import (analyze_trend, format_trend_message,
                            format_signal_message, format_welcome_message,
                            should_send_trend_alert)

BKK = timezone(timedelta(hours=7))

# ── FastAPI ───────────────────────────────────────────────
app = FastAPI(title="Alpha Buffalo v5")
latest_signal: dict = {}
signal_history: list = []

# --- STATE MANAGEMENT (SNIPER AMBUSH PROTOCOL) ---
current_trap_state = {
    "status": "INACTIVE",           # INACTIVE, PRE_LOAD, ACTIVE, DONE
    "trap_id": "NONE", 
    "strategy": "VSA_WALL_PRELOAD", 
    "direction": "",
    "zone_type": "MANUAL", 
    "trap_price": 0.0, 
    "sl": 0.0, 
    "tp": 0.0,
    "max_slippage_points": 50, 
    "be_trigger": True, 
    "active_from_unix": 0,          # จุดเริ่มต้นเวลาที่ EA อนุญาตให้ยิง (เช่น 19:29)
    "active_to_unix": 0,            # หมดเวลาดักยิง (เช่น 19:31)
    "ticket": 0                     # EA จะส่งกลับมาใส่เมื่อยิงสำเร็จ
}

VALID_LICENSES = set(os.getenv("VALID_LICENSES", "DEMO123").split(","))
def chk(key): return key in VALID_LICENSES

# --- API CONTRACTS (MODELS) ---
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
    is_v5:         bool  = False
    v5_tp1:        float = 0.0
    v5_tp2:        float = 0.0
    next_pattern:  str   = ""
    d_point:       float = 0.0
    prz_low_zone:  float = 0.0

class CP(BaseModel):
    signal_id: str; executed: bool; license: str

class RP(BaseModel):
    signal_id: str
    license:   str
    hit_sl:    bool
    price:     float

# --- API ROUTES ---
@app.get("/")
def root(): return "OK"

@app.head("/")
def head(): return Response(status_code=200)

@app.get("/health")
def health():
    return {"status":"ok","version":"v5.4.0-Sniper","latest_signal":latest_signal.get("direction","none")}

# [อัปเดต] เลิกใช้ JSON เพื่อแก้ 422 รับค่าผ่าน URL ตรงๆ
@app.api_route("/webhook/mt5", methods=["GET", "POST"])
async def mt5_webhook(action: str = "", price: float = 0.0, lead_minutes: int = 1, duration_minutes: int = 2):
    global current_trap_state
    
    action = action.upper().replace("TEST_", "")
    if action not in ["BUY", "SELL"] or price <= 0:
        return {"status": "IGNORED", "message": "Invalid parameters"}
        
    now = int(time.time())
    # กำหนดเวลานัดหมายล่วงหน้า
    active_from = now + (lead_minutes * 60)
    active_to = active_from + (duration_minutes * 60)
    
    current_trap_state.update({
        "status": "PRE_LOAD",
        "trap_id": f"TRAP_{now}",
        "strategy": "VSA_WALL_PRELOAD",
        "direction": action,
        "trap_price": price,
        "sl": price - 3.0 if action == "BUY" else price + 3.0,
        "tp": price + 6.0 if action == "BUY" else price - 6.0,
        "active_from_unix": active_from,
        "active_to_unix": active_to,
        "ticket": 0
    })
    
    print(f"🎯 [PRE-LOAD TRAP SET] {action} @ {price} | Active: {lead_minutes}m from now")
    return {"status": "SUCCESS", "message": "Trap pre-loaded successfully", "data": current_trap_state}

@app.get("/trap")
def get_trap():
    # ฝั่ง EA จะเดินมาอ่านป้ายหน้านี้ทุกๆ 1-5 นาที
    return current_trap_state

# [ใหม่] Endpoint ให้ EA โยนงานกลับมาให้ Python หลังยิงออเดอร์เสร็จ
@app.api_route("/report_trade", methods=["GET", "POST"])
async def report_trade(trap_id: str = "", ticket: int = 0, exec_price: float = 0.0):
    global current_trap_state
    if current_trap_state["trap_id"] == trap_id:
        current_trap_state["status"] = "DONE"
        current_trap_state["ticket"] = ticket
        print(f"✅ [SNIPER EXECUTED] Trap {trap_id} | Ticket: {ticket} | Price: {exec_price}")
        return {"status": "SUCCESS", "message": "Trade handover complete"}
    return {"status": "ERROR", "message": "Trap ID mismatch or expired"}

@app.post("/webhook/signal")
async def recv(p: SP):
    global latest_signal
    sid = p.signal_id or str(uuid.uuid4())[:8]
    latest_signal = {**p.model_dump(), "signal_id":sid, "confirmed":False,
                     "created_at":datetime.now(BKK).isoformat()}
    signal_history.append(latest_signal.copy())
    if len(signal_history)>100: signal_history.pop(0)
    return {"ok":True,"signal_id":sid}

@app.get("/signal/latest")
def latest(key:str=""):
    if not chk(key): raise HTTPException(403,"Invalid license")
    return latest_signal or {"direction":"","signal_id":""}

@app.post("/signal/confirm")
async def confirm(p: CP):
    if not chk(p.license): raise HTTPException(403,"Invalid license")
    if latest_signal.get("signal_id")==p.signal_id:
        latest_signal["confirmed"]=p.executed
    return {"ok":True}

@app.get("/signal/history")
def history(key:str="",limit:int=20):
    if not chk(key): raise HTTPException(403,"Invalid license")
    return signal_history[-limit:]

@app.get("/signal/scenarios")
def scenarios(key: str = ""):
    if not chk(key): raise HTTPException(403, "Invalid license")
    try:
        from scenario_scanner import scenario_scanner
        return {"ok": True, "zones": scenario_scanner.get_summary()}
    except Exception as e:
        return {"ok": False, "zones": [], "error": str(e)}

@app.get("/signal/zone_check")
def zone_check(key:str=""):
    if not chk(key): raise HTTPException(403,"Invalid license")
    sig = latest_signal
    if not sig:
        return {"zone_valid": False, "reentry_ok": False, "vsa_bias": "NEUTRAL"}
    return {
        "zone_valid":    sig.get("zone_valid", False),
        "reentry_ok":    sig.get("reentry_ok", False),
        "visual_sl":     sig.get("visual_sl",  0.0),
        "vsa_bias":      sig.get("vsa_bias",   "NEUTRAL"),
        "gps_confirmed": sig.get("gps_confirmed", False),
        "direction":     sig.get("direction",  ""),
        "signal_id":     sig.get("signal_id",  ""),
    }

@app.post("/signal/reentry")
async def reentry(p: RP):
    if not chk(p.license): raise HTTPException(403,"Invalid license")
    sig = latest_signal
    if not sig or sig.get("signal_id") != p.signal_id:
        return {"allowed": False, "reason": "Signal mismatch"}
    if not sig.get("zone_valid", False):
        return {"allowed": False, "reason": "Zone no longer valid"}
    if not sig.get("reentry_ok", False):
        return {"allowed": False, "reason": "VSA bias: " + sig.get("vsa_bias","NEUTRAL")}
    return {
        "allowed":   True,
        "direction": sig.get("direction"),
        "entry":     p.price,
        "visual_sl": sig.get("visual_sl"),
        "tp_final":  sig.get("tp_final"),
        "partial":   sig.get("partial", []),
        "reason":    "Zone valid + VSA " + sig.get("vsa_bias",""),
    }

# ── Config & Main Loop ────────────────────────────────────
TELEGRAM_TOKEN  = os.getenv("TELEGRAM_TOKEN")
ADMIN_ID        = os.getenv("ADMIN_ID","0")
_notify_raw     = os.getenv("NOTIFY_IDS", ADMIN_ID)
NOTIFY_IDS      = [x.strip() for x in _notify_raw.split(",") if x.strip()]
TELEGRAM_API    = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
POLL_INTERVAL   = int(os.getenv("POLL_INTERVAL","1800"))
TWELVE_KEY      = os.getenv("TWELVE_API_KEY","")
SYMBOL          = os.getenv("TRADE_SYMBOL","XAUUSD")

last_update_id = 0
last_signal_time = None

def log(msg): print(f"{datetime.now(BKK).strftime('%H:%M:%S')} | {msg}", flush=True)

def send_telegram(msg, chat_id=None):
    targets = [str(chat_id)] if chat_id else NOTIFY_IDS
    for cid in targets:
        try:
            requests.post(f"{TELEGRAM_API}/sendMessage",
                json={"chat_id":cid,"text":msg,"parse_mode":"HTML"},timeout=10)
        except Exception as e: log(f"telegram error {cid}: {e}")

def get_ohlcv(interval="1h", bars=200):
    try:
        sym_map = {"XAUUSD":"XAU/USD","EURUSD":"EUR/USD","BTCUSD":"BTC/USD"}
        sym = sym_map.get(SYMBOL, SYMBOL)
        r = requests.get("https://api.twelvedata.com/time_series",
            params={"symbol":sym,"interval":interval,"outputsize":bars,
                    "apikey":TWELVE_KEY,"format":"JSON"},timeout=15)
        data = r.json()
        if "values" not in data: return None
        import pandas as pd
        df = pd.DataFrame(data["values"])
        df["datetime"] = pd.to_datetime(df["datetime"])
        df = df.sort_values("datetime").reset_index(drop=True)
        df.set_index("datetime",inplace=True)
        df.index = df.index.tz_localize("UTC")
        for c in ["open","high","low","close"]:
            df[c] = df[c].astype(float)
        if "volume" in df.columns: df["volume"] = df["volume"].astype(float)
        return df
    except Exception as e: log(f"ohlcv error: {e}"); return None

def is_market_open():
    now = datetime.now(timezone.utc)
    if now.weekday()==5: return now.hour<22
    if now.weekday()==6: return now.hour>=22
    return True

def signal_loop():
    log("📡 Signal loop started")
    while True:
        try:
            if not is_market_open():
                log("🔴 ตลาดปิด"); time.sleep(POLL_INTERVAL); continue
            log("⏳ Fetching 4H/1H/15M...")
            df_4h  = get_ohlcv("4h",  100)
            df_1h  = get_ohlcv("1h",  200)
            df_15m = get_ohlcv("15min",96)
            if df_4h is None or df_1h is None or df_15m is None:
                log("⚠️ ดึงข้อมูลไม่ครบ"); time.sleep(POLL_INTERVAL); continue
            price = float(df_15m["close"].iloc[-1])
            log(f"💰 {SYMBOL}: {price:,.2f}")
            trend = analyze_trend(df_4h, df_1h, df_15m, SYMBOL)
            if should_send_trend_alert(trend.session):
                send_telegram(format_trend_message(trend))
                log("📊 Trend: " + trend.session + " " + trend.bias)

            sig = compute_signal(df_4h, df_1h, df_15m)
            if sig:
                sig_dict = signal_to_dict(sig)
                port = int(os.getenv("PORT",8080))
                try:
                    requests.post(f"http://localhost:{port}/webhook/signal",
                                  json=sig_dict, timeout=3)
                except: pass
                tp1 = sig.partial[0]["price"] if sig.partial else sig.tp_final
                tp2 = sig.partial[1]["price"] if len(sig.partial) > 1 else sig.tp_final
                msg = format_signal_message(
                    direction=sig.direction, signal_type=sig.signal_type,
                    entry=sig.entry, sl=sig.sl, tp1=tp1, tp2=tp2,
                    pattern=sig.pattern, score=sig.score, session=trend.session,
                )
                send_telegram(msg)
                log("Signal: " + sig.direction + " " + sig.signal_type + " Score:" + str(sig.score))
            else:
                log(f"⏳ No signal | {price:,.2f}")

            try:
                from scenario_scanner import run_scenario_scan
                zones = run_scenario_scan(df_15m)
                if zones:
                    log(f"🔍 Scenarios active: {len(zones)}")
            except Exception as e:
                log(f"⚠️ scenario_scanner error: {e}")

        except Exception as e: log(f"signal_loop error: {e}")
        time.sleep(POLL_INTERVAL)

def command_loop():
    global last_update_id
    log("🤖 Command loop started")
    while True:
        try:
            r = requests.get(f"{TELEGRAM_API}/getUpdates",
                params={"offset":last_update_id+1,"timeout":2,"allowed_updates":["message"]},
                timeout=10)
            data = r.json()
            if not data.get("ok"):
                last_update_id=0; time.sleep(5); continue
            updates = data.get("result",[])
            if updates:
                last_update_id = updates[-1]["update_id"]
                for upd in updates:
                    msg  = upd.get("message",{})
                    text = msg.get("text","").strip()
                    cid  = msg.get("chat",{}).get("id")
                    if text and cid: handle_cmd(text, cid)
        except requests.exceptions.Timeout: pass
        except Exception as e: log(f"cmd error: {e}"); time.sleep(10)
        time.sleep(3)

def handle_cmd(text, chat_id):
    t = text.lower()
    if t == "/start":
        send_telegram(format_welcome_message(), chat_id)
    elif t == "/status":
        send_telegram("v5 Market:" + ("Open" if is_market_open() else "Closed"), chat_id)
    elif t == "/price":
        df = get_ohlcv("15min", 1)
        p = float(df["close"].iloc[-1]) if df is not None else 0
        send_telegram("XAUUSD: " + "{:,.2f}".format(p), chat_id)
    elif t == "/health":
        send_telegram("Bot running | Latest: " + latest_signal.get("direction", "none"), chat_id)
    elif t == "/context":
        try:
            from context_engine import get_context_status
            s = get_context_status()
            msg = "Market Context"
            msg += "\nNews : " + ("OK" if s["news_safe"] else "BLOCK") + " " + str(s["news_reason"])
            msg += "\nF&G  : " + str(s["fear_greed"])
            msg += "\nDXY  : " + str(s["dxy_trend"])
            msg += "\nCOT  : " + str(s["cot_rank"])
            msg += "\nTime : " + str(s["timestamp"])
        except Exception as e:
            msg = "Context error: " + str(e)
        send_telegram(msg, chat_id)
    elif t == "/setup":
        try:
            from early_warning import get_warning_status
            s = get_warning_status("XAUUSD")
            if s["stage"] == 0:
                msg = "⚡ Setup Status\n⏳ No active setup"
            else:
                stage_emoji = {1: "👀", 2: "🎯", 3: "🚀"}
                msg = "⚡ Setup Status"
                msg += "\n" + stage_emoji.get(s["stage"], "") + " Stage: " + str(s["status"])
                msg += "\nDir    : " + str(s.get("direction", "N/A"))
                msg += "\nScore  : " + str(s.get("score", 0))
                if s.get("pattern"):
                    msg += "\nPattern: " + str(s["pattern"])
                if s.get("setup_price"):
                    msg += "\nPrice  : " + "{:,.2f}".format(s["setup_price"])
        except Exception as e:
            msg = "Setup error: " + str(e)
        send_telegram(msg, chat_id)
    elif t in ("/quota", "/newlicense", "/newtrial", "/licenses") or \
         t.startswith("/newlicense ") or t.startswith("/newtrial ") or \
         t.startswith("/revoke ") or t.startswith("/extend ") or \
         t.startswith("/quota "):
        try:
            from license_manager import handle_admin_command
            msg = handle_admin_command(t)
        except Exception as e:
            msg = "License error: " + str(e)
        send_telegram(msg, chat_id)
    elif t == "/session":
        try:
            from session_weight import format_session_status, should_close_asia_positions
            msg = format_session_status()
            close = should_close_asia_positions()
            if close["should_close"]:
                msg += "\n" + ("URGENT" if close["urgent"] else "INFO") + ": " + close["reason"]
        except Exception as e:
            msg = "Session error: " + str(e)
        send_telegram(msg, chat_id)
    elif t in ("/help", "/?"):
        send_telegram("/status /price /health /context /setup\n/quota /newlicense /newtrial /revoke /extend /licenses", chat_id)

if __name__ == "__main__":
    print("🐃 ALPHA BUFFALO v5 (Sniper Ambush) started\n")
    threading.Thread(target=command_loop, daemon=True).start()
    threading.Thread(target=signal_loop,  daemon=True).start()
    port = int(os.getenv("PORT",8080))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")
