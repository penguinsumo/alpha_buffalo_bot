import requests
import time
import threading
import pandas as pd
import os
from datetime import datetime, timezone, timedelta
from supabase import create_client, Client
from pivot_engine_v2 import PivotEngine, BasketState

# ── Config (Railway env vars) ──────────────────────────────
TWELVE_API_KEY      = os.getenv("TWELVE_API_KEY")
SUPABASE_URL        = os.getenv("SUPABASE_URL")
SUPABASE_KEY        = os.getenv("SUPABASE_KEY")
TELEGRAM_TOKEN      = os.getenv("TELEGRAM_TOKEN")
RAILWAY_WEBHOOK_URL = os.getenv("RAILWAY_WEBHOOK_URL")
ADMIN_ID            = int(os.getenv("ADMIN_ID", "0"))
TELEGRAM_API        = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
SYMBOL              = "XAU/USD"
COOLDOWN_MIN        = 60
POLL_INTERVAL       = 120
CMD_INTERVAL        = 3

# ── State ──────────────────────────────────────────────────
last_signal      = None
last_signal_time = None
last_update_id   = 0
state_lock       = threading.Lock()
BKK              = timezone(timedelta(hours=7))

# ── Pivot Engine ───────────────────────────────────────────
pivot_engine = PivotEngine(left=5, right=5)
basket_state = BasketState()


def now_bkk():
    return datetime.now(BKK)


def log(msg):
    print(f"{now_bkk().strftime('%H:%M:%S')} | {msg}", flush=True)


# ── Supabase ───────────────────────────────────────────────
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY) \
    if SUPABASE_URL and SUPABASE_KEY else None


# ── bot_status table (persist last_signal) ─────────────────
def load_last_signal():
    """Load last_signal from Supabase on restart"""
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
            log(f"Loaded last_signal: {last_signal}")
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


# ── Market Hours ───────────────────────────────────────────
def is_market_open():
    now = datetime.now(timezone.utc)
    wd  = now.weekday()
    if wd == 5: return now.hour < 22
    if wd == 6: return now.hour >= 22
    return True


# ── Access Control ─────────────────────────────────────────
def get_user_role(user_id: int) -> str:
    if not supabase:
        return "admin" if user_id == ADMIN_ID else "guest"
    try:
        res = (supabase.table("bot_users")
               .select("role, is_active")
               .eq("id", user_id).single().execute())
        if res.data and res.data.get("is_active"):
            return res.data["role"]
        return "guest"
    except Exception as e:
        log(f"get_user_role error: {e}")
        return "admin" if user_id == ADMIN_ID else "guest"


def is_admin(uid):  return get_user_role(uid) == "admin"
def is_member(uid): return get_user_role(uid) in ("admin", "member")


def add_user(user_id, username, role, approved_by):
    if not supabase:
        return False
    try:
        supabase.table("bot_users").upsert({
            "id": user_id,
            "username": username,
            "role": role,
            "approved_at": now_bkk().isoformat(),
            "approved_by": approved_by,
            "is_active": True
        }, on_conflict="id").execute()
        return True
    except Exception as e:
        log(f"add_user error: {e}")
        return False


def deactivate_user(user_id):
    if not supabase:
        return False
    try:
        supabase.table("bot_users").update(
            {"is_active": False}).eq("id", user_id).execute()
        return True
    except Exception as e:
        log(f"deactivate_user error: {e}")
        return False


def get_all_members():
    if not supabase:
        return []
    try:
        res = (supabase.table("bot_users")
               .select("id, username, role")
               .eq("is_active", True).execute())
        return res.data or []
    except Exception as e:
        log(f"get_all_members error: {e}")
        return []


# ── Telegram ───────────────────────────────────────────────
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
        updates = r.json().get("result", [])
        if updates:
            log(f"📨 Got {len(updates)} updates")
        return updates
    except Exception as e:
        log(f"get_updates error: {e}")
        return []


