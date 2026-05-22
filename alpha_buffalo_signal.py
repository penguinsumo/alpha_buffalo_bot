import os, time, threading, telebot, requests
from flask import Flask
import keep_alive

# Setup
TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
API_KEY = os.environ.get('TWELVE_DATA_API_KEY')
BOT_MODE = os.environ.get('BOT_MODE', 'passive') # Default passive เพื่อกันชน
bot = telebot.TeleBot(TOKEN)

# Web Server
app = Flask(__name__)
@app.route('/')
def health(): return "Bot is alive"
def run_web(): app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
threading.Thread(target=run_web, daemon=True).start()

# Price Fetcher

def get_xau_price():
    api_key = os.environ.get("TWELVE_DATA_API_KEY")
    if not api_key:
        print("DEBUG LOG: Environment variables available:", list(os.environ.keys()))
        return "Error: API Key Not Found"
    try:
        url = f"https://api.twelvedata.com/price?symbol=XAU/USD&apikey={api_key}"
        res = requests.get(url, timeout=10).json()
        return f"{float(res["price"]):.2f}" if "price" in res else "Data Error"
    except: return "Connection Error"


# Commands
@bot.message_handler(commands=['price'])
def price(m): bot.reply_to(m, f"💰 ราคาทอง XAU/USD: {get_xau_price()}")

@bot.message_handler(commands=['status'])
def status(m): bot.reply_to(m, f"✅ Bot is running on: {os.uname()[1]} | Mode: {BOT_MODE}")

@bot.message_handler(func=lambda m: True)
def handle(m): bot.reply_to(m, "บอททำงานอยู่ครับ")

# Start Polling (Only if Active)
if BOT_MODE == "active":
    print("🚀 Active Mode: Starting Polling")
    keep_alive.keep_alive()
    bot.infinity_polling()
else:
    print("💤 Passive Mode: Only Web Server running")
