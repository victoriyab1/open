import pytest

from hsk_bot.config import Config


def env(monkeypatch):
    monkeypatch.setattr("hsk_bot.config.load_dotenv", lambda: None)
    monkeypatch.delenv("RENDER", raising=False)
    values = {"BOT_TOKEN": "123:offline-test", "OWNER_TELEGRAM_ID": "123456", "DATABASE_URL": "sqlite:///test.sqlite3",
              "WEBHOOK_SECRET": "w" * 32, "CRON_SECRET": "c" * 32, "DEFAULT_TIMEZONE": "Europe/Moscow"}
    for key, value in values.items():
        monkeypatch.setenv(key, value)


@pytest.mark.parametrize("key,value", [("OWNER_TELEGRAM_ID", ""), ("OWNER_TELEGRAM_ID", "-1"),
                                      ("BOT_TOKEN", ""), ("WEBHOOK_SECRET", "short"),
                                      ("CRON_SECRET", "w" * 32), ("DATABASE_URL", "")])
def test_config_fails_closed(monkeypatch, key, value):
    env(monkeypatch)
    monkeypatch.setenv(key, value)
    with pytest.raises(ValueError):
        Config.from_env()


def test_render_rejects_ephemeral_sqlite(monkeypatch):
    env(monkeypatch)
    monkeypatch.setenv("RENDER", "true")
    with pytest.raises(ValueError, match="persistent PostgreSQL"):
        Config.from_env()


def test_valid_local_config(monkeypatch):
    env(monkeypatch)
    assert Config.from_env().owner_id == 123456
