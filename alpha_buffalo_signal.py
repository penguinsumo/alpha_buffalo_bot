import os, time, threading, telebot, requests, logging
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

# Server
app = Flask(__name__)
@app.route('/')
def health(): return "Bot is alive"
def run_web(): app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
threading.Thread(target=run_web, daemon=True).start()

@bot.message_handler(commands=['price'])
def price(m):
    try:
        url = f"https://api.twelvedata.com/price?symbol=XAU/USD&apikey={API_KEY}"
        price_val = f"{float(requests.get(url, timeout=5).json().get('price', 0)):.2f}"
        score = get_flexible_score()
    except: 
        price_val, score = "0.00", "N/A"
    bot.reply_to(m, f"💰 *Price*: `{price_val}`\n🎯 *V5-Score*: `{score}`", parse_mode="Markdown")

# Safe Polling
if BOT_MODE == "active":
    print("Starting Polling Mode...")
    keep_alive.keep_alive()
    try:
        bot.infinity_polling(timeout=10, long_polling_timeout=5)
    except Exception as e:
        print(f"Polling Error: {e}")
else:
    print("Passive Mode: No Polling.")
