from fastapi import FastAPI, Request
import os, requests, uvicorn

app = FastAPI()
TOKEN = os.getenv("TELEGRAM_TOKEN")
TWELVE_API_KEY = os.getenv("TWELVE_API_KEY")

def send_message(chat_id, text):
    if not TOKEN: return
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"}, timeout=5)
    except Exception as e:
        print(f"Send error: {e}")

def get_xauusd_price():
    if not TWELVE_API_KEY:
        return "N/A (no API key)"
    url = f"https://api.twelvedata.com/price?symbol=XAU/USD&apikey={TWELVE_API_KEY}"
    try:
        resp = requests.get(url, timeout=10).json()
        return resp.get("price", "N/A")
    except Exception as e:
        return f"Error: {e}"

@app.get("/health")
async def health():
    return {"status": "ok", "service": "telegram-bot", "timestamp": str(requests.get("http://worldtimeapi.org/api/ip").json().get("datetime", ""))}

@app.post("/webhook/telegram")
async def webhook(request: Request):
    body = await request.json()
    msg = body.get("message")
    if not msg: return {"ok": True}
    chat_id = msg.get("chat", {}).get("id")
    text = msg.get("text", "").strip()
    if not chat_id: return {"ok": True}
    if text == "/price":
        price = get_xauusd_price()
        send_message(chat_id, f"💰 XAUUSD: {price}")
    elif text == "/status":
        send_message(chat_id, "🐂 Alpha Buffalo v5.2\nVSA: ON\nSpike detection: ON\nSession: LONDON\nData feed: Twelve Data")
    else:
        send_message(chat_id, "🐃 ALPHA BUFFALO V5\nGold Trading Signal System\nCommands: /price, /status")
    return {"ok": True}

@app.get("/")
def root():
    return {"ok": True}

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
