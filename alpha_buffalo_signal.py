"""
alpha_buffalo_signal.py — Alpha Buffalo v5
เพิ่ม Multi-Timeframe: 4H + 1H + 15M
เพิ่ม signal_composer (V4+ Session + V5 Sniper)

เปลี่ยนจาก v4:
  - get_ohlcv() → get_ohlcv(interval, bars) รับ TF ได้
  - signal loop → ดึง df_4h, df_1h, df_15m พร้อมกัน
  - calculate_signal() → compose_signal() จาก signal_composer
"""

import requests
import time
import threading
import pandas as pd
import os
from datetime import datetime, timezone, timedelta
from supabase import create_client, Client
from http.server import HTTPServer, BaseHTTPRequestHandler

# ── นำเข้า modules ใหม่ ───────────────────────────────────
from signal_composer import compose_signal, format_composed, kill_basket, reset_basket

# ── Config ────────────────────────────────────────────────
TWELVE_API_KEY      = os.getenv("TWELVE_API_KEY")
SUPABASE_URL        = os.getenv("SUPABASE_URL")
SUPABASE_KEY        = os.getenv("SUPABASE_KEY")
TELEGRAM_TOKEN      = os.getenv("TELEGRAM_TOKEN")
RAILWAY_WEBHOOK_URL = os.getenv("RAILWAY_WEBHOOK_URL")
ADMIN_ID            = int(os.getenv("ADMIN_ID", "0"))
TELEGRAM_API        = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
SYMBOL              = "XAU/USD"
COOLDOWN_MIN        = 10
POLL_INTERVAL       = 900   # 15 นาที (sync กับ 15M TF)
CMD_INTERVAL        = 3

# ── Timeframe Config ──────────────────────────────────────
TF_CONFIG = {
    "4h":   {"interval": "4h",   "bars": 100},
    "1h":   {"interval": "1h",   "bars": 200},
    "15min":{"interval": "15min","bars": 96},
}

# ── State ──────────────────────────────────────────────────
last_signal      = None
last_signal_time = None
last_update_id   = 0
state_lock       = threading.Lock()
BKK              = timezone(timedelta(hours=7))


def now_bkk():
    return datetime.now(BKK).strftime("%H:%M:%S")


def log(msg: str):
    print(f"{now_bkk()} | {msg}", flush=True)


# ── Supabase ──────────────────────────────────────────────
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


def load_last_signal():
    global last_signal, last_signal_time
    try:
        res = supabase.table("signals").select("*").order(
            "created_at", desc=True).limit(1).execute()
        if res.data:
            row = res.data[0]
            last_signal      = row.get("direction")
            last_signal_time = row.get("created_at")
            log(f"Loaded last_signal: {last_signal}")
    except Exception as e:
        log(f"load_last_signal error: {e}")


def save_signal(sig_dict: dict):
    try:
        supabase.table("signals").insert(sig_dict).execute()
    except Exception as e:
        log(f"save_signal error: {e}")


# ── OHLCV ─────────────────────────────────────────────────
def get_ohlcv(interval: str = "1h", bars: int = 200) -> pd.DataFrame | None:
    """ดึง OHLCV จาก TwelveData — รองรับทุก TF"""
    try:
        url = "https://api.twelvedata.com/time_series"
        params = {
            "symbol":      SYMBOL,
            "interval":    interval,
            "outputsize":  bars,
            "apikey":      TWELVE_API_KEY,
            "format":      "JSON",
        }
        r = requests.get(url, params=params, timeout=15)
        data = r.json()

        if "values" not in data:
            log(f"OHLCV {interval} error: {data.get('message','no values')}")
            return None

        df = pd.DataFrame(data["values"])
        df["datetime"] = pd.to_datetime(df["datetime"])
        df = df.sort_values("datetime").reset_index(drop=True)
        df.set_index("datetime", inplace=True)
        df.index = df.index.tz_localize("UTC")

        for col in ["open", "high", "low", "close"]:
            df[col] = df[col].astype(float)
        if "volume" in df.columns:
            df["volume"] = df["volume"].astype(float)

        return df

    except Exception as e:
        log(f"get_ohlcv({interval}) error: {e}")
        return None


# ── Market Open Check ─────────────────────────────────────
def is_market_open() -> bool:
    now = datetime.now(timezone.utc)
    # XAU/USD ปิด Sat 22:00 UTC - Sun 22:00 UTC
    if now.weekday() == 5:  # Saturday
        return now.hour < 22
    if now.weekday() == 6:  # Sunday
        return now.hour >= 22
    return True


# ── Telegram ──────────────────────────────────────────────
def send_telegram(msg: str, chat_id: int = None):
    cid = chat_id or ADMIN_ID
    try:
        requests.post(
            f"{TELEGRAM_API}/sendMessage",
            json={"chat_id": cid, "text": msg, "parse_mode": "HTML"},
            timeout=10,
        )
    except Exception as e:
        log(f"send_telegram error: {e}")


