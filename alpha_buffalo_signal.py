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
    if not API_KEY: return "0.00"
    try:
        url = f"https://api.twelvedata.com/price?symbol=XAU/USD&apikey={API_KEY}"
        res = requests.get(url, timeout=10).json()
        return f"{float(res.get('price', 0)):.2f}"
    except: return "0.00"

# Commands
@bot.message_handler(commands=['price'])
def price(m):
    price_val = get_xau_price()
    try:
        score = get_flexible_score()
    except Exception as e:
        score = "N/A"
    
    text = (
        "💰 *Alpha Buffalo Price*\\n\\n"
        "📌 Asset: `XAUUSD`\\n"
        "📊 Price: `~{}`\\n"
        "🎯 V5-Score: `{}`\\n"
        "⏰ Time: {}"
    ).format(price_val, score, time.strftime("%d %b %Y | %H:%M"))
    bot.reply_to(m, text, parse_mode="Markdown")

@bot.message_handler(commands=['status'])
def status(m):
    text = (
        "🟢 *Alpha Buffalo System Status*\\n\\n"
        "🖥 Server: `Alpha Buffalo Engine`\\n"
        "⚙️ Mode: `{}`\\n"
        "✅ Status: `Active`\\n"
        "⏱ Time: {}"
    ).format(BOT_MODE, time.strftime("%d %b %Y | %H:%M"))
    bot.reply_to(m, text, parse_mode="Markdown")

@bot.message_handler(func=lambda m: True)
def handle(m): bot.reply_to(m, "บอททำงานอยู่ครับ")

if BOT_MODE == "active":
    keep_alive.keep_alive()
    bot.infinity_polling()
