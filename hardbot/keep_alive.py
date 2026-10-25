import logging
import os
from threading import Thread

from flask import Flask

# UptimeRobot pega cada 5 min y no hace falta llenar los logs con eso
logging.getLogger("werkzeug").setLevel(logging.WARNING)

app = Flask(__name__)


@app.get("/")
def home():
    return "HardBot en línea", 200


@app.get("/health")
def health():
    return {"status": "ok"}, 200


def _run():
    # Render pone el puerto en la variable PORT
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)


def keep_alive():
    Thread(target=_run, name="flask-keepalive", daemon=True).start()
