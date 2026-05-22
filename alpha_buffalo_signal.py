import os, time, threading, telebot, requests
from flask import Flask
import keep_alive
from pivot_engine_v2 import get_flexible_score

# Setup
TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
API_KEY = os.environ.get('TWELVE_API_KEY')
BOT_MODE = os.environ.get('BOT_MODE', 'active')
bot = telebot.TeleBot(TOKEN)

# Web Server
app = Flask(__name__)
@app.route('/')
def health(): return "Bot is alive"
def run_web(): app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
threading.Thread(target=run_web, daemon=True).start()

# Price Fetcher
def get_xau_price():
    if not API_KEY: return "Error: API Key Not Found"
    try:
        url = f"https://api.twelvedata.com/price?symbol=XAU/USD&apikey={API_KEY}"
        res = requests.get(url, timeout=10).json()
        return f"{float(res['price']):.2f}" if 'price' in res else "Data Error"
    except: return "Connection Error"

# Commands
@bot.message_handler(commands=['price'])
def price(m):
    score = get_flexible_score()
    price_val = get_xau_price()
    bot.reply_to(m, f"💰 ราคาทอง XAU/USD: {price_val} | V5-Score: {score}")

@bot.message_handler(commands=['status'])
def status(m):
    bot.reply_to(m, f"✅ Alpha Buffalo Engine | Mode: {BOT_MODE} | Time: {time.strftime('%d %b %Y | %H:%M')}")

@bot.message_handler(func=lambda m: True)
def handle(m): bot.reply_to(m, "บอททำงานอยู่ครับ")

# Start Polling
if BOT_MODE == "active":
    keep_alive.keep_alive()
    bot.infinity_polling()
