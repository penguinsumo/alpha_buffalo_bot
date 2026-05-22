import os
import time
import threading
import telebot
import requests
from flask import Flask
import keep_alive

# --- Setup ---
TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
bot = telebot.TeleBot(TOKEN)

# --- 0. Web Server (Keep Alive) ---
app = Flask(__name__)
@app.route('/')
def health_check(): return "Alpha Buffalo Bot is Alive!"
def run_web_server(): app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
threading.Thread(target=run_web_server, daemon=True).start()

# --- 1. Price Function (The Real Deal) ---
def get_xau_price():
    api_key = os.environ.get("TWELVE_DATA_API_KEY")
    if not api_key: return "API Key Not Found"
    try:
        url = f"https://api.twelvedata.com/price?symbol=XAU/USD&apikey={api_key}"
        res = requests.get(url, timeout=10).json()
        return f"{float(res['price']):.2f}" if 'price' in res else "Data Error"
    except Exception as e:
        return f"Error: {str(e)}"

# --- 2. Command Loop ---
def command_loop():
    @bot.message_handler(commands=['status'])
    def status(message):
        bot.reply_to(message, f"✅ Bot is running on: {os.uname()[1]}\nสถานะ: Active")

    @bot.message_handler(commands=['price'])
    def price_cmd(message):
        bot.reply_to(message, f"💰 ราคาทอง XAU/USD ตอนนี้คือ {get_xau_price()}")

    @bot.message_handler(func=lambda message: True)
    def handle_all(message):
        text = message.text.lower()
        if '/menu' in text or 'menu' in text:
            bot.reply_to(message, "🐃 **Alpha Buffalo Menu**\n/status - เช็คสถานะ\n/price - เช็คราคาทอง\n/signal - ดูสัญญาณล่าสุด")
        else:
            bot.reply_to(message, "ได้รับข้อความแล้ว: " + message.text)
    
    keep_alive.keep_alive()
    bot.infinity_polling()

# --- 3. Signal Loop ---
def signal_loop():
    while True:
        try:
            # ใส่ Logic เฝ้าราคาของคุณที่นี่
            time.sleep(120)
        except:
            time.sleep(10)

if __name__ == "__main__":
    threading.Thread(target=command_loop, daemon=True).start()
    signal_loop()
