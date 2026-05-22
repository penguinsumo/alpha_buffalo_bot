import keep_alive
import os
import threading
from flask import Flask

app = Flask(__name__)
@app.route('/')
def health_check():
    return "Alpha Buffalo Bot is Alive and Running!"

def run_web_server():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

# สั่งเปิด Web Server ไว้เป็น Background
threading.Thread(target=run_web_server, daemon=True).start()

# --- ด้านล่างนี้คือโค้ดระบบเดิมของคุณ ---
import os
import time
import threading
import telebot
from dotenv import load_dotenv

# --- SETUP ---
TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
bot = telebot.TeleBot(TOKEN)
@bot.message_handler(commands=["status"])
def status(message):
    host = os.uname()[1]
    bot.reply_to(message, f"✅ Bot is running on: {host}\nสถานะ: Active")


# --- 1. COMMAND LOOP (หู) ---
def command_loop():
    print("🤖 Command loop started")
    @bot.message_handler(func=lambda message: True)
    def handle_all(message):
        text = message.text.lower()
        if '/status' in text:
            bot.reply_to(message, "✅ บอททำงานปกติ (Signal & Command Active)")
        elif '/menu' in text or 'menu' in text:
            bot.reply_to(message, "🐃 **Alpha Buffalo Menu**\n/status - เช็คสถานะบอท\n/price - เช็คราคาทอง\n/signal - ดูสัญญาณล่าสุด")
        elif '/price' in text:
            bot.reply_to(message, "💰 ราคาทองตอนนี้ประมาณ 4,535.xx")
        else:
            bot.reply_to(message, "ได้รับข้อความแล้ว: " + message.text)
    
    keep_alive.keep_alive(); bot.infinity_polling()

# --- 2. SIGNAL LOOP (สมอง) ---
def signal_loop():
    print("📡 Signal loop started")
    while True:
        try:
            # ใส่ Logic เฝ้าราคาเดิมของคุณที่นี่
            time.sleep(120)
        except Exception as e:
            time.sleep(10)

# --- 3. RUN ---
if __name__ == "__main__":
    threading.Thread(target=command_loop, daemon=True).start()
    signal_loop()
