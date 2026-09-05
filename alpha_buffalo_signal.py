"""
alpha_buffalo_signal.py — Alpha Buffalo v5 Cloud-Driven
"""
import os
import requests
import time
import threading
import traceback
import uuid
import pandas as pd
from datetime import datetime, timezone, timedelta
from fastapi import FastAPI, HTTPException
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
    # V5 + Reversal fields
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

@app.get("/")
def root(): return "OK"

@app.head("/")
def head(): return Response(status_code=200)

@app.get("/health")
def health():
    return {"status":"ok","version":"v5","latest_signal":latest_signal.get("direction","none")}

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
    """คืน active VSA zones สำหรับ EA / dashboard / Telegram"""
    if not chk(key): raise HTTPException(403, "Invalid license")
    try:
        from scenario_scanner import scenario_scanner
        return {"ok": True, "zones": scenario_scanner.get_summary()}
    except Exception as e:
        return {"ok": False, "zones": [], "error": str(e)}

@app.get("/signal/zone_check")
def zone_check(key:str=""):
    """EA ถาม: zone ยัง valid + reentry อนุญาตไหม"""
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

class RP(BaseModel):
    signal_id: str
    license:   str
    hit_sl:    bool
    price:     float

@app.post("/signal/reentry")
async def reentry(p: RP):
    """EA แจ้ง: ชน visual_sl ขอ re-entry — ตรวจ zone + VSA ก่อนอนุญาต"""
    if not chk(p.license): raise HTTPException(403,"Invalid license")
    sig = latest_signal
    if not sig or sig.get("signal_id") != p.signal_id:
        return {"allowed": False, "reason": "Signal mismatch"}
    if not sig.get("zone_valid", False):
        return {"allowed": False, "reason": "Zone no longer valid"}
    if not sig.get("reentry_ok", False):
        return {"allowed": False, "reason": "VSA bias: " + sig.get("vsa_bias","NEUTRAL")}
    # อนุญาต re-entry — ส่ง visual_sl กลับเป็น SL ใหม่
    return {
        "allowed":   True,
        "direction": sig.get("direction"),
        "entry":     p.price,
        "visual_sl": sig.get("visual_sl"),
        "tp_final":  sig.get("tp_final"),
        "partial":   sig.get("partial", []),
        "reason":    "Zone valid + VSA " + sig.get("vsa_bias",""),
    }

# ── Config ────────────────────────────────────────────────
TELEGRAM_TOKEN  = os.getenv("TELEGRAM_TOKEN")
ADMIN_ID        = os.getenv("ADMIN_ID","0")
# NOTIFY_IDS = "ADMIN_ID,-100xxx,-100yyy" คั่นด้วย comma
_notify_raw     = os.getenv("NOTIFY_IDS", ADMIN_ID)
NOTIFY_IDS      = [x.strip() for x in _notify_raw.split(",") if x.strip()]
TELEGRAM_API    = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
POLL_INTERVAL   = int(os.getenv("POLL_INTERVAL","1800"))
TWELVE_KEY      = os.getenv("TWELVE_API_KEY","")
SYMBOL          = os.getenv("TRADE_SYMBOL","XAUUSD")

# ── [OPT-IN, default OFF] Extra symbols into the same signal room ──────
# ALPHA_EXTRA_SYMBOLS_ENABLED=true runs the exact same trend/signal engine
# used for SYMBOL above against additional symbols (BTC/US100/JPN225) and
# broadcasts real BUY/SELL signals for them into the signal room too.
# Root cause this exists for: the production loop was hardcoded to one
# module-level SYMBOL end-to-end -- this is purely additive, the main
# SYMBOL path below is untouched byte-for-byte when this flag is off.
#
# Ticker verification status (checked 2026-09-05 against TwelveData):
#   BTCUSD  -> BTC/USD  (confirmed, already used by the main sym_map below)
#   JPN225  -> N225     (confirmed against TwelveData's own /indices list)
#   US100   -> NDX      (NOT independently confirmed -- TwelveData's
#     free/keyless /indices list excludes all United States indices
#     entirely, so this could not be verified from this sandbox. NDX is
#     the standard Nasdaq-100 ticker used elsewhere in this project's docs,
#     kept here as the default, but VERIFY with the real TWELVE_API_KEY
#     (e.g. .../symbol_search?symbol=NASDAQ&apikey=...) before trusting its
#     output for real signals -- override via ALPHA_EXTRA_SYMBOL_US100_TICKER
#     if it turns out to be wrong.
EXTRA_SYMBOLS_ENABLED = os.getenv("ALPHA_EXTRA_SYMBOLS_ENABLED", "false").lower() in {
    "1", "true", "yes", "on",
}
_extra_symbols_raw = os.getenv("ALPHA_EXTRA_SYMBOLS", "BTCUSD,US100,JPN225")
EXTRA_SYMBOLS = [s.strip() for s in _extra_symbols_raw.split(",") if s.strip()]

