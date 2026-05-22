import os, telebot, threading, time
from flask import Flask
from db_manager import db

TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
bot = telebot.TeleBot(TOKEN)
app = Flask(__name__)

@app.route('/')
def health(): return "OK"

# คำสั่งทดสอบ DB
@bot.message_handler(commands=['checkdb'])
def check_db(m):
    status = db.load_all_state()
    bot.reply_to(m, f"Database Status: {status}")

@bot.message_handler(commands=['start'])
def start(m): bot.reply_to(m, "System Online & DB Ready")

# Web Server
threading.Thread(target=lambda: app.run(host="0.0.0.0", port=8080), daemon=True).start()

# Polling
bot.delete_webhook(drop_pending_updates=True)
print("Bot started with DB integration...")
bot.infinity_polling(timeout=20, long_polling_timeout=20, none_stop=True)
