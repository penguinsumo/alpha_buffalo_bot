import requests, time, pandas as pd, os
from dotenv import load_dotenv
from datetime import datetime, timezone, timedelta
import json

load_dotenv(os.path.expanduser("~/.env.alpha"))

TWELVE_API_KEY = os.getenv("TWELVE_API_KEY")
SUPABASE_URL   = os.getenv("SUPABASE_URL")
SUPABASE_KEY   = os.getenv("SUPABASE_KEY")

SYMBOL          = "XAU/USD"
MIN_CONFLUENCE  = 75
INTERVAL        = "15min"
CHECK_EVERY_SEC = 60

# ── Circuit Breaker state ──────────────────────────────────
cb_state            = "CLOSED"   # CLOSED / OPEN / HALF_OPEN
cb_consecutive_losses = 0
cb_daily_loss       = 0.0
cb_opened_at        = None
cb_last_reset       = datetime.now(timezone.utc).date()

CB_MAX_LOSSES    = 3
CB_MAX_DAILY_LOSS = 3.0
CB_COOLDOWN_SEC  = 3600

# ── Active signals being monitored ────────────────────────
active_signals = []   # list of {id, action, ninja_sl, hard_sl, entry_price}

last_signal_time = None

# ──────────────────────────────────────────────────────────
# SUPABASE HELPERS
# ──────────────────────────────────────────────────────────

def sb_headers():
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation"
    }

def insert_signal(payload):
    """Insert signal → return signal id"""
    try:
        r = requests.post(
            f"{SUPABASE_URL}/rest/v1/trading_signals",
            headers=sb_headers(),
            json=payload,
            timeout=10
        )
        data = r.json()
        if isinstance(data, list) and len(data) > 0:
            sig_id = data[0].get("id")
            print(f"[SUPABASE] Signal inserted id={sig_id}")
            return sig_id
    except Exception as e:
        print(f"[SUPABASE ERROR] insert: {e}")
    return None

def update_signal_status(sig_id, status):
    """Set status = CLOSED / OPEN on a signal"""
    try:
        requests.patch(
            f"{SUPABASE_URL}/rest/v1/trading_signals?id=eq.{sig_id}",
            headers=sb_headers(),
            json={"status": status},
            timeout=10
        )
        print(f"[SUPABASE] Signal {sig_id} → {status}")
    except Exception as e:
        print(f"[SUPABASE ERROR] update: {e}")

# ──────────────────────────────────────────────────────────
# CIRCUIT BREAKER
# ──────────────────────────────────────────────────────────

def reset_daily_if_needed():
    global cb_daily_loss, cb_consecutive_losses, cb_state, cb_last_reset
    today = datetime.now(timezone.utc).date()
    if today != cb_last_reset:
        cb_daily_loss = 0.0
        cb_consecutive_losses = 0
        if cb_state == "OPEN":
            cb_state = "HALF_OPEN"
        cb_last_reset = today
        print("[CB] Daily reset ✅")

def cb_can_trade():
    global cb_state, cb_opened_at
    if cb_state == "CLOSED":
        return True
    if cb_state == "HALF_OPEN":
        cb_state = "CLOSED"
        return True
    if cb_state == "OPEN":
        elapsed = (datetime.now(timezone.utc) - cb_opened_at).total_seconds()
        if elapsed >= CB_COOLDOWN_SEC:
            cb_state = "HALF_OPEN"
            print("[CB] Cooldown done → HALF_OPEN")
        return False
    return False

def cb_record_loss(sig_id=None):
    global cb_state, cb_consecutive_losses, cb_daily_loss, cb_opened_at
    cb_consecutive_losses += 1
    cb_daily_loss += 1.0
    if sig_id:
        update_signal_status(sig_id, "CLOSED")
    print(f"[CB] Loss recorded — streak={cb_consecutive_losses} daily={cb_daily_loss}%")
    if cb_consecutive_losses >= CB_MAX_LOSSES or cb_daily_loss >= CB_MAX_DAILY_LOSS:
        cb_state = "OPEN"
        cb_opened_at = datetime.now(timezone.utc)
        print(f"[CB] OPEN — cooldown {CB_COOLDOWN_SEC}s")

def cb_record_win(sig_id=None):
    global cb_consecutive_losses
    cb_consecutive_losses = 0
    if sig_id:
        update_signal_status(sig_id, "CLOSED")
    print(f"[CB] Win recorded — streak reset")

# ──────────────────────────────────────────────────────────
# CONFLUENCE ENGINE (4 Layers · 0-100pts)
# ──────────────────────────────────────────────────────────

def calc_confluence(df):
    score = 0
    close = df["close"]

    # Layer 1 — Trend (EMA20/50) · 25pts
    ema20 = close.ewm(span=20).mean().iloc[-1]
    ema50 = close.ewm(span=50).mean().iloc[-1]
    price = close.iloc[-1]
    if price > ema20 > ema50:
        score += 25; trend = "BUY"
    elif price < ema20 < ema50:
        score += 25; trend = "SELL"
    else:
        trend = None

    # Layer 2 — Fibonacci · 25pts
    high = df["high"].rolling(50).max().iloc[-1]
    low  = df["low"].rolling(50).min().iloc[-1]
    rng  = high - low
    fibs = [low + rng*r for r in [0.236, 0.382, 0.5, 0.618, 0.786]]
    near_fib = any(abs(price - f) / price < 0.002 for f in fibs)
    if near_fib: score += 25

    # Layer 3 — Momentum RSI · 25pts
    delta = close.diff()
    gain  = delta.clip(lower=0).rolling(14).mean()
    loss  = (-delta.clip(upper=0)).rolling(14).mean()
    rs    = gain / loss.replace(0, 1e-10)
    rsi   = (100 - 100 / (1 + rs)).iloc[-1]
    if trend == "BUY"  and 40 <= rsi <= 65: score += 25
    if trend == "SELL" and 35 <= rsi <= 60: score += 25

    # Layer 4 — Structure S/R · 25pts
    recent_high = df["high"].iloc[-20:].max()
    recent_low  = df["low"].iloc[-20:].min()
    near_sr = (abs(price - recent_high) / price < 0.003 or
               abs(price - recent_low)  / price < 0.003)
    if near_sr: score += 25

    return score, trend, rsi, ema20, ema50

