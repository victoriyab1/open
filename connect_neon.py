"""Save a Neon connection string with hidden input; never print credentials."""
import getpass
import os
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import psycopg
from dotenv import set_key


def validate_url(value):
    if any(c in value for c in "\r\n"):
        raise ValueError("Строка подключения должна занимать одну строку.")
    parsed = urlsplit(value)
    if parsed.scheme not in ("postgres", "postgresql") or not parsed.hostname or not parsed.username:
        raise ValueError("Нужна Connection string, начинающаяся с postgresql://, без команды psql.")
    if not parsed.hostname.endswith(".neon.tech"):
        raise ValueError("Ожидается адрес базы Neon с окончанием .neon.tech.")
    if parse_qs(parsed.query).get("sslmode", [""])[0] not in ("require", "verify-ca", "verify-full"):
        raise ValueError("Скопируйте строку Neon целиком, включая sslmode=require.")
    return value


def main():
    target = Path(__file__).resolve().parent / ".env"
    if not target.exists():
        raise SystemExit("Сначала запустите setup.command для настройки токена.")
    print("Neon: откройте проект → Connect → Connection string.")
    print("Включите Connection pooling и скопируйте только строку postgresql://…")
    try:
        value = validate_url(getpass.getpass("Строка подключения (ввод скрыт): ").strip())
    except ValueError as error:
        raise SystemExit(str(error)) from None
    try:
        with psycopg.connect(value, connect_timeout=15) as connection:
            connection.execute("SELECT 1").fetchone()
    except psycopg.Error:
        raise SystemExit("Не удалось подключиться к Neon. Проверьте строку и сеть. Настройки не изменены.") from None
    set_key(str(target), "DATABASE_URL", value, quote_mode="always")
    os.chmod(target, 0o600)
    print("Готово: подключение проверено, DATABASE_URL сохранён в .env. Другие настройки сохранены.")
    print("Саму строку подключения в чат не отправляйте. Напишите только: «Neon готов».")


if __name__ == "__main__":
    main()
