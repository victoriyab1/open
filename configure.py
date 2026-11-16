"""Run in a local terminal to pair your own Telegram account without sharing a token."""
import getpass
import os
import re
import secrets
import time
from pathlib import Path

from hsk_bot.telegram import Telegram, TelegramError


def main():
    target = Path(__file__).resolve().parent / ".env"
    if target.exists():
        raise SystemExit("Файл .env уже существует. Измените его вручную — настройки не перезаписаны.")
    token = getpass.getpass("Токен BotFather (ввод скрыт): ").strip()
    if not re.fullmatch(r"\d+:[A-Za-z0-9_-]+", token):
        raise SystemExit("Неверный формат токена.")
    api = Telegram(token)
    me = api.call("getMe")
    if api.call("getWebhookInfo").get("url"):
        raise SystemExit("У бота уже установлен webhook. Напишите мне: сначала проверим существующее подключение.")
    code = secrets.token_urlsafe(12)
    print(f"Откройте https://t.me/{me['username']} и отправьте ему команду:")
    print(f"/start {code}")
    print("Жду ваше сообщение до 5 минут…")
    owner = None
    offset = 0
    end = time.monotonic() + 300
    while time.monotonic() < end and owner is None:
        updates = api.call("getUpdates", {"offset": offset, "timeout": 10, "allowed_updates": ["message"]})
        for update in updates:
            offset = max(offset, update["update_id"] + 1)
            message = update.get("message", {})
            if (message.get("text", "").strip() == "/start " + code
                    and message.get("chat", {}).get("type") == "private"
                    and message.get("from", {}).get("id") == message["chat"]["id"]):
                owner = message["from"]["id"]
                break
    if owner is None:
        raise SystemExit("Сообщение не получено. Запустите настройку ещё раз.")
    # Confirm offsets so the pairing message doesn't become a learning command later.
    api.call("getUpdates", {"offset": offset, "timeout": 0})
    database = getpass.getpass("Строка подключения Neon (Enter — пока локальная база): ").strip()
    public_url = input("Адрес Render https://...onrender.com (Enter — добавить позже): ").strip()
    values = {
        "BOT_TOKEN": token, "OWNER_TELEGRAM_ID": str(owner),
        "DATABASE_URL": database or "sqlite:///bot.sqlite3", "WEBHOOK_SECRET": secrets.token_urlsafe(32),
        "CRON_SECRET": secrets.token_urlsafe(32), "PUBLIC_URL": public_url,
        "DEFAULT_TIMEZONE": "Europe/Moscow",
    }
    if any("\n" in v or "\r" in v for v in values.values()):
        raise SystemExit("Значения не должны содержать перевод строки.")
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as stream:
        # No interpolation or shell execution; quote to preserve # and other URL characters.
        for key, value in values.items():
            escaped = value.replace("\\", "\\\\").replace('"', '\\"')
            stream.write(f'{key}="{escaped}"\n')
    print("Готово: .env создан с доступом только для владельца файла. Токен не выведен на экран.")
    print(f"Telegram ID владельца: {owner}. /start в боте запустит урок после включения программы.")


if __name__ == "__main__":
    try:
        main()
    except TelegramError as error:
        raise SystemExit(f"Telegram недоступен или токен неверен (код {error.code}). Настройки не сохранены.") from None