# Which room(s) extra-symbol signals go to. Defaults to the SAME room(s) as
# the main SYMBOL signal (NOTIFY_IDS) -- i.e. "the signal room" the way it
# was asked for. Override with a separate list if these should NOT be mixed
# into the same room as the main XAUUSD signal.
_extra_notify_raw = os.getenv("ALPHA_EXTRA_SYMBOLS_NOTIFY_IDS", _notify_raw)
EXTRA_NOTIFY_IDS = [x.strip() for x in _extra_notify_raw.split(",") if x.strip()]

# "เปิดทุก Session" -- extra symbols run every poll regardless of the main
# is_market_open() weekend/session gate (BTC trades 24/7; gating it on
# gold's weekend schedule would silently skip it for ~2 days every week).
EXTRA_BYPASS_MARKET_GATE = os.getenv("ALPHA_EXTRA_SYMBOLS_BYPASS_MARKET_GATE", "true").lower() in {
    "1", "true", "yes", "on",
}

# "ระงับสัญญาณ pine monitor หรือน้อยสุดที่ 4H/1ครั้ง" -- the periodic
# no-signal "Trend Update" ping (as opposed to an actual BUY/SELL signal,
# which is never throttled) defaults to fully suppressed for extra symbols.
# If explicitly re-enabled, it is floor-capped to once per this interval
# per symbol -- independent of the main SYMBOL's own per-session trend
# alert in trend_monitor.py, which this does not touch at all.
EXTRA_TREND_UPDATE_ENABLED = os.getenv("ALPHA_EXTRA_SYMBOLS_TREND_UPDATE_ENABLED", "false").lower() in {
    "1", "true", "yes", "on",
}
EXTRA_TREND_UPDATE_MIN_INTERVAL_SEC = int(
    os.getenv("ALPHA_EXTRA_SYMBOLS_TREND_UPDATE_MIN_INTERVAL_SEC", "14400")  # 4 hours
)
_extra_trend_last_sent: dict = {}

last_update_id = 0

# Heartbeat: signal_loop updates this every time it completes one full pass
# (success or handled failure). The watchdog thread below uses it to detect
# a stalled/dead loop and alert the owner instead of staying silent for
# hours -- see _signal_loop_watchdog().
_last_loop_heartbeat: float = time.time()
_loop_lock = threading.Lock()

def log(msg): print(f"{datetime.now(BKK).strftime('%H:%M:%S')} | {msg}", flush=True)

def send_telegram(msg, chat_id=None):
    # ถ้ามี chat_id (reply ตรง) → ส่งแค่ห้องนั้น
    # ถ้าไม่มี (auto signal) → broadcast ทุก NOTIFY_IDS
    targets = [str(chat_id)] if chat_id else NOTIFY_IDS
    for cid in targets:
        try:
            resp = requests.post(f"{TELEGRAM_API}/sendMessage",
                json={"chat_id":cid,"text":msg,"parse_mode":"HTML"},timeout=10)
            body = resp.json()
            if not body.get("ok"):
                # Telegram accepted the HTTP request but rejected the send
                # (bad chat_id, bot kicked/blocked, etc). requests does not
                # raise on this, so without this check a broken destination
                # fails completely silently -- log it so it shows up in
                # Railway logs instead of just disappearing.
                log(f"telegram rejected {cid}: {body.get('description')}")
        except Exception as e: log(f"telegram error {cid}: {e}")

EXTRA_SYMBOL_TICKERS = {
    "BTCUSD": "BTC/USD",
    "US100":  os.getenv("ALPHA_EXTRA_SYMBOL_US100_TICKER", "NDX"),
    "JPN225": os.getenv("ALPHA_EXTRA_SYMBOL_JPN225_TICKER", "N225"),
}

