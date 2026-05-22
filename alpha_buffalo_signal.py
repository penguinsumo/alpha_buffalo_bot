import os, telebot

# 1. Setup พื้นฐาน
TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
bot = telebot.TeleBot(TOKEN)

# 2. ป้องกัน Conflict (เคลียร์ Webhook เก่า)
bot.remove_webhook()

# 3. คำสั่งทดสอบพื้นฐาน
@bot.message_handler(commands=['start'])
def start(m):
    bot.reply_to(m, "System Online - Baseline Stable")

@bot.message_handler(commands=['ping'])
def ping(m):
    bot.reply_to(m, "Pong!")

# 4. Polling แบบมาตรฐานที่สุด
print("Bot starting...")
bot.infinity_polling(timeout=10, long_polling_timeout=5)
