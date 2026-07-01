from flask import Flask
from threading import Thread
from config import KEEP_ALIVE_PORT

app = Flask(__name__)


@app.route("/")
def home():
    return "🤫 Whisper Bot is alive!", 200


@app.route("/health")
def health():
    return {"status": "ok"}, 200


def run():
    app.run(host="0.0.0.0", port=KEEP_ALIVE_PORT, debug=False, use_reloader=False)


def keep_alive():
    t = Thread(target=run, daemon=True)
    t.start()
