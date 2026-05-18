import requests
import time
import threading
import pandas as pd
import os
from datetime import datetime, timezone, timedelta
from supabase import create_client, Client

# âââ Config (Railway env vars) ââââââââââââââââââââââââââââ
TWELVE_API_KEY      = os.getenv("TWELVE_API_KEY")
SUPABASE_URL        = os.getenv("SUPABASE_URL")
SUPABASE_KEY        = os.getenv("SUPABASE_KEY")
TELEGRAM_TOKEN      = os.getenv("TELEGRAM_TOKEN")
RAILWAY_WEBHOOK_URL = os.getenv("RAILWAY_WEBHOOK_URL")
ADMIN_ID            = int(os.getenv("ADMIN_ID", "0"))
TELEGRAM_API        = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
SYMBOL              = "XAU/USD"
COOLDOWN_MIN        = 10
POLL_INTERVAL       = 120  # signal loop (à¸§à¸´à¸à¸²à¸à¸µ)
CMD_INTERVAL        = 3    # command loop (à¸§à¸´à¸à¸²à¸à¸µ)

# âââ State ââââââââââââââââââââââââââââââââââââââââââââââââ
last_signal      = None
last_signal_time = None
last_update_id   = 0
state_lock       = threading.Lock()
BKK              = timezone(timedelta(hours=7))

def now_bkk():
        return datetime.now(BKK)

def log(msg):
        print(f"{now_bkk().strftime('%H:%M:%S')} | {msg}", flush=True)

# âââ Supabase âââââââââââââââââââââââââââââââââââââââââââââ
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY) \
    if SUPABASE_URL and SUPABASE_KEY else None

# âââ bot_status table (persist last_signal) âââââââââââââââ
def load_last_signal():
        """à¹à¸«à¸¥à¸ last_signal à¸à¸²à¸ Supabase à¹à¸¡à¸·à¹à¸­ restart"""
        global last_signal, last_signal_time
        if not supabase:
                    return
                try:
                            res = supabase.table("bot_status").select("*").eq("id", 1).single().execute()
                            if res.data:
                                            last_signal = res.data.get("last_signal")
                                            ts = res.data.get("last_signal_time")
                                            if ts:
                                                                last_signal_time = datetime.fromisoformat(ts)
                                                            log(f"ð Loaded last_signal: {last_signal}")
                except Exception as e:
                            log(f"load_last_signal: {e}")

def save_last_signal(action: str):
        if not supabase:
                    return
                try:
        supabase.table("bot_status").upsert({
                                "id": 1,
                                "last_signal": action,
                                "last_signal_time": now_bkk().isoformat()
                }, on_conflict="id").execute()
except Exception as e:
        log(f"save_last_signal error: {e}")

# âââ Market Hours ââââââââââââââââââââââââââââââââââââââââââ
def is_market_open():
        now = datetime.now(timezone.utc)
    wd  = now.weekday()
    if wd == 5: return now.hour < 22
            if wd == 6: return now.hour >= 22
                    return True

# âââ Access Control ââââââââââââââââââââââââââââââââââââââââ
def get_user_role(user_id: int) -> str:
        if not supabase:
                    return "admin" if user_id == ADMIN_ID else "guest"
                try:
                            res = supabase.table("bot_users") \
                                .select("role, is_active") \
                                .eq("id", user_id).single().execute()
                            if res.data and res.data.get("is_active"):
                                            return res.data["role"]
                except Exception as e:
                            log(f"get_user_role error: {e} â fallback hardcode")
                            return "admin" if user_id == ADMIN_ID else "guest"
                        return "guest"

def is_admin(uid):  return get_user_role(uid) == "admin"
    def is_member(uid): return get_user_role(uid) in ("admin", "member")

def add_user(user_id, username, role, approved_by):
        if not supabase:
                    return False
                try:
                            supabase.table("bot_users").upsert({
                                            "id": user_id, "username": username, "role": role,
                                            "approved_at": now_bkk().isoformat(),
                        "approved_by": approved_by, "is_active": True
                            }, on_conflict="id").execute()
                            return True
except Exception as e:
        log(f"add_user error: {e}")
        return False

def deactivate_user(user_id):
        if not supabase:
                    return False
                try:
                            supabase.table("bot_users") \
                                .update({"is_active": False}).eq("id", user_id).execute()
                            return True
except Exception as e:
        log(f"deactivate_user error: {e}")
        return False

def get_all_members():
        if not supabase:
                    return []
                try:
                            res = supabase.table("bot_users") \
                                .select("id, username, role") \
                                .eq("is_active", True).execute()
                            return res.data or []
except Exception as e:
        log(f"get_all_members error: {e}")
        return []

