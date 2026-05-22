import os, telebot, threading, logging
from flask import Flask
from db_manager import db
from pivot_engine_v2 import PivotEngine

# Setup
TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
bot = telebot.TeleBot(TOKEN)
app = Flask(__name__)
engine = PivotEngine()

logging.basicConfig(level=logging.INFO)

@app.route('/')
def health(): return "OK"

# Bot Commands
@bot.message_handler(commands=['start', 'signal', 'checkdb'])
def handle_commands(m):
    if m.text == '/start': bot.reply_to(m, "System Online")
    elif m.text == '/checkdb': bot.reply_to(m, f"Status: {db.load_all_state()}")
    elif m.text == '/signal':
        try:
            bot.reply_to(m, f"Analysis: {engine.calculate(db.load_all_state())}")
        except Exception as e:
            bot.reply_to(m, f"Error: {e}")

# Start Web in Background
def run_web():
    app.run(host="0.0.0.0", port=8080)

threading.Thread(target=run_web, daemon=True).start()

# Main Thread (Bot)
print("Alpha Buffalo System Starting Bot Polling...")
bot.delete_webhook(drop_pending_updates=True)
bot.infinity_polling(timeout=20, long_polling_timeout=20, none_stop=True)
