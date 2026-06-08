from fastapi import FastAPI, Request
import os, uvicorn, aiohttp

app = FastAPI()
TOKEN = os.getenv("TELEGRAM_TOKEN")
ADMIN_ID = os.getenv("ADMIN_ID") # สามารถนำไปใช้เช็คสิทธิ์ (Admin) ได้ในอนาคต

# 1. เปลี่ยนเป็น Async เพื่อไม่ให้บล็อก Event Loop ของ FastAPI
async def send_message(chat_id, text):
    if not TOKEN:
        print("Error: TELEGRAM_TOKEN is missing")
        return
    
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id, 
        "text": text, 
        "parse_mode": "HTML"
    }
    
    try:
        # ใช้ aiohttp สำหรับการส่ง HTTP Request แบบ Async
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, timeout=5) as response:
                if response.status != 200:
                    print(f"Failed to send to {chat_id}: HTTP {response.status}")
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
        
    # 2. เพิ่มคำสั่ง await เพื่อรอให้ส่งข้อความเสร็จสิ้น
    if text == "/status":
        await send_message(chat_id, "🐂 Alpha Buffalo v5.2 is running. VSA: ON")
    else:
        await send_message(chat_id, "🐃 ALPHA BUFFALO V5\nGold Trading Signal System\nCommands: /status")
        
    return {"ok": True}
