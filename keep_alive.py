from flask import Flask
from threading import Thread
import os
app = Flask(__name__)
@app.route("/")
def home(): return "Bot is awake!", 200
def run(): app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
def keep_alive(): Thread(target=run, daemon=True).start()
