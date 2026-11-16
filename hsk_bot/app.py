import hmac
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from flask import Flask, jsonify, request
from werkzeug.exceptions import HTTPException

from .config import Config
from .curriculum import load_words
from .engine import Engine
from .storage import Storage
from .telegram import Telegram, flush_outbox


def create_app(config=None, storage=None, telegram=None, words=None, clock=None):
    config = config or Config.from_env()
    storage = storage or Storage(config.database_url)
    storage.initialize()
    telegram = telegram or Telegram(config.token)
    engine = Engine(words or load_words(), config.owner_id, config.timezone)
    clock = clock or (lambda: datetime.now(timezone.utc))
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 65536
    app.extensions["hsk"] = {"storage": storage, "engine": engine, "telegram": telegram}
    # One worker is intentional: this private bot's scheduling metadata is cached
    # so a minutely cron doesn't keep the free Neon database awake all month.
    def metadata(state):
        user = state["user"]
        return {"zone": user["timezone"] if user else config.timezone,
                "scheduled": bool(user and user["onboarded"] and not user["paused"]),
                "pending": bool(state["outbox"]), "days": set(user["days"]) if user else set()}

    runtime = storage.update(metadata)

    def process(operation):
        def transaction(state):
            result = operation(state)
            return result, metadata(state)
        result, snapshot = storage.update(transaction)
        runtime.update(snapshot)
        return result

    def deliver():
        if not runtime["pending"]:
            return True
        delivered = flush_outbox(storage, telegram)
        runtime.update(storage.update(metadata))
        return delivered

    @app.get("/")
    @app.get("/health")
    def health():
        return jsonify(status="ok", service="private-hsk-bot")

    def authorized(header, expected):
        return hmac.compare_digest(request.headers.get(header, "").encode(), expected.encode())

    @app.post("/telegram")
    def webhook():
        if not authorized("X-Telegram-Bot-Api-Secret-Token", config.webhook_secret):
            return jsonify(error="unauthorized"), 401
        update = request.get_json(silent=True)
        if not isinstance(update, dict):
            return jsonify(error="invalid update"), 400
        process(lambda state: engine.handle(state, update, clock()))
        delivered = deliver()
        return jsonify(ok=delivered), 200 if delivered else 503

    @app.post("/cron")
    def cron():
        if not authorized("Authorization", "Bearer " + config.cron_secret):
            return jsonify(error="unauthorized"), 401
        now = clock()
        local = now.astimezone(ZoneInfo(runtime["zone"]))
        due = (runtime["scheduled"] and local.hour == 12 and local.minute < 15
               and local.date().isoformat() not in runtime["days"])
        created = process(lambda state: engine.tick(state, now)) if due else False
        delivered = deliver()
        return jsonify(ok=delivered, lesson_created=created), 200 if delivered else 503

    # Do not echo exceptions (they may contain provider connection strings).
    @app.errorhandler(Exception)
    def server_error(_error):
        if isinstance(_error, HTTPException):
            return jsonify(error=_error.name), _error.code
        logging.error("Request failed (%s)", type(_error).__name__)
        return jsonify(error="temporary server error"), 500

    return app