# ── Command Handler ────────────────────────────────────────
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
                    f"👋 สวัสดี @{username}!\n"
                    f"ยังไม่ได้รับสิทธิ์ → ติดต่อ Admin เพื่อขอ Approve")
                send_message(ADMIN_ID,
                    f"🔔 ผู้ใช้ใหม่: @{username} (ID: {user_id})\n"
                    f"พิมพ์ /approve {user_id} {username} เพื่ออนุมัติ")
            else:
                send_message(chat_id, f"✅ ยินดีต้อนรับ @{username}! (Role: {role})")

        # /help
        elif cmd == "/help":
            role = get_user_role(user_id)
            if role == "guest":
                send_message(chat_id, "❌ ไม่มีสิทธิ์เข้าถึง")
            elif role == "member":
                send_message(chat_id,
                    "📋 <b>Alpha Buffalo Bot</b>\n\n"
                    "/price — ราคา XAUUSD\n"
                    "/signal — signal ล่าสุด\n"
                    "/help — คำสั่ง")
            else:
                send_message(chat_id,
                    "📋 <b>Alpha Buffalo Bot — Admin</b>\n\n"
                    "/price /signal\n"
                    "/approve &lt;id&gt; &lt;username&gt;\n"
                    "/kick &lt;id&gt;\n"
                    "/members\n"
                    "/status\n"
                    "/cb on|off")

        # /price
        elif cmd == "/price":
            if not is_member(user_id):
                send_message(chat_id, "❌ ไม่มีสิทธิ์")
                continue
            prices = get_prices()
            if prices:
                send_message(chat_id, f"💰 XAUUSD: <b>{prices[-1]:,.2f}</b> USD")
            else:
                send_message(chat_id, "❌ ดึงราคาไม่ได้")

        # /signal
        elif cmd == "/signal":
            if not is_member(user_id):
                send_message(chat_id, "❌ ไม่มีสิทธิ์")
                continue
            with state_lock:
                s, t = last_signal, last_signal_time
            if s and t:
                elapsed = (now_bkk() - t).total_seconds() / 60
                send_message(chat_id,
                    f"📊 Signal ล่าสุด: <b>{s}</b>\n⏱ {elapsed:.0f} นาทีที่แล้ว")
            else:
                send_message(chat_id, "📭 ยังไม่มี signal")

        # /approve
        elif cmd == "/approve":
            if not is_admin(user_id):
                send_message(chat_id, "❌ Admin เท่านั้น")
                continue
            if not arg:
                send_message(chat_id, "❌ /approve &lt;id&gt; &lt;username&gt;")
                continue
            try:
                tid  = int(arg)
                name = parts[2] if len(parts) > 2 else str(tid)
                if add_user(tid, name, "member", user_id):
                    send_message(chat_id, f"✅ Approved @{name}")
                    send_message(tid, "✅ คุณได้รับสิทธิ์เข้าถึง Alpha Buffalo Bot แล้ว!")
                else:
                    send_message(chat_id, "❌ บันทึกไม่สำเร็จ")
            except ValueError:
                send_message(chat_id, "❌ id ต้องเป็นตัวเลข")

        # /kick
        elif cmd == "/kick":
            if not is_admin(user_id):
                send_message(chat_id, "❌ Admin เท่านั้น")
                continue
            try:
                tid = int(arg)
                if tid == ADMIN_ID:
                    send_message(chat_id, "❌ ไม่สามารถ kick Admin")
                    continue
                if deactivate_user(tid):
                    send_message(chat_id, f"✅ ถอนสิทธิ์ {tid} แล้ว")
                else:
                    send_message(chat_id, "❌ ถอนสิทธิ์ไม่สำเร็จ")
            except ValueError:
                send_message(chat_id, "❌ id ต้องเป็นตัวเลข")

        # /members
        elif cmd == "/members":
            if not is_admin(user_id):
                send_message(chat_id, "❌ Admin เท่านั้น")
                continue
            members = get_all_members()
            if not members:
                send_message(chat_id, "📭 ยังไม่มีสมาชิก")
            else:
                lines = ["👥 <b>สมาชิก:</b>"]
                for m in members:
                    lines.append(f"• @{m['username']} ({m['id']}) — {m['role']}")
                send_message(chat_id, "\n".join(lines))

        # /status
        elif cmd == "/status":
            if not is_admin(user_id):
                send_message(chat_id, "❌ Admin เท่านั้น")
                continue
            with state_lock:
                s, t = last_signal, last_signal_time
            market = "✅ เปิด" if is_market_open() else "🔴 ปิด"
            cd = ""
            if t:
                rem = max(0, COOLDOWN_MIN - (now_bkk() - t).total_seconds() / 60)
                cd = f"\n⏸ Cooldown: {rem:.0f} นาที" if rem > 0 else ""
            db = "✅" if supabase else "❌ Supabase ดาวน์"
            send_message(chat_id,
                f"📊 <b>Bot Status</b>\n"
                f"ตลาด: {market}\n"
                f"DB: {db}\n"
                f"Signal ล่าสุด: {s or 'ยังไม่มี'}{cd}")

        # /buy /sell (blocked)
        elif cmd in ("/buy", "/sell"):
            send_message(chat_id,
                "⚠️ /buy /sell ส่งโดย Bot อัตโนมัติเท่านั้น\nดูที่ /signal")

        # /cb
        elif cmd == "/cb":
            if not is_admin(user_id):
                send_message(chat_id, "❌ Admin เท่านั้น")
                continue
            send_message(chat_id, "⚙️ Circuit Breaker: coming soon")


