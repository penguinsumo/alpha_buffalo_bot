import os, telebot, threading, logging
from flask import Flask
from telebot import logger

# เปิด Log ละเอียดสุดๆ
logger.setLevel(logging.DEBUG)

bot = telebot.TeleBot(os.environ.get('TELEGRAM_BOT_TOKEN'))
app = Flask(__name__)

@app.route('/')
def health(): return "OK"

# คำสั่ง Debug
@bot.message_handler(func=lambda m: True)
def echo_all(message):
    print(f"DEBUG: Received message from {message.chat.id}: {message.text}")
    bot.reply_to(message, "I heard you!")

# Start
threading.Thread(target=lambda: app.run(host="0.0.0.0", port=8080), daemon=True).start()

bot.delete_webhook(drop_pending_updates=True)
print("Bot listening for updates...")
bot.infinity_polling(timeout=10, long_polling_timeout=10)
