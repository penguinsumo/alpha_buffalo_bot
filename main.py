from fastapi import FastAPI, Request
import os
import requests
import uvicorn

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

@app.api_route("/", methods=["GET", "HEAD"])
async def root():
    return {"status": "OK", "bot": "Alpha Buffalo v11.2"}

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
        send_message(chat_id, "🐂 Alpha Buffalo v11.2 is running. VSA: ON")
    else:
        send_message(chat_id, "🐃 ALPHA BUFFALO V11.2\nGold Trading Signal System\nCommands: /status")
    return {"ok": True}

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
