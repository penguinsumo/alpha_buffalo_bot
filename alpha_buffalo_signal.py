import requests, time, pandas as pd, os
from dotenv import load_dotenv
from datetime import datetime, timezone, timedelta

load_dotenv(os.path.expanduser("~/.env.alpha"))

TWELVE_API_KEY = os.getenv("TWELVE_API_KEY")
RAILWAY_URL = "https://glistening-fascination-production-e6c1.up.railway.app/webhook/tradingview"
SYMBOL = "XAU/USD"
MIN_CONFLUENCE = 75

last_signal = None
cb_state = "CLOSED"
cb_consecutive_losses = 0
cb_daily_loss = 0.0
cb_opened_at = None
CB_MAX_LOSSES = 3
CB_MAX_DAILY_LOSS = 3.0
CB_COOLDOWN_SEC = 3600


def cb_can_trade():
        global cb_state, cb_opened_at
        if cb_state == "OPEN":
                    if cb_opened_at and (time.time() - cb_opened_at) >= CB_COOLDOWN_SEC:
                                    cb_state = "HALF_OPEN"
                                    print("[CB] HALF_OPEN - testing 1 trade")
                                    return True
                                print("[CB] OPEN - trade blocked")
                    return False
                return True


def cb_record_win():
        global cb_state, cb_consecutive_losses
    cb_consecutive_losses = 0
    if cb_state == "HALF_OPEN":
                cb_state = "CLOSED"
                print("[CB] CLOSED - recovered")


def cb_record_loss(loss_pct=0.5):
        global cb_state, cb_consecutive_losses, cb_daily_loss, cb_opened_at
    cb_consecutive_losses += 1
    cb_daily_loss += loss_pct
    if cb_consecutive_losses >= CB_MAX_LOSSES or cb_daily_loss >= CB_MAX_DAILY_LOSS or cb_state == "HALF_OPEN":
                cb_state = "OPEN"
                cb_opened_at = time.time()
                print(f"[CB] OPEN - losses:{cb_consecutive_losses} daily:{cb_daily_loss:.1f}%")


def score_trend(close, action):
        ema20 = close.ewm(span=20).mean().iloc[-1]
    ema50 = close.ewm(span=50).mean().iloc[-1]
    price = close.iloc[-1]
        score = 0
    if action == "BUY":
                if price > ema20:
                                score += 8
                            if ema20 > ema50:
                                            score += 17
else:
        if price < ema20:
                        score += 8
                    if ema20 < ema50:
                                    score += 17
                            return score


def score_fibo(close):
        hi = close.rolling(20).max().iloc[-1]
    lo = close.rolling(20).min().iloc[-1]
    price = close.iloc[-1]
    rng = hi - lo
    if rng == 0:
                return 0
    strength = 1 - abs(price - (lo + rng * 0.618)) / rng
    strength = max(0.0, min(1.0, strength))
    return round(strength * 25)


def score_momentum(rsi, action):
        if action == "BUY":
                    if 40 <= rsi <= 65:
            return 25
        if 30 <= rsi < 40:
                        return 15
                    if 65 < rsi <= 75:
                                    return 8
else:
        if 35 <= rsi <= 60:
                        return 25
                    if 60 < rsi <= 70:
                                    return 15
        if 25 <= rsi < 35:
                        return 8
                return 0


def score_structure(close, action):
        hi20 = close.rolling(20).max().iloc[-1]
    lo20 = close.rolling(20).min().iloc[-1]
    price = close.iloc[-1]
    near_support = abs(price - lo20) / lo20 < 0.003
    near_resist = abs(price - hi20) / hi20 < 0.003
    if action == "BUY":
                if near_support and not near_resist:
                                return 25
                            if near_support and near_resist:
                                            return 12
else:
        if near_resist and not near_support:
                        return 25
                    if near_resist and near_support:
                                    return 12
                            return 0


def sl_buffers(score):
        if score >= 90:
                    return 0.3, 1.0
                if score >= 75:
                            return 0.5, 1.2
                        if score >= 60:
                                    return 0.8, 1.5
    return 1.2, 2.0