# âââ Telegram âââââââââââââââââââââââââââââââââââââââââââââ
def send_message(chat_id, text):
        try:
                    requests.post(f"{TELEGRAM_API}/sendMessage", json={
                                    "chat_id": chat_id, "text": text, "parse_mode": "HTML"
                    }, timeout=10)
except Exception as e:
        log(f"send_message error: {e}")

def get_updates():
        global last_update_id
    try:
                r = requests.get(f"{TELEGRAM_API}/getUpdates", params={
                                "offset": last_update_id + 1, "timeout": 2
                }, timeout=8)
                return r.json().get("result", [])
except Exception as e:
        log(f"get_updates error: {e}")
        return []

# âââ Command Handler âââââââââââââââââââââââââââââââââââââââ
def handle_commands():
        global last_update_id
    for update in get_updates():
                last_update_id = update["update_id"]
                msg = update.get("message") or update.get("edited_message")
                if not msg:
                                continue
                            text     = msg.get("text", "").strip()
        chat_id  = msg["chat"]["id"]
        user     = msg.get("from", {})
        user_id  = user.get("id", 0)
        username = user.get("username", str(user_id))

        if not text.startswith("/"):
                        continue

        parts = text.split()
        cmd   = parts[0].lower().split("@")[0]
        arg   = parts[1] if len(parts) > 1 else ""

        log(f"CMD: {cmd} from @{username} ({user_id})")

        # /start
        if cmd == "/start":
                        role = get_user_role(user_id)
                        if role == "guest":
                                            send_message(chat_id,
                                                                             f"ð à¸ªà¸§à¸±à¸ªà¸à¸µ @{username}!\n"
                                                                             f"à¸¢à¸±à¸à¹à¸¡à¹à¹à¸à¹à¸£à¸±à¸à¸ªà¸´à¸à¸à¸´à¹ â à¸à¸´à¸à¸à¹à¸­ Admin à¹à¸à¸·à¹à¸­à¸à¸­ Approve")
                                            send_message(ADMIN_ID,
                                                f"ð à¸à¸¹à¹à¹à¸à¹à¹à¸«à¸¡à¹: @{username} (ID: {user_id})\n"
                                                f"à¸à¸´à¸¡à¸à¹ /approve {user_id} {username} à¹à¸à¸·à¹à¸­à¸­à¸à¸¸à¸¡à¸±à¸à¸´")
        else:
                send_message(chat_id, f"â à¸¢à¸´à¸à¸à¸µà¸à¹à¸­à¸à¸£à¸±à¸ @{username}! (Role: {role})")

        # /help
elif cmd == "/help":
            role = get_user_role(user_id)
            if role == "guest":
                                send_message(chat_id, "â à¹à¸¡à¹à¸¡à¸µà¸ªà¸´à¸à¸à¸´à¹à¹à¸à¹à¸à¸²à¸")
elif role == "member":
                send_message(chat_id,
                                                 "ð <b>Alpha Buffalo Bot</b>\n\n"
                                                 "/price â à¸£à¸²à¸à¸² XAUUSD\n"
                                                 "/signal â signal à¸¥à¹à¸²à¸ªà¸¸à¸\n"
                                                 "/help â à¸à¸³à¸ªà¸±à¹à¸")
else:
                send_message(chat_id,
                                                 "ð <b>Alpha Buffalo Bot â Admin</b>\n\n"
                                                 "/price /signal\n"
                                                 "/approve &lt;id&gt; &lt;username&gt;\n"
                                                 "/kick &lt;id&gt;\n"
                                                 "/members\n"
                                                 "/status\n"
                                                 "/cb on|off")

        # /price
elif cmd == "/price":
            if not is_member(user_id):
                                send_message(chat_id, "â à¹à¸¡à¹à¸¡à¸µà¸ªà¸´à¸à¸à¸´à¹")
                                continue
                            prices = get_prices()
            if prices:
                                send_message(chat_id, f"ð° XAUUSD: <b>{prices[-1]:,.2f}</b> USD")
else:
                send_message(chat_id, "â à¸à¸¶à¸à¸£à¸²à¸à¸²à¹à¸¡à¹à¹à¸à¹")

        # /signal
elif cmd == "/signal":
            if not is_member(user_id):
                                send_message(chat_id, "â à¹à¸¡à¹à¸¡à¸µà¸ªà¸´à¸à¸à¸´à¹")
                                continue
                            with state_lock:
                                                s, t = last_signal, last_signal_time
                                            if s and t:
                                                                elapsed = (now_bkk() - t).total_seconds() / 60
                                                                send_message(chat_id,
                                                                    f"ð Signal à¸¥à¹à¸²à¸ªà¸¸à¸: <b>{s}</b>\nâ± {elapsed:.0f} à¸à¸²à¸à¸µà¸à¸µà¹à¹à¸¥à¹à¸§")
