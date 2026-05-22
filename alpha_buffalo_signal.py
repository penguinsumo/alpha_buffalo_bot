import os, time, threading, telebot, requests
from flask import Flask
import keep_alive
from pivot_engine_v2 import get_flexible_score
from db_manager import db

# Setup
TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
API_KEY = os.environ.get('TWELVE_API_KEY')
BOT_MODE = os.environ.get('BOT_MODE', 'active')
bot = telebot.TeleBot(TOKEN)

def on_startup():
    print("🚀 Booting Alpha Buffalo...")
    state = db.load_all_state()
    if state is None:
        print("❌ CRITICAL: DB Connection Failed. Aborting.")
        exit(1)
    print("✅ System Ready. State Restored.")

# Run Startup
on_startup()

# Server & Price...
app = Flask(__name__)
@app.route('/')
def health(): return "Bot is alive"
def run_web(): app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
threading.Thread(target=run_web, daemon=True).start()

def get_xau_price():
    try:
        url = f"https://api.twelvedata.com/price?symbol=XAU/USD&apikey={API_KEY}"
        return f"{float(requests.get(url, timeout=5).json().get('price', 0)):.2f}"
    except: return "0.00"

@bot.message_handler(commands=['price'])
def price(m):
    price_val = get_xau_price()
    try: score = get_flexible_score()
    except: score = "N/A"
    text = f"💰 *Price*: `{price_val}`\n🎯 *V5-Score*: `{score}`"
    bot.reply_to(m, text, parse_mode="Markdown")

if BOT_MODE == "active":
    keep_alive.keep_alive()
    bot.infinity_polling()