def get_ohlcv(interval="1h", bars=200, symbol=None):
    # symbol=None (default, all existing call sites) preserves old behavior
    # exactly -- resolves against the module-level SYMBOL. Passing an
    # explicit symbol is what lets the extra-symbol scan reuse this same
    # function without touching the main SYMBOL path at all.
    target_symbol = symbol or SYMBOL
    try:
        sym_map = {"XAUUSD":"XAU/USD","EURUSD":"EUR/USD","BTCUSD":"BTC/USD"}
        sym_map.update(EXTRA_SYMBOL_TICKERS)
        sym = sym_map.get(target_symbol, target_symbol)
        r = requests.get("https://api.twelvedata.com/time_series",
            params={"symbol":sym,"interval":interval,"outputsize":bars,
                    "apikey":TWELVE_KEY,"format":"JSON"},timeout=15)
        data = r.json()
        if "values" not in data: return None
        df = pd.DataFrame(data["values"])
        df["datetime"] = pd.to_datetime(df["datetime"])
        df = df.sort_values("datetime").reset_index(drop=True)
        df.set_index("datetime",inplace=True)
        df.index = df.index.tz_localize("UTC")
        for c in ["open","high","low","close"]:
            df[c] = df[c].astype(float)
        if "volume" in df.columns: df["volume"] = df["volume"].astype(float)
        return df
    except Exception as e: log(f"ohlcv error ({target_symbol}): {e}"); return None

def is_market_open():
    now = datetime.now(timezone.utc)
    if now.weekday()==5: return now.hour<22
    if now.weekday()==6: return now.hour>=22
    return True

def _touch_heartbeat():
    global _last_loop_heartbeat
    with _loop_lock:
        _last_loop_heartbeat = time.time()

def _extra_trend_update_allowed(symbol: str) -> bool:
    if not EXTRA_TREND_UPDATE_ENABLED:
        return False
    now = time.time()
    last = _extra_trend_last_sent.get(symbol, 0.0)
    if now - last >= EXTRA_TREND_UPDATE_MIN_INTERVAL_SEC:
        _extra_trend_last_sent[symbol] = now
        return True
    return False

def run_extra_symbol_pass(symbol: str):
    """
    [OPT-IN, ALPHA_EXTRA_SYMBOLS_ENABLED] Run the exact same trend/signal
    engine used for the main SYMBOL above against one additional symbol
    (BTC/US100/JPN225) and broadcast a real signal into EXTRA_NOTIFY_IDS if
    one fires. Mirrors the main SYMBOL block in signal_loop() below but is
    fully independent of it -- an error here is caught and logged per
    symbol, never able to interrupt the main gold loop.
    """
    try:
        df_4h  = get_ohlcv("4h",  100, symbol=symbol)
        df_1h  = get_ohlcv("1h",  200, symbol=symbol)
        df_15m = get_ohlcv("15min", 96, symbol=symbol)
        if df_4h is None or df_1h is None or df_15m is None:
            log(f"⚠️ [{symbol}] ดึงข้อมูลไม่ครบ"); return

        price = float(df_15m["close"].iloc[-1])
        log(f"💰 [{symbol}] {price:,.2f}")
        trend = analyze_trend(df_4h, df_1h, df_15m, symbol)

        if _extra_trend_update_allowed(symbol):
            for cid in EXTRA_NOTIFY_IDS:
                send_telegram(format_trend_message(trend), chat_id=cid)
            log(f"📊 [{symbol}] Trend: {trend.session} {trend.bias}")

        sig = compute_signal(df_4h, df_1h, df_15m, symbol_label=symbol)
        if sig:
            tp1 = sig.partial[0]["price"] if sig.partial else sig.tp_final
            tp2 = sig.partial[1]["price"] if len(sig.partial) > 1 else sig.tp_final
            msg = format_signal_message(
                direction=sig.direction, signal_type=sig.signal_type,
                entry=sig.entry, sl=sig.sl, tp1=tp1, tp2=tp2,
                pattern=sig.pattern, score=sig.score, session=trend.session,
                symbol=symbol, ea_executes=False,
            )
            for cid in EXTRA_NOTIFY_IDS:
                send_telegram(msg, chat_id=cid)
            log(f"[{symbol}] Signal: {sig.direction} {sig.signal_type} Score:{sig.score}")
        else:
            log(f"⏳ [{symbol}] No signal | {price:,.2f}")
    except Exception as e:
        log(f"extra symbol {symbol} error: {e}")
        log(traceback.format_exc())