else:
                send_message(chat_id, "ð à¸¢à¸±à¸à¹à¸¡à¹à¸¡à¸µ signal")

        # /approve
elif cmd == "/approve":
            if not is_admin(user_id):
                                send_message(chat_id, "â Admin à¹à¸à¹à¸²à¸à¸±à¹à¸")
                                continue
                            if not arg:
                                                send_message(chat_id, "â /approve &lt;id&gt; &lt;username&gt;")
                                                continue
                                            try:
                                                                tid  = int(arg)
                                                                name = parts[2] if len(parts) > 2 else str(tid)
                                                                if add_user(tid, name, "member", user_id):
                                                                                        send_message(chat_id, f"â Approved @{name}")
                                                                                        send_message(tid, "â à¸à¸¸à¸à¹à¸à¹à¸£à¸±à¸à¸ªà¸´à¸à¸à¸´à¹à¹à¸à¹à¸à¸²à¸ Alpha Buffalo Bot à¹à¸¥à¹à¸§!")
                                            else:
                    send_message(chat_id, "â à¸à¸±à¸à¸à¸¶à¸à¹à¸¡à¹à¸ªà¸³à¹à¸£à¹à¸")
                                            except ValueError:
                                                                send_message(chat_id, "â id à¸à¹à¸­à¸à¹à¸à¹à¸à¸à¸±à¸§à¹à¸¥à¸")

        # /kick
elif cmd == "/kick":
            if not is_admin(user_id):
                                send_message(chat_id, "â Admin à¹à¸à¹à¸²à¸à¸±à¹à¸")
                                continue
                            try:
                                                tid = int(arg)
                                                if tid == ADMIN_ID:
                                                                        send_message(chat_id, "â à¹à¸¡à¹à¸ªà¸²à¸¡à¸²à¸£à¸ kick Admin")
                                                                        continue
                                                                    if deactivate_user(tid):
                                                                                            send_message(chat_id, f"â à¸à¸­à¸à¸ªà¸´à¸à¸à¸´à¹ {tid} à¹à¸¥à¹à¸§")
else:
                    send_message(chat_id, "â à¸à¸­à¸à¸ªà¸´à¸à¸à¸´à¹à¹à¸¡à¹à¸ªà¸³à¹à¸£à¹à¸")
except ValueError:
                send_message(chat_id, "â id à¸à¹à¸­à¸à¹à¸à¹à¸à¸à¸±à¸§à¹à¸¥à¸")

        # /members
elif cmd == "/members":
            if not is_admin(user_id):
                                send_message(chat_id, "â Admin à¹à¸à¹à¸²à¸à¸±à¹à¸")
                continue
            members = get_all_members()
            if not members:
                                send_message(chat_id, "ð à¸¢à¸±à¸à¹à¸¡à¹à¸¡à¸µà¸ªà¸¡à¸²à¸à¸´à¸")
else:
                lines = ["ð <b>à¸ªà¸¡à¸²à¸à¸´à¸:</b>"]
                for m in members:
                                        lines.append(f"â¢ @{m['username']} ({m['id']}) â {m['role']}")
                send_message(chat_id, "\n".join(lines))

        # /status
elif cmd == "/status":
            if not is_admin(user_id):
                                send_message(chat_id, "â Admin à¹à¸à¹à¸²à¸à¸±à¹à¸")
                continue
            with state_lock:
                                s, t = last_signal, last_signal_time
            market = "â à¹à¸à¸´à¸" if is_market_open() else "ð à¸à¸´à¸"
            cd = ""
            if t:
                                rem = max(0, COOLDOWN_MIN - (now_bkk()-t).total_seconds()/60)
                cd = f"\nâ¸ Cooldown: {rem:.0f} à¸à¸²à¸à¸µ" if rem > 0 else ""
            db = "â" if supabase else "â Supabase à¸à¸²à¸§à¸à¹"
            send_message(chat_id,
                                         f"ð <b>Bot Status</b>\n"
                                         f"à¸à¸¥à¸²à¸: {market}\n"
                                         f"DB: {db}\n"
                                         f"Signal à¸¥à¹à¸²à¸ªà¸¸à¸: {s or 'à¸¢à¸±à¸à¹à¸¡à¹à¸¡à¸µ'}{cd}")

        # /buy /sell (blocked)
elif cmd in ("/buy", "/sell"):
            send_message(chat_id,
                                         "â ï¸ /buy /sell à¸ªà¹à¸à¹à¸à¸¢ Bot à¸­à¸±à¸à¹à¸à¸¡à¸±à¸à¸´à¹à¸à¹à¸²à¸à¸±à¹à¸\nà¸à¸¹à¸à¸µà¹ /signal")

        # /cb
