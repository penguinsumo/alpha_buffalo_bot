import sys, os
sys.path.insert(0, os.path.expanduser("~/alpha_buffalo_bot"))

import requests, pandas as pd, warnings, logging
from datetime import datetime, timedelta
from core.config.loader import load_env_safely

load_env_safely()
warnings.filterwarnings('ignore')
logging.basicConfig(level=logging.INFO)

API_KEY = os.getenv("TWELVEDATA_API_KEY")
if not API_KEY:
    print("❌ TWELVEDATA_API_KEY missing")
    exit()

end = datetime(2026,6,17)
start = end - timedelta(days=60)

def fetch(interval):
    url = "https://api.twelvedata.com/time_series"
    params = {
        'symbol': 'XAU/USD', 'interval': interval,
        'start_date': start.strftime('%Y-%m-%d'),
        'end_date': end.strftime('%Y-%m-%d'),
        'outputsize': 5000, 'apikey': API_KEY
    }
    r = requests.get(url, params=params)
    data = r.json()
    if 'values' not in data:
        return None
    df = pd.DataFrame(data['values'])
    df['datetime'] = pd.to_datetime(df['datetime'])
    df = df.set_index('datetime').sort_index()
    for c in ['open','high','low','close']:
        df[c] = pd.to_numeric(df[c])
    return df

df15 = fetch('15min')
df1h = fetch('1h')
df4h = fetch('4h')
if df15 is None or df1h is None or df4h is None:
    print("❌ Missing data")
    exit()
print(f"✅ Data: 15m={len(df15)}, 1h={len(df1h)}, 4h={len(df4h)}")

from signal_composer import compose_signal

sell_signals = []
for i in range(100, len(df15)):
    ts = df15.index[i]
    win15 = df15.loc[:ts]
    win1h = df1h.loc[:ts]
    win4h = df4h.loc[:ts]
    if win1h.empty or win4h.empty:
        continue
    sig = compose_signal(win4h, win1h, win15)
    if sig and sig.direction == "SELL":
        sell_signals.append(sig)

print(f"\n📊 SELL M15 signals found: {len(sell_signals)}")

# ── ส่ง Telegram ไปทุกห้อง ──
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
NOTIFY_IDS = os.getenv("NOTIFY_IDS", "")
CHAT_IDS = [x.strip() for x in NOTIFY_IDS.split(",") if x.strip()]

if TELEGRAM_TOKEN and CHAT_IDS:
    print(f"\n📤 Sending last 3 SELL signals to {len(CHAT_IDS)} chat(s)...")
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    for s in sell_signals[-3:]:   # ส่ง 3 ตัวล่าสุด
        msg = (
            f"🔴 Alpha Buffalo SELL M15\n"
            f"🧪 ทดสอบระบบ (Paper Trade)\n"
            f"💰 Entry: {s.entry_price:.2f}\n"
            f"📈 TP1: {s.tp1_price:.2f}\n"
            f"🛡️ SL: {s.sl_price:.2f}\n"
            f"---------------------------\n"
            f"🐃 Not financial advice. Trade at your own risk."
        )
        for chat_id in CHAT_IDS:
            try:
                resp = requests.post(url, json={"chat_id": chat_id, "text": msg}, timeout=5)
                if resp.status_code == 200:
                    print(f"✅ Sent SELL @ {s.entry_price:.2f} to {chat_id}")
                else:
                    print(f"❌ Failed to {chat_id}: {resp.text}")
            except Exception as e:
                print(f"❌ Failed to {chat_id}: {e}")
    print("✅ Done")
else:
    print("⚠️ Telegram not configured")

print("\n✅ Simulation Complete!")
