import 
from pivot_engine_v2 import get_flexible_scoreos, time, threading, telebot, requests
from flask import Flask
import keep_alive

# Setup
TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
API_KEY = os.environ.get('TWELVE_API_KEY') # ดึงจากชื่อที่ตรงกับ Dashboard ของคุณ
BOT_MODE = os.environ.get('BOT_MODE', 'active') # Default เป็น Active ถ้าไม่ได้ตั้งค่า
bot = telebot.TeleBot(TOKEN)

# Debug: Print env to logs (ช่วยให้เราเห็นว่าบอทเห็นตัวแปรอะไรบ้าง)
print(f"DEBUG: TWELVE_API_KEY found: {bool(API_KEY)}")
print(f"DEBUG: All env vars: {list(os.environ.keys())}")

# Web Server
app = Flask(__name__)
@app.route('/')
def health(): return "Bot is alive"
def run_web(): app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
threading.Thread(target=run_web, daemon=True).start()

# Price Fetcher
def get_xau_price():
    if not API_KEY: return "Error: API Key Not Found (Check TWELVE_API_KEY in Dashboard)"
    try:
        url = f"https://api.twelvedata.com/price?symbol=XAU/USD&apikey={API_KEY}"
        res = requests.get(url, timeout=10).json()
        return f"{float(res['price']):.2f}" if 'price' in res else f"Data Error: {res}"
    except Exception as e: return f"Error: {str(e)}"

# Commands
@bot.message_handler(commands=['price'])
def price(m):
    score = get_flexible_score() bot.reply_to(m, f"💰 ราคาทอง XAU/USD: {get_xau_price()} | V5-Score: {score}\")

@bot.message_handler(commands=['status'])
def status(m): bot.reply_to(m, f"✅ Bot is running on: {os.uname()[1]} | Mode: {BOT_MODE}")

@bot.message_handler(func=lambda m: True)
def handle(m): bot.reply_to(m, "บอททำงานอยู่ครับ")

# Start Polling
if BOT_MODE == "active":
    print("🚀 Active Mode: Starting Polling")
    keep_alive.keep_alive()
    bot.infinity_polling()
else:
    print("💤 Passive Mode: Only Web Server running")
