import os, telebot

TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
bot = telebot.TeleBot(TOKEN)

# ล้าง Webhook และล้างคิวเก่าทิ้งทั้งหมด (Drop pending updates)
bot.delete_webhook(drop_pending_updates=True)

@bot.message_handler(commands=['start'])
def start(m):
    bot.reply_to(m, "System Online - Force Cleaned")

print("Bot starting with force clean...")
# ใช้ none_stop=True เพื่อให้มันทนทานขึ้น
bot.infinity_polling(timeout=20, long_polling_timeout=20, none_stop=True)
