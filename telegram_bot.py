from fastapi import FastAPI, Request
import os, requests, uvicorn

app = FastAPI()
TOKEN = os.getenv("TELEGRAM_TOKEN")
ADMIN_ID = os.getenv("ADMIN_ID")

def send_message(chat_id, text):
    if not TOKEN:
        return
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"}, timeout=5)
    except Exception as e:
        print(f"Send error: {e}")

@app.get("/health")
def health():
    return {"status": "ok", "service": "tgbot-gate"}

@app.post("/webhook/telegram")
async def webhook(request: Request):
    body = await request.json()
    message = body.get("message")
    if not message:
        return {"ok": True}
    chat_id = message.get("chat", {}).get("id")
    text = message.get("text", "").strip()
    if not chat_id:
        return {"ok": True}
    if text == "/status":
        send_message(chat_id, "🐂 Alpha Buffalo v5.2 is running. VSA: ON")
    else:
        send_message(chat_id, "🐃 ALPHA BUFFALO V5\nGold Trading Signal System\nCommands: /status")
    return {"ok": True}

    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)

@app.head("/health")
async def health_head():
    return {"status": "ok"}
