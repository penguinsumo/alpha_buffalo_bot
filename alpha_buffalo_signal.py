import os, time, threading, telebot, requests
from flask import Flask
import keep_alive
from pivot_engine_v2 import get_flexible_score

# Setup
TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
API_KEY = os.environ.get('TWELVE_API_KEY')
BOT_MODE = os.environ.get('BOT_MODE', 'active')
bot = telebot.TeleBot(TOKEN)

# Cache Storage
cache = {"price": "0.00", "last_update": 0}

# Web Server
app = Flask(__name__)
@app.route('/')
def health(): return "Bot is alive"
def run_web(): app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
threading.Thread(target=run_web, daemon=True).start()

# Price Fetcher with Caching
def get_xau_price():
    if time.time() - cache["last_update"] < 30: # 30 วินาทีใช้ค่าเดิม
        return cache["price"]
    
    if not API_KEY: return "0.00"
    try:
        url = f"https://api.twelvedata.com/price?symbol=XAU/USD&apikey={API_KEY}"
        res = requests.get(url, timeout=5).json()
        new_price = f"{float(res.get('price', 0)):.2f}"
        cache.update({"price": new_price, "last_update": time.time()})
        return new_price
    except: return cache["price"]

# Commands
@bot.message_handler(commands=['price'])
def price(m):
    price_val = get_xau_price()
    try: score = get_flexible_score()
    except: score = "N/A"
    
    text = (
        "💰 *Alpha Buffalo Price*\\n\\n"
        "📊 Price: `~{}`\\n"
        "🎯 V5-Score: `{}`\\n"
        "⏰ {}"
    ).format(price_val, score, time.strftime("%H:%M:%S"))
    bot.reply_to(m, text, parse_mode="Markdown")

@bot.message_handler(commands=['status'])
def status(m):
    bot.reply_to(m, f"🟢 *Alpha Buffalo* | Status: Active | Time: {time.strftime('%H:%M:%S')}", parse_mode="Markdown")

@bot.message_handler(func=lambda m: True)
def handle(m): bot.reply_to(m, "System ready.")

if BOT_MODE == "active":
    keep_alive.keep_alive()
    bot.infinity_polling()
