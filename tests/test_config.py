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


@pytest.mark.parametrize("wrapper", ['"{}"', "'{}'", 'BOT_TOKEN="{}"'])
def test_token_copied_with_dotenv_quotes_or_assignment(monkeypatch, wrapper):
    env(monkeypatch)
    monkeypatch.setenv("BOT_TOKEN", wrapper.format("123:offline-test"))
    assert Config.from_env().token == "123:offline-test"


def test_missing_fields_are_reported_together_without_values(monkeypatch):
    env(monkeypatch)
    monkeypatch.delenv("BOT_TOKEN")
    monkeypatch.delenv("CRON_SECRET")
    with pytest.raises(ValueError) as error:
        Config.from_env()
    assert "Missing environment variables: BOT_TOKEN, CRON_SECRET" in str(error.value)
    assert "w" * 32 not in str(error.value)


def test_full_export_pasted_into_single_value_is_rejected(monkeypatch):
    env(monkeypatch)
    monkeypatch.setenv("BOT_TOKEN", "BOT_TOKEN=123:offline-test\nDATABASE_URL=secret")
    with pytest.raises(ValueError, match="BOT_TOKEN has invalid format") as error:
        Config.from_env()
    assert "123:offline-test" not in str(error.value)