def signal_loop():
    log("📡 Signal loop started")
    _touch_heartbeat()
    while True:
        try:
            # [OPT-IN] Extra symbols (BTC/US100/JPN225) run every poll,
            # independent of the main gold market/session gate below --
            # "เปิดทุก Session" for these three specifically.
            if EXTRA_SYMBOLS_ENABLED:
                for extra_symbol in EXTRA_SYMBOLS:
                    if EXTRA_BYPASS_MARKET_GATE or is_market_open():
                        run_extra_symbol_pass(extra_symbol)

            if not is_market_open():
                log("🔴 ตลาดปิด"); _touch_heartbeat(); time.sleep(POLL_INTERVAL); continue
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
                log(f"📊 Trend: {trend.session} {trend.bias}")

            sig = compute_signal(df_4h, df_1h, df_15m)
            if sig:
                sig_dict = signal_to_dict(sig)
                port = int(os.getenv("PORT",8080))
                try:
                    requests.post(f"http://localhost:{port}/webhook/signal",
                                  json=sig_dict, timeout=3)
                except requests.RequestException as e:
                    log(f"webhook post error: {e}")
                tp1 = sig.partial[0]["price"] if sig.partial else sig.tp_final
                tp2 = sig.partial[1]["price"] if len(sig.partial) > 1 else sig.tp_final
                msg = format_signal_message(
                    direction=sig.direction, signal_type=sig.signal_type,
                    entry=sig.entry, sl=sig.sl, tp1=tp1, tp2=tp2,
                    pattern=sig.pattern, score=sig.score, session=trend.session,
                )
                send_telegram(msg)
                log(f"Signal: {sig.direction} {sig.signal_type} Score:{sig.score}")
            else:
                log(f"⏳ No signal | {price:,.2f}")

            # ── Scenario Scanner (Mode B — Telegram alert) ──
            try:
                from scenario_scanner import run_scenario_scan
                zones = run_scenario_scan(df_15m)
                if zones:
                    log(f"🔍 Scenarios active: {len(zones)}")
            except Exception as e:
                log(f"⚠️ scenario_scanner error: {e}")

        except Exception as e:
            log(f"signal_loop error: {e}")
            log(traceback.format_exc())
        _touch_heartbeat()
        time.sleep(POLL_INTERVAL)

def _signal_loop_watchdog():
    """
    signal_loop() runs as a daemon thread with no supervisor: if it ever
    hangs on a blocking call that ignores its own timeout (or dies from
    something the broad except Exception above can't catch), the process
    keeps serving HTTP fine -- health checks stay green -- while no new
    signal or Telegram message goes out, silently, indefinitely. This has
    happened in production before it was ever noticed.

    This watchdog just checks the heartbeat signal_loop touches every pass.
    If it goes stale for longer than a few missed cycles, alert the owner
    once, then force-exit so Railway's process supervisor restarts the
    container (a fresh process re-runs the startup hook and starts a new,
    healthy signal_loop thread).
    """
    STALE_AFTER = max(POLL_INTERVAL * 3, 1800)
    alerted = False
    while True:
        time.sleep(120)
        with _loop_lock:
            age = time.time() - _last_loop_heartbeat
        if age > STALE_AFTER and not alerted:
            alerted = True
            log(f"⚠️ WATCHDOG: signal_loop heartbeat stale for {int(age)}s -- alerting owner")
            try:
                send_telegram(
                    "⚠️ ALPHA BUFFALO WATCHDOG\n"
                    f"Signal loop has not completed a cycle in {int(age/60)} min.\n"
                    "Service will restart automatically.",
                    chat_id=ADMIN_ID,
                )
            except Exception as e:
                log(f"watchdog alert failed: {e}")
        if age > STALE_AFTER * 2:
            log("⚠️ WATCHDOG: signal_loop still stale after alert -- forcing restart")
            os._exit(1)

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
        except Exception as e:
            log(f"cmd error: {e}")
            log(traceback.format_exc())
            time.sleep(10)
        time.sleep(3)

