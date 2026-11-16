import os
import re
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from dotenv import load_dotenv


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
        load_dotenv()
        token = os.environ.get("BOT_TOKEN", "").strip()
        owner = os.environ.get("OWNER_TELEGRAM_ID", "")
        webhook = os.environ.get("WEBHOOK_SECRET", "")
        cron = os.environ.get("CRON_SECRET", "")
        database = os.environ.get("DATABASE_URL", "")
        if not re.fullmatch(r"\d+:[A-Za-z0-9_-]+", token):
            raise ValueError("BOT_TOKEN is missing or invalid")
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
        timezone = os.environ.get("DEFAULT_TIMEZONE", "Europe/Moscow")
        ZoneInfo(timezone)
        return cls(token, int(owner), database, webhook, cron, timezone)
