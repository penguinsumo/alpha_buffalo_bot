import os, telebot, threading
from flask import Flask
from db_manager import db
from pivot_engine_v2 import PivotEngine

# Setup
TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
bot = telebot.TeleBot(TOKEN)
app = Flask(__name__)
engine = PivotEngine()

@app.route('/')
def health(): return "OK"

# --- คำสั่งหลัก ---

@bot.message_handler(commands=['start'])
def start(m):
    bot.reply_to(m, "Alpha Buffalo System: Online & Ready")

@bot.message_handler(commands=['checkdb'])
def check_db(m):
    status = db.load_all_state()
    bot.reply_to(m, f"Database Status: {status}")

@bot.message_handler(commands=['signal'])
def get_signal(m):
    bot.reply_to(m, "Calculating signal... please wait.")
    try:
        # ดึงสถานะล่าสุดจาก DB แล้วส่งเข้า Engine
        state = db.load_all_state()
        result = engine.calculate(state) 
        bot.reply_to(m, f"📊 Analysis Result:\n{result}")
    except Exception as e:
        bot.reply_to(m, f"Engine Error: {str(e)}")

# --- Run ---
threading.Thread(target=lambda: app.run(host="0.0.0.0", port=8080), daemon=True).start()

print("Alpha Buffalo System Started...")
bot.delete_webhook(drop_pending_updates=True)
bot.infinity_polling(timeout=20, long_polling_timeout=20, none_stop=True)
