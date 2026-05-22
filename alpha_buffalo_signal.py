import os, telebot, threading, requests, time
from flask import Flask

# Setup
TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
bot = telebot.TeleBot(TOKEN)
app = Flask(__name__)

@app.route('/')
def health(): return "OK"

# 1. ทดสอบว่า Railway ออกเน็ตหา Telegram ได้ไหม
def test_connection():
    try:
        url = f"https://api.telegram.org/bot{TOKEN}/getMe"
        res = requests.get(url, timeout=10)
        print(f"DEBUG: Telegram API Connection Test: {res.status_code}")
        print(f"DEBUG: Response: {res.text}")
    except Exception as e:
        print(f"DEBUG: CRITICAL Network Error: {e}")

# 2. Start
threading.Thread(target=lambda: app.run(host="0.0.0.0", port=8080), daemon=True).start()
test_connection()

print("Bot starting manual polling...")
# ใช้ get_updates แบบ manual ทีละรอบเพื่อเลี่ยง Error ในตัว infinity_polling
offset = None
while True:
    try:
        updates = bot.get_updates(offset=offset, timeout=10)
        for update in updates:
            offset = update.update_id + 1
            print(f"DEBUG: Received: {update}")
            bot.reply_to(update.message, "Pong!")
    except Exception as e:
        print(f"DEBUG: Polling loop error: {e}")
        time.sleep(5)
