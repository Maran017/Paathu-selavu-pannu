from flask import Flask
import threading
import os

app = Flask(__name__)

def run_bot():
    import bot   

@app.route("/")
def home():
    return "Telegram bot is running ✅"

if __name__ == "__main__":
    threading.Thread(target=run_bot).start()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
