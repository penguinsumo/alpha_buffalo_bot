import os, telebot, threading, logging
from flask import Flask
from db_manager import db
from pivot_engine_v2 import PivotEngine

# Setup
TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
bot = telebot.TeleBot(TOKEN)
app = Flask(__name__)
engine = PivotEngine()

# Logging config
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger('bot_logger')

@app.route('/')
def health(): return "OK"

# Bot Logic
@bot.message_handler(commands=['start', 'signal', 'checkdb'])
def handle_commands(m):
    logger.info(f"Received command: {m.text} from {m.chat.id}")
    if m.text == '/start': bot.reply_to(m, "Alpha Buffalo System: Online")
    elif m.text == '/checkdb': bot.reply_to(m, f"Status: {db.load_all_state()}")
    elif m.text == '/signal':
        try:
            bot.reply_to(m, f"Analysis: {engine.calculate(db.load_all_state())}")
        except Exception as e:
            bot.reply_to(m, f"Error: {e}")

# Separate Threads
def run_web():
    logger.info("Starting Web Server...")
    app.run(host="0.0.0.0", port=8080)

def run_bot():
    logger.info("Starting Telegram Bot Polling...")
    bot.delete_webhook(drop_pending_updates=True)
    bot.infinity_polling(timeout=20, long_polling_timeout=20, none_stop=True)

# Main Execution
if __name__ == "__main__":
    t1 = threading.Thread(target=run_web, daemon=True)
    t2 = threading.Thread(target=run_bot, daemon=True)
    
    t1.start()
    t2.start()
    
    t1.join()
    t2.join()
