import requests, time, pandas as pd, os
from dotenv import load_dotenv
from datetime import datetime, timezone, timedelta

load_dotenv(os.path.expanduser("~/.env.alpha"))

TWELVE_API_KEY = os.getenv("TWELVE_API_KEY")
RAILWAY_URL = "https://glistening-fascination-production-e6c1.up.railway.app/webhook/tradingview"
SYMBOL = "XAU/USD"

last_signal = None

def get_prices():
    try:
        url = f"https://api.twelvedata.com/time_series?symbol={SYMBOL}&interval=1min&outputsize=50&apikey={TWELVE_API_KEY}"
        r = requests.get(url, timeout=10)
        data = r.json()
        if "values" in data:
            prices = [float(v["close"]) for v in reversed(data["values"])]
            return prices
    except:
        pass
    return None

def calculate_signal(prices):
    if len(prices) < 26:
        return None
    close = pd.Series(prices)
    ema9 = close.ewm(span=9).mean().iloc[-1]
    ema21 = close.ewm(span=21).mean().iloc[-1]
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / loss
    rsi = (100 - (100 / (1 + rs))).iloc[-1]
    
    fibo = 80
    if ema9 > ema21 and rsi > 50 and rsi < 75:
        return {"action": "BUY", "fibo": fibo, "rsi": rsi}
    elif ema9 < ema21 and rsi < 50 and rsi > 25:
        return {"action": "SELL", "fibo": fibo, "rsi": rsi}
    return None

def send_signal(signal, price):
    global last_signal
    if last_signal == signal["action"]:
        return
    
    payload = {
        "symbol": "XAUUSD",
        "action": signal["action"],
        "price": price,
        "lot": 0.1,
        "fibo_score": signal["fibo"],
        "source": "python_mac"
    }
    
    try:
        r = requests.post(RAILWAY_URL, json=payload, timeout=10)
        result = r.json()
        if result.get("status") == "ok":
            bkk = datetime.now(timezone(timedelta(hours=7))).strftime("%H:%M:%S")
            print(f"✅ {bkk} | {signal['action']} | FIBO:{signal['fibo']} | RSI:{signal['rsi']:.1f} | Price:{price}")
            last_signal = signal["action"]
    except:
        pass

print("🐃 ALPHA BUFFALO Signal Bot started")
print("Checking every 60 seconds...\n")

while True:
    try:
        prices = get_prices()
        if prices:
            price = prices[-1]
            signal = calculate_signal(prices)
            bkk = datetime.now(timezone(timedelta(hours=7))).strftime("%H:%M:%S")
            
            if signal:
                send_signal(signal, price)
            else:
                print(f"⏳ {bkk} | No signal | Price:{price}")
        
        time.sleep(60)
    except KeyboardInterrupt:
        print("\n🛑 Bot stopped")
        break
