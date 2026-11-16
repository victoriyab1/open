import os
import re
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv


def env_value(name, default=""):
    """Accept a value pasted alone or as one dotenv assignment, without logging it."""
    value = os.environ.get(name, default).strip()
    for _ in range(2):
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1].strip()
        if value.startswith(name + "="):
            value = value[len(name) + 1:].strip()
    return value


@dataclass(frozen=True)
class Config:
    token: str
    owner_id: int
    database_url: str
    webhook_secret: str
    cron_secret: str
    timezone: str = "Europe/Moscow"

    @classmethod
    def from_env(cls):
        env_file = os.environ.get("BOT_ENV_FILE", "").strip()
        if env_file:
            if not Path(env_file).is_file():
                raise ValueError("The secret file configured in BOT_ENV_FILE is missing. "
                                 "Check its filename in Render > Environment > Secret Files.")
            load_dotenv(dotenv_path=env_file, interpolate=False)
        else:
            load_dotenv()
        required = ("BOT_TOKEN", "OWNER_TELEGRAM_ID", "DATABASE_URL", "WEBHOOK_SECRET", "CRON_SECRET")
        values = {key: env_value(key) for key in required}
        missing = [key for key, value in values.items() if not value]
        if missing:
            raise ValueError("Missing environment variables: " + ", ".join(missing)
                             + ". In Render open Environment > Add from .env, then Save, rebuild, and deploy.")
        token = values["BOT_TOKEN"]
        owner = values["OWNER_TELEGRAM_ID"]
        webhook = values["WEBHOOK_SECRET"]
        cron = values["CRON_SECRET"]
        database = values["DATABASE_URL"]
        if not re.fullmatch(r"\d+:[A-Za-z0-9_-]+", token):
            raise ValueError("BOT_TOKEN has invalid format. Set BOT_TOKEN to the token from BotFather; "
                             "do not paste an API URL or multiple .env lines into its value.")
        if not owner.isdecimal() or int(owner) <= 0:
            raise ValueError("Set OWNER_TELEGRAM_ID to your positive numeric Telegram ID")
        if any(not re.fullmatch(r"[A-Za-z0-9_-]{32,256}", s) for s in (webhook, cron)):
            raise ValueError("WEBHOOK_SECRET and CRON_SECRET need 32+ URL-safe characters")
        if webhook == cron:
            raise ValueError("Use different webhook and cron secrets")
        if not database.startswith(("sqlite:///", "postgresql://", "postgres://")):
            raise ValueError("Set DATABASE_URL to a PostgreSQL or local SQLite URL")
        if os.environ.get("RENDER") and database.startswith("sqlite:"):
            raise ValueError("Render requires persistent PostgreSQL (Neon), not SQLite")
        timezone = env_value("DEFAULT_TIMEZONE", "Europe/Moscow")
        ZoneInfo(timezone)
        return cls(token, int(owner), database, webhook, cron, timezone)
