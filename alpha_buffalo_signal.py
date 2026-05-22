import os, telebot, threading, time
from flask import Flask
from db_manager import db

# Setup
bot = telebot.TeleBot(os.environ.get('TELEGRAM_BOT_TOKEN'))
app = Flask(__name__)

@app.route('/')
def health(): return "OK"

def run_web(): app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
threading.Thread(target=run_web, daemon=True).start()

@bot.message_handler(commands=['start'])
def start(m): bot.reply_to(m, "System Cleaned & Online")

# Force Clear
bot.delete_webhook(drop_pending_updates=True)
print("Bot starting clean...")
bot.infinity_polling(timeout=10, long_polling_timeout=10, none_stop=True)
