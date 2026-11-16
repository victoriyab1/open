"""Local polling, ordinary system cron, and secure webhook registration."""
import argparse
import logging
import os
import time

from hsk_bot.config import Config
from hsk_bot.curriculum import load_words
from hsk_bot.engine import Engine
from hsk_bot.storage import Storage
from hsk_bot.telegram import Telegram, TelegramError, flush_outbox


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["poll", "tick", "set-webhook", "webhook-info", "init-db"])
    args = parser.parse_args()
    config = Config.from_env()
    telegram = Telegram(config.token)
    storage = Storage(config.database_url)
    storage.initialize()
    engine = Engine(load_words(), config.owner_id, config.timezone)
    if args.command == "set-webhook":
        url = os.environ.get("PUBLIC_URL", "").rstrip("/")
        if not url.startswith("https://"):
            parser.error("PUBLIC_URL must be the HTTPS address of your deployed service")
        telegram.call("setWebhook", {"url": url + "/telegram", "secret_token": config.webhook_secret,
                                     "allowed_updates": ["message", "callback_query"], "max_connections": 1})
        for code, lang in (("ru", "ru"), ("en", "en")):
            labels = {
                "ru": [("start", "Начать"), ("lesson", "Урок и практика"), ("settings", "Настройки"),
                       ("archive", "Архив"), ("stats", "Прогресс"), ("pause", "Пауза рассылки"),
                       ("resume", "Включить рассылку"), ("privacy", "Данные"), ("delete_me", "Удалить прогресс")],
                "en": [("start", "Start"), ("lesson", "Lesson and practice"), ("settings", "Settings"),
                       ("archive", "Archive"), ("stats", "Progress"), ("pause", "Pause delivery"),
                       ("resume", "Resume delivery"), ("privacy", "Privacy"), ("delete_me", "Delete progress")],
            }
            telegram.call("setMyCommands", {"commands": [{"command": c, "description": d} for c, d in labels[lang]],
                                            "language_code": code})
        print("Webhook and RU/EN command menus configured.")
    elif args.command == "webhook-info":
        info = telegram.call("getWebhookInfo")
        # Only non-secret operational status, never print the complete API response.
        print({"configured": bool(info.get("url")), "pending_updates": info.get("pending_update_count", 0),
               "has_recent_error": bool(info.get("last_error_date"))})
    elif args.command == "tick":
        created = storage.update(lambda state: engine.tick(state))
        delivered = flush_outbox(storage, telegram)
        print({"lesson_created": created, "queue_drained": delivered})
    elif args.command == "init-db":
        print("Database initialized.")
    else:
        # Polling is for a Mac/local server; scheduled messages are checked each loop.
        telegram.call("deleteWebhook")
        offset = storage.update(lambda s: s.get("poll_offset", 0))
        print("Private bot polling. Ctrl+C to stop.")
        while True:
            try:
                updates = telegram.call("getUpdates", {"offset": offset, "timeout": 10,
                                                        "allowed_updates": ["message", "callback_query"]})
                for update in updates:
                    next_offset = update["update_id"] + 1
                    def process(state):
                        engine.handle(state, update)
                        state["poll_offset"] = next_offset
                    storage.update(process)
                    offset = next_offset
                    flush_outbox(storage, telegram)
                storage.update(lambda state: engine.tick(state))
                flush_outbox(storage, telegram)
            except TelegramError as error:
                logging.warning("Telegram unavailable (code=%s)", error.code)
                time.sleep(min(error.retry_after, 30))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        main()
    except KeyboardInterrupt:
        print("Stopped.")