# ── Price & Signal ─────────────────────────────────────────
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
    """
    v5: ใช้ PivotEngine แทน rolling window
    คืน signal dict หรือ None
    """
    if len(prices) < 30:
        return None
    try:
        # สร้าง DataFrame สำหรับ engine
        df = pd.DataFrame({
            "open":   prices,
            "high":   [p * 1.0002 for p in prices],
            "low":    [p * 0.9998 for p in prices],
            "close":  prices,
            "volume": [1000.0] * len(prices),
        })

        # update pivot engine
        state, basket = pivot_engine.update(df, basket_state)

        # ต้องมี pivot ก่อน
        if not state.is_ready():
            log("⏳ Pivot not ready yet")
            return None

        # BOS → skip
        if state.bos_detected:
            log("⚡ BOS detected — skip signal")
            return None

        # sideways → skip
        if state.trend_dir == "sideways":
            log("↔️ Sideways — skip signal")
            return None

        # confluence threshold
        if state.confluence < 70:
            log(f"📊 Confluence {state.confluence}/100 — below threshold")
            return None

        # map trend → action
        action = "BUY" if state.trend_dir == "up" else "SELL"

        return {
            "action":     action,
            "score":      state.confluence,
            "rsi":        0.0,
            "sl":         state.sl,
            "tp1":        state.tp1,
            "tp2":        state.tp2,
            "fibo_ratio": state.fibo_ratio,
            "trend_dir":  state.trend_dir,
            "in_deep":    state.in_deep_zone,
        }
    except Exception as e:
        log(f"calculate_signal error: {e}")
    return None


def send_signal(signal, price):
    global last_signal, last_signal_time
    with state_lock:
        if last_signal_time:
            diff = (now_bkk() - last_signal_time).total_seconds() / 60
            if diff < COOLDOWN_MIN:
                log(f"⏸ Cooldown {COOLDOWN_MIN - diff:.1f} นาที")
                return
        if last_signal == signal["action"]:
            log("⏸ Same direction — skip")
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
            log(f"✅ {signal['action']} | RSI:{signal['rsi']:.1f} | Price:{price:,.2f}")
            # save ลง Supabase signals table
            if supabase:
                try:
                    sl  = signal.get("sl")  or round(price + 8  if signal["action"] == "SELL" else price - 8,  2)
                    tp1 = signal.get("tp1") or round(price - 15 if signal["action"] == "SELL" else price + 15, 2)
                    supabase.table("signals").insert({
                        "symbol":           "XAUUSD",
                        "action":           signal["action"],
                        "price":            price,
                        "lot":              0.1,
                        "fibo_score":       signal["score"],
                        "confluence_score": signal["score"],
                        "sl":               sl,
                        "tp":               tp1,
                        "source":           "python_bot_v5",
                        "status":           "active"
                    }).execute()
                    log(f"📝 Saved signal v5 | score:{signal['score']} sl:{sl} tp:{tp1}")
                except Exception as e:
                    log(f"save signal error: {e}")
        else:
            log(f"send_signal response: {r.text}")
    except Exception as e:
        log(f"send_signal error: {e}")


# ── Threads ────────────────────────────────────────────────
def command_loop():
    log("🤖 Command loop started (3s)")
    while True:
        try:
            handle_commands()
        except Exception as e:
            log(f"command_loop error: {e}")
        time.sleep(CMD_INTERVAL)


def signal_loop():
    log(f"📡 Signal loop started ({POLL_INTERVAL}s)")
    while True:
        try:
            if not is_market_open():
                log("🔴 ตลาดปิด")
                time.sleep(POLL_INTERVAL)
                continue
            prices = get_prices()
            if prices:
                signal = calculate_signal(prices)
                if signal:
                    send_signal(signal, prices[-1])
                else:
                    log(f"⏳ No signal | Price:{prices[-1]:,.2f}")
            else:
                log("⚠️ ดึงราคาไม่ได้")
        except Exception as e:
            log(f"signal_loop error: {e}")
        time.sleep(POLL_INTERVAL)


# ── Main ───────────────────────────────────────────────────
if __name__ == "__main__":
    print("🐃 ALPHA BUFFALO Signal Bot v4 started\n")
    load_last_signal()
    threading.Thread(target=command_loop, daemon=True).start()
    threading.Thread(target=signal_loop,  daemon=True).start()
    while True:
        time.sleep(60)
