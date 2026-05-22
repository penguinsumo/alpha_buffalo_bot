import os, telebot, time
from flask import Flask
import threading

# Setup
TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
bot = telebot.TeleBot(TOKEN)
app = Flask(__name__)

@app.route('/')
def health(): return "OK"

# 1. ล้าง Webhook ให้สะอาดที่สุด (บางทีค้างจากอดีต)
try:
    bot.remove_webhook()
    print("DEBUG: Webhook removed successfully.")
except Exception as e:
    print(f"DEBUG: Webhook remove failed: {e}")

# 2. Start Web Server
threading.Thread(target=lambda: app.run(host="0.0.0.0", port=8080), daemon=True).start()

# 3. เริ่มต้น Polling แบบเคลียร์คิวถาวร
print("Bot starting with exclusive polling...")
while True:
    try:
        # ใช้วิธีนี้เพื่อหลีกเลี่ยง Conflict
        bot.infinity_polling(timeout=20, long_polling_timeout=20, drop_pending_updates=True)
    except Exception as e:
        print(f"DEBUG: Polling Error: {e}")
        time.sleep(5)