def handle_cmd(text, chat_id):
    t = text.lower()
    if t == "/start":
        send_telegram(format_welcome_message(), chat_id)
    elif t == "/status":
        market_status = "Open" if is_market_open() else "Closed"
        send_telegram(f"v5 Market:{market_status}", chat_id)
    elif t == "/price":
        df = get_ohlcv("15min", 1)
        p = float(df["close"].iloc[-1]) if df is not None else 0
        send_telegram(f"XAUUSD: {p:,.2f}", chat_id)
    elif t == "/health":
        latest_dir = latest_signal.get("direction", "none")
        send_telegram(f"Bot running | Latest: {latest_dir}", chat_id)
    elif t == "/context":
        try:
            from context_engine import get_context_status
            s = get_context_status()
            news_status = "OK" if s["news_safe"] else "BLOCK"
            msg = "Market Context"
            msg += f"\nNews : {news_status} {s['news_reason']}"
            msg += f"\nF&G  : {s['fear_greed']}"
            msg += f"\nDXY  : {s['dxy_trend']}"
            msg += f"\nCOT  : {s['cot_rank']}"
            msg += f"\nTime : {s['timestamp']}"
        except Exception as e:
            msg = f"Context error: {e}"
        send_telegram(msg, chat_id)
    elif t == "/setup":
        try:
            from early_warning import get_warning_status
            s = get_warning_status("XAUUSD")
            if s["stage"] == 0:
                msg = "⚡ Setup Status\n⏳ No active setup"
            else:
                stage_emoji = {1: "👀", 2: "🎯", 3: "🚀"}
                emoji = stage_emoji.get(s["stage"], "")
                msg = "⚡ Setup Status"
                msg += f"\n{emoji} Stage: {s['status']}"
                msg += f"\nDir    : {s.get('direction', 'N/A')}"
                msg += f"\nScore  : {s.get('score', 0)}"
                if s.get("pattern"):
                    msg += f"\nPattern: {s['pattern']}"
                if s.get("setup_price"):
                    msg += f"\nPrice  : {s['setup_price']:,.2f}"
        except Exception as e:
            msg = f"Setup error: {e}"
        send_telegram(msg, chat_id)
    elif (t in ("/quota", "/newlicense", "/newtrial", "/licenses") or
          t.startswith("/newlicense ") or t.startswith("/newtrial ") or
          t.startswith("/revoke ") or t.startswith("/extend ") or
          t.startswith("/quota ")):
        try:
            from license_manager import handle_admin_command
            msg = handle_admin_command(t)
        except Exception as e:
            msg = f"License error: {e}"
        send_telegram(msg, chat_id)
    elif t == "/session":
        try:
            from session_weight import format_session_status, should_close_asia_positions
            msg = format_session_status()
            close = should_close_asia_positions()
            if close["should_close"]:
                urgency = "URGENT" if close["urgent"] else "INFO"
                msg += f"\n{urgency}: {close['reason']}"
        except Exception as e:
            msg = f"Session error: {e}"
        send_telegram(msg, chat_id)
    elif t in ("/help", "/?"):
        help_msg = "/status /price /health /context /setup\n/quota /newlicense /newtrial /revoke /extend /licenses"
        send_telegram(help_msg, chat_id)


# ── Background thread startup ────────────────────────────────
# IMPORTANT: Procfile runs `uvicorn alpha_buffalo_signal:app`, which
# imports this module rather than executing it as __main__. The old
# `if __name__ == "__main__":` block below never runs under that launch
# path, so signal_loop()/command_loop() were never actually started by
# the process Railway runs -- the FastAPI app still serves HTTP fine
# (health checks stay green), but no background thread was ever alive
# to generate a signal or send it to Telegram. Starting them from a
# FastAPI startup event fires under both launch paths (uvicorn CLI
# import, and `python alpha_buffalo_signal.py` via uvicorn.run below),
# and fires exactly once per process either way.
_background_threads_started = False

@app.on_event("startup")
def _start_background_threads():
    global _background_threads_started
    if _background_threads_started:
        return
    _background_threads_started = True
    log("🐃 ALPHA BUFFALO v5 background threads starting")
    threading.Thread(target=command_loop, daemon=True).start()
    threading.Thread(target=signal_loop, daemon=True).start()
    threading.Thread(target=_signal_loop_watchdog, daemon=True).start()

if __name__ == "__main__":
    print("🐃 ALPHA BUFFALO v5 started\n")
    port = int(os.getenv("PORT",8080))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")
