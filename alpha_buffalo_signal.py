import os
import time
import threading
import telebot
from dotenv import load_dotenv

# --- SETUP ---
TOKEN = '8700567296:AAE8TNqJUN2X97ASVbQKyy4KuIUdAehIfxI'
bot = telebot.TeleBot(TOKEN)

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
    
    bot.infinity_polling()

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