def evaluate_confluence(close, action, rsi):
        s1 = score_trend(close, action)
    s2 = score_fibo(close)
    s3 = score_momentum(rsi, action)
    s4 = score_structure(close, action)
    total = s1 + s2 + s3 + s4
    ninja_buf, hard_buf = sl_buffers(total)
    return {
        "score": total,
                "layers": {"trend": s1, "fibo": s2, "momentum": s3, "structure": s4},
        "can_trade": total >= MIN_CONFLUENCE,
                "ninja_sl_buffer": ninja_buf,
                "hard_sl_buffer": hard_buf,
    }


def get_prices():
    try:
                url = (
                    f"https://api.twelvedata.com/time_series?symbol={SYMBOL}"
                    f"&interval=1min&outputsize=70&apikey={TWELVE_API_KEY}"
    )
        r = requests.get(url, timeout=10)
        data = r.json()
        if "values" in data:
                        return [float(v["close"]) for v in reversed(data["values"])]
except Exception:
        pass
    return None


def calculate_signal(prices):
        if len(prices) < 51:
                    return None
                close = pd.Series(prices)
    ema9 = close.ewm(span=9).mean().iloc[-1]
                ema21 = close.ewm(span=21).mean().iloc[-1]
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
                                rsi = (100 - (100 / (1 + gain / loss))).iloc[-1]

    if ema9 > ema21 and 40 <= rsi <= 75:
                action = "BUY"
elif ema9 < ema21 and 25 <= rsi <= 60:
        action = "SELL"
else:
        return None

    confluence = evaluate_confluence(close, action, rsi)
    if not confluence["can_trade"]:
                print(f"  [Confluence] HOLD score:{confluence['score']}/100 {confluence['layers']}")
        return None

    return {
                "action": action,
                "rsi": rsi,
                "confluence_score": confluence["score"],
                "ninja_sl_buffer": confluence["ninja_sl_buffer"],
        "hard_sl_buffer": confluence["hard_sl_buffer"],
    }


def send_signal(signal, price):
        global last_signal
    if last_signal == signal["action"]:
                return
    ninja = price - signal["ninja_sl_buffer"] if signal["action"] == "BUY" else price + signal["ninja_sl_buffer"]
    hard = price - signal["hard_sl_buffer"] if signal["action"] == "BUY" else price + signal["hard_sl_buffer"]
    payload = {
                "symbol": "XAUUSD",
                "action": signal["action"],
                "price": price,
        "lot": 0.1,
                "confluence_score": signal["confluence_score"],
                "ninja_sl": ninja,
                "hard_sl": hard,
                "source": "alpha_buffalo_v2",
    }
    try:
                r = requests.post(RAILWAY_URL, json=payload, timeout=10)
        result = r.json()
        if result.get("status") == "ok":
                        bkk = datetime.now(timezone(timedelta(hours=7))).strftime("%H:%M:%S")
            print(f"[{bkk}] SENT {signal['action']} score:{signal['confluence_score']} RSI:{signal['rsi']:.1f} Price:{price:.2f}")
            last_signal = signal["action"]
except Exception:
        pass


print("ALPHA BUFFALO v2 - Confluence 4 Layer + Circuit Breaker")
print(f"Min confluence: {MIN_CONFLUENCE}/100  CB max losses: {CB_MAX_LOSSES}\n")

while True:
        try:
                    if not cb_can_trade():
                                    time.sleep(60)
                                    continue

                    prices = get_prices()
                    bkk = datetime.now(timezone(timedelta(hours=7))).strftime("%H:%M:%S")

        if prices:
                        price = prices[-1]
                        signal = calculate_signal(prices)
                        if signal:
                                            send_signal(signal, price)
    else:
                print(f"[{bkk}] No signal | CB:{cb_state} | Price:{price:.2f}")
else:
            print(f"[{bkk}] Price fetch failed")

        time.sleep(60)

except KeyboardInterrupt:
        print("\nBot stopped")
        break