def calc_sl(score, action, price):
    if score >= 90:
        ninja_pct, hard_pct = 0.003, 0.010
    else:
        ninja_pct, hard_pct = 0.005, 0.012
    if action == "BUY":
        return round(price * (1 - ninja_pct), 2), round(price * (1 - hard_pct), 2)
    else:
        return round(price * (1 + ninja_pct), 2), round(price * (1 + hard_pct), 2)

# ──────────────────────────────────────────────────────────
# PRICE FEED
# ──────────────────────────────────────────────────────────

def get_candles():
    try:
        r = requests.get(
            "https://api.twelvedata.com/time_series",
            params={"symbol": SYMBOL, "interval": INTERVAL,
                    "outputsize": 100, "apikey": TWELVE_API_KEY},
            timeout=15
        )
        data = r.json()
        if "values" not in data:
            print(f"[TWELVE] Error: {data}")
            return None
        df = pd.DataFrame(data["values"])
        for col in ["open","high","low","close"]:
            df[col] = pd.to_numeric(df[col])
        return df.iloc[::-1].reset_index(drop=True)
    except Exception as e:
        print(f"[TWELVE ERROR] {e}")
        return None

def get_current_price():
    try:
        r = requests.get(
            "https://api.twelvedata.com/price",
            params={"symbol": SYMBOL, "apikey": TWELVE_API_KEY},
            timeout=10
        )
        return float(r.json().get("price", 0))
    except:
        return None

# ──────────────────────────────────────────────────────────
# SL MONITOR — check active signals every loop
# ──────────────────────────────────────────────────────────

def monitor_sl(current_price):
    global active_signals
    remaining = []
    for sig in active_signals:
        sid     = sig["id"]
        action  = sig["action"]
        ninja   = sig["ninja_sl"]
        hard    = sig["hard_sl"]
        hit     = False

        if action == "BUY":
            if current_price <= hard:
                print(f"[SL] #{sid} BUY Hard SL hit @ {current_price}")
                cb_record_loss(sid); hit = True
            elif current_price <= ninja:
                print(f"[SL] #{sid} BUY Ninja SL hit @ {current_price}")
                cb_record_loss(sid); hit = True
        else:
            if current_price >= hard:
                print(f"[SL] #{sid} SELL Hard SL hit @ {current_price}")
                cb_record_loss(sid); hit = True
            elif current_price >= ninja:
                print(f"[SL] #{sid} SELL Ninja SL hit @ {current_price}")
                cb_record_loss(sid); hit = True

        if not hit:
            remaining.append(sig)
    active_signals = remaining

# ──────────────────────────────────────────────────────────
# SIGNAL SENDER
# ──────────────────────────────────────────────────────────

def send_signal(action, score, ninja_sl, hard_sl, price):
    global last_signal_time, active_signals
    payload = {
        "asset_pair":       SYMBOL,
        "action":           action,
        "lot_size":         0.1,
        "fibo_strength":    score,
        "confluence_score": score,
        "ninja_sl":         ninja_sl,
        "hard_sl":          hard_sl,
        "status":           "OPEN",
        "source":           "alpha_buffalo_v2"
    }
    sig_id = insert_signal(payload)
    if sig_id:
        active_signals.append({
            "id": sig_id, "action": action,
            "ninja_sl": ninja_sl, "hard_sl": hard_sl,
            "entry_price": price
        })
    last_signal_time = datetime.now(timezone.utc)
    print(f"[SIGNAL] {action} score={score} ninja={ninja_sl} hard={hard_sl} id={sig_id}")

# ──────────────────────────────────────────────────────────
# MAIN LOOP
# ──────────────────────────────────────────────────────────

def main():
    print("[BOT] Alpha Buffalo v2 started ✅")
    print(f"[BOT] Symbol={SYMBOL} Interval={INTERVAL} MinScore={MIN_CONFLUENCE}")

    while True:
        try:
            reset_daily_if_needed()

            current_price = get_current_price()
            if current_price:
                monitor_sl(current_price)

            if not cb_can_trade():
                print(f"[CB] OPEN — skipping. losses={cb_consecutive_losses}")
                time.sleep(CHECK_EVERY_SEC)
                continue

            df = get_candles()
            if df is None or len(df) < 60:
                time.sleep(CHECK_EVERY_SEC)
                continue

            score, trend, rsi, ema20, ema50 = calc_confluence(df)
            price = df["close"].iloc[-1]
            now = datetime.now(timezone.utc)

            # Cooldown: 1 signal per 15 min max
            if last_signal_time:
                elapsed = (now - last_signal_time).total_seconds()
                if elapsed < 900:
                    print(f"[BOT] Cooldown {int(900-elapsed)}s remaining")
                    time.sleep(CHECK_EVERY_SEC)
                    continue

            print(f"[BOT] score={score} trend={trend} rsi={rsi:.1f} price={price:.2f}")

            if score >= MIN_CONFLUENCE and trend:
                ninja_sl, hard_sl = calc_sl(score, trend, price)
                send_signal(trend, score, ninja_sl, hard_sl, price)

        except Exception as e:
            print(f"[ERROR] {e}")

        time.sleep(CHECK_EVERY_SEC)

if __name__ == "__main__":
    main()