def send_signal(sig):
    global last_signal, last_signal_time
    with state_lock:
        last_signal      = sig.direction
        last_signal_time = datetime.now(BKK).isoformat()

    msg = format_composed(sig)
    send_telegram(msg)
    log(f"Signal sent: {sig.direction} Score:{sig.confluence_score}")

    save_signal({
        "direction":        sig.direction,
        "signal_type":      sig.signal_type,
        "entry_price":      sig.entry_price,
        "sl_price":         sig.sl_price,
        "tp1_price":        sig.tp1_price,
        "tp2_price":        sig.tp2_price,
        "lot_multiplier":   sig.lot_multiplier,
        "basket_layer":     sig.basket_layer,
        "confluence_score": sig.confluence_score,
        "sources":          ", ".join(sig.sources),
        "created_at":       last_signal_time,
    })


# ── Cooldown Check ────────────────────────────────────────
def can_send_signal(direction: str) -> bool:
    global last_signal, last_signal_time
    if last_signal_time is None:
        return True
    try:
        last_dt  = datetime.fromisoformat(last_signal_time)
        elapsed  = (datetime.now(BKK) - last_dt).total_seconds() / 60
        # ถ้า direction เดิม ต้อง cooldown
        if last_signal == direction and elapsed < COOLDOWN_MIN:
            return False
    except Exception:
        pass
    return True


# ── Signal Loop ───────────────────────────────────────────
def signal_loop():
    log("📡 Signal loop started (15m)")
    while True:
        try:
            if not is_market_open():
                log("🔴 ตลาดปิด")
                time.sleep(POLL_INTERVAL)
                continue

            # ดึงทั้ง 3 TF
            log("⏳ Fetching 4H / 1H / 15M...")
            df_4h  = get_ohlcv("4h",    TF_CONFIG["4h"]["bars"])
            df_1h  = get_ohlcv("1h",    TF_CONFIG["1h"]["bars"])
            df_15m = get_ohlcv("15min", TF_CONFIG["15min"]["bars"])

            if df_4h is None or df_1h is None or df_15m is None:
                log("⚠️ ดึงข้อมูลไม่ครบ")
                time.sleep(POLL_INTERVAL)
                continue

            price = float(df_15m["close"].iloc[-1])
            log(f"💰 XAUUSD: {price:,.2f}")

            # Compose signal
            sig = compose_signal(df_4h, df_1h, df_15m)

            if sig:
                if can_send_signal(sig.direction):
                    send_signal(sig)
                else:
                    log(f"⏳ Cooldown | {sig.direction} Score:{sig.confluence_score}")
            else:
                log(f"⏳ No signal | Price:{price:,.2f}")

        except Exception as e:
            log(f"signal_loop error: {e}")

        time.sleep(POLL_INTERVAL)


# ── Command Loop (Telegram Bot) ───────────────────────────
def command_loop():
    global last_update_id
    log("🤖 Command loop started (3s)")
    while True:
        try:
            r = requests.get(
                f"{TELEGRAM_API}/getUpdates",
                params={
                    "offset":          last_update_id + 1,
                    "timeout":         2,
                    "allowed_updates": ["message"],
                },
                timeout=10,
            )
            data = r.json()

            # reset offset ถ้า Telegram ตอบ error
            if not data.get("ok"):
                log(f"getUpdates error: {data.get('description','unknown')} — reset offset")
                last_update_id = 0
                time.sleep(5)
                continue

            updates = data.get("result", [])

            # อัพเดท offset ทุกครั้งแม้ไม่มี message
            if updates:
                last_update_id = updates[-1]["update_id"]
                for upd in updates:
                    msg  = upd.get("message", {})
                    text = msg.get("text", "").strip()
                    cid  = msg.get("chat", {}).get("id")
                    if text and cid:
                        handle_command(text, cid)

        except requests.exceptions.Timeout:
            pass  # timeout ปกติ ไม่ต้อง log
        except Exception as e:
            log(f"command_loop error: {e}")
            time.sleep(10)  # รอนานขึ้นถ้า error
        time.sleep(CMD_INTERVAL)


def handle_command(text: str, chat_id: int):
    t = text.lower()
    if t == "/start":
        send_telegram("🐃 Alpha Buffalo v5 Online", chat_id)
    elif t == "/status":
        msg = (
            f"🐃 Alpha Buffalo v5\n"
            f"Last: {last_signal} @ {last_signal_time}\n"
            f"Market: {'🟢 Open' if is_market_open() else '🔴 Closed'}"
        )
        send_telegram(msg, chat_id)
    elif t == "/reset_buy":
        reset_basket("BUY")
        send_telegram("✅ BUY Basket Reset", chat_id)
    elif t == "/reset_sell":
        reset_basket("SELL")
        send_telegram("✅ SELL Basket Reset", chat_id)
    elif t in ("/help", "/?"):
        send_telegram(
            "/status — สถานะ bot\n"
            "/reset_buy — reset BUY basket\n"
            "/reset_sell — reset SELL basket",
            chat_id,
        )


# ── Health Check Server ────────────────────────────────────
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")
    def log_message(self, *args):
        pass


# ── Main ───────────────────────────────────────────────────
if __name__ == "__main__":
    print("🐃 ALPHA BUFFALO Signal Bot v5 started\n")
    load_last_signal()
    threading.Thread(target=command_loop, daemon=True).start()
    threading.Thread(target=signal_loop,  daemon=True).start()
    port = int(os.getenv("PORT", 8080))
    HTTPServer(("0.0.0.0", port), HealthHandler).serve_forever()
