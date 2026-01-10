from flask import Flask
import threading
import os
from bot import bot  # make sure bot.py has TELEGRAM_BOT_TOKEN from env

app = Flask(__name__)

def run_bot():
    # This will start the bot polling in a separate thread
    bot.infinity_polling(skip_pending=True)

# Start the bot in a background thread
threading.Thread(target=run_bot, daemon=True).start()

@app.route("/")
def home():
    return "Telegram bot is running ✅"

if __name__ == "__main__":
    # Render provides the PORT environment variable
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