elif cmd == "/cb":
            if not is_admin(user_id):
                                send_message(chat_id, "â Admin à¹à¸à¹à¸²à¸à¸±à¹à¸")
                continue
            send_message(chat_id, "âï¸ Circuit Breaker: coming soon")

# âââ Price & Signal ââââââââââââââââââââââââââââââââââââââââ
def get_prices():
        try:
                    url = (f"https://api.twelvedata.com/time_series"
                                          f"?symbol={SYMBOL}&interval=1min&outputsize=50"
                                          f"&apikey={TWELVE_API_KEY}")
                    data = requests.get(url, timeout=10).json()
                    if "values" in data:
                                    return [float(v["close"]) for v in reversed(data["values"])]
                                log(f"Twelve Data error: {data.get('message', 'unknown')}")
except Exception as e:
            log(f"get_prices error: {e}")
        return None

def calculate_signal(prices):
        if len(prices) < 26:
                    return None
                try:
                            close = pd.Series(prices)
                            ema9  = close.ewm(span=9).mean().iloc[-1]
                            ema21 = close.ewm(span=21).mean().iloc[-1]
                            delta = close.diff()
                            gain  = delta.clip(lower=0).rolling(14).mean()
                            loss  = (-delta.clip(upper=0)).rolling(14).mean()
                            rsi   = (100 - 100 / (1 + gain / loss)).iloc[-1]
                            score = 80  # TODO: replace with real Fibonacci score calculation
        if ema9 > ema21 and 50 < rsi < 75:
                        return {"action": "BUY",  "score": score, "rsi": rsi}
                    if ema9 < ema21 and 25 < rsi < 50:
                                    return {"action": "SELL", "score": score, "rsi": rsi}
except Exception as e:
        log(f"calculate_signal error: {e}")
    return None

def send_signal(signal, price):
        global last_signal, last_signal_time
    with state_lock:
                if last_signal_time:
                                diff = (now_bkk() - last_signal_time).total_seconds() / 60
                                if diff < COOLDOWN_MIN:
                                                    log(f"â¸ Cooldown {COOLDOWN_MIN - diff:.1f} à¸à¸²à¸à¸µ")
                                                    return
                                            if last_signal == signal["action"]:
                                                            log("â¸ Same direction â skip")
                                                            return
    try:
                r = requests.post(RAILWAY_WEBHOOK_URL, json={
                                "symbol":     "XAUUSD",
                                "action":     signal["action"],
                                "price":      price,
                                "lot":        0.1,
                                "fibo_score": signal["score"],
                                "source":     "python_bot"
                }, timeout=10)
                if r.json().get("status") == "ok":
                                with state_lock:
                                                    last_signal      = signal["action"]
                                                    last_signal_time = now_bkk()
                                                save_last_signal(signal["action"])
                                log(f"â {signal['action']} | RSI:{signal['rsi']:.1f} | Price:{price:,.2f}")
    else:
            log(f"send_signal response: {r.text}")
    except Exception as e:
        log(f"send_signal error: {e}")

# âââ Threads ââââââââââââââââââââââââââââââââââââââââââââââ
def command_loop():
        log("ð¤ Command loop started (3s)")
    while True:
                try:
                                handle_commands()
except Exception as e:
            log(f"command_loop error: {e}")
        time.sleep(CMD_INTERVAL)

def signal_loop():
        log(f"ð¡ Signal loop started ({POLL_INTERVAL}s)")
    while True:
                try:
                                if not is_market_open():
                                                    log("ð à¸à¸¥à¸²à¸à¸à¸´à¸")
                                                    time.sleep(POLL_INTERVAL)
                                                    continue
                                                prices = get_prices()
                                if prices:
                                                    signal = calculate_signal(prices)
                                                    if signal:
                                                                            send_signal(signal, prices[-1])
                                else:
                                                        log(f"â³ No signal | Price:{prices[-1]:,.2f}")
                else:
                                    log("â ï¸ à¸à¸¶à¸à¸£à¸²à¸à¸²à¹à¸¡à¹à¹à¸à¹")
except Exception as e:
            log(f"signal_loop error: {e}")
        time.sleep(POLL_INTERVAL)

# âââ Main âââââââââââââââââââââââââââââââââââââââââââââââââ
if __name__ == "__main__":
        print("ð ALPHA BUFFALO Signal Bot v4 started\n")
    load_last_signal()
    threading.Thread(target=command_loop, daemon=True).start()
    threading.Thread(target=signal_loop,  daemon=True).start()
    while True:
                time.sleep(60)
