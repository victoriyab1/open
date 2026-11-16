import copy
from datetime import datetime, timezone

import pytest

from hsk_bot.app import create_app
from hsk_bot.config import Config
from hsk_bot.storage import Storage
from hsk_bot.telegram import TelegramError, flush_outbox


class FakeTelegram:
    def __init__(self):
        self.calls = []
        self.error = None

    def execute(self, effect, state):
        if self.error:
            raise self.error
        self.calls.append(copy.deepcopy(effect))


@pytest.fixture
def services(tmp_path):
    url = "sqlite:///" + str(tmp_path / "bot.sqlite3")
    storage = Storage(url)
    telegram = FakeTelegram()
    config = Config("123:test", 123456, url, "w" * 32, "c" * 32)
    app = create_app(config, storage, telegram)
    app.testing = True
    return storage, telegram, app.test_client()


def test_state_persists_and_rolls_back(tmp_path):
    url = "sqlite:///" + str(tmp_path / "bot.sqlite3")
    store = Storage(url)
    store.initialize()
    store.update(lambda s: s.update(user={"id": 1}))
    reopened = Storage(url)
    reopened.initialize()
    assert reopened.snapshot()["user"] == {"id": 1}
    def fail(state):
        state["user"]["id"] = 2
        raise ValueError("test failure")
    with pytest.raises(ValueError):
        store.update(fail)
    assert store.snapshot()["user"] == {"id": 1}


def test_webhook_requires_secret_and_owner(services):
    storage, telegram, client = services
    update = {"update_id": 1, "message": {"from": {"id": 123456},
              "chat": {"id": 123456, "type": "private"}, "text": "/start"}}
    assert client.post("/telegram", json=update).status_code == 401
    assert storage.snapshot()["user"] is None
    headers = {"X-Telegram-Bot-Api-Secret-Token": "w" * 32}
    assert client.post("/telegram", json=update, headers=headers).status_code == 200
    assert len(telegram.calls) == 2
    assert client.post("/telegram", json=update, headers=headers).status_code == 200
    assert len(telegram.calls) == 2
    update["update_id"] = 2
    update["message"]["from"]["id"] = 42
    client.post("/telegram", json=update, headers=headers)
    assert len(telegram.calls) == 2


def test_cron_requires_separate_secret(services):
    _, _, client = services
    assert client.post("/cron").status_code == 401
    assert client.post("/cron", headers={"Authorization": "Bearer " + "w" * 32}).status_code == 401
    assert client.post("/cron", headers={"Authorization": "Bearer " + "c" * 32}).status_code == 200
    assert client.get("/cron").status_code == 405


def test_invalid_update_body(services):
    _, _, client = services
    headers = {"X-Telegram-Bot-Api-Secret-Token": "w" * 32}
    assert client.post("/telegram", json=[], headers=headers).status_code == 400
    assert client.get("/health").json["status"] == "ok"


def test_durable_outbox_on_network_error(services):
    storage, telegram, _ = services
    storage.update(lambda s: s["outbox"].append({"method": "sendMessage", "payload": {"text": "test"}}))
    telegram.error = TelegramError(429, retry_after=5)
    assert not flush_outbox(storage, telegram)
    assert len(storage.snapshot()["outbox"]) == 1
    telegram.error = None
    storage.update(lambda s: s["outbox"][0].pop("not_before"))
    assert flush_outbox(storage, telegram)
    assert not storage.snapshot()["outbox"]
    assert len(telegram.calls) == 1


def test_expired_daily_cards_are_not_sent_next_day(services):
    storage, telegram, _ = services
    storage.update(lambda s: s["outbox"].append({"method": "sendMessage", "payload": {"text": "old"}, "expires": 1}))
    assert flush_outbox(storage, telegram)
    assert not telegram.calls


def test_webhook_network_failure_does_not_repeat_learning_transition(services):
    storage, telegram, client = services
    update = {"update_id": 100, "message": {"from": {"id": 123456},
              "chat": {"id": 123456, "type": "private"}, "text": "/start"}}
    headers = {"X-Telegram-Bot-Api-Secret-Token": "w" * 32}
    telegram.error = TelegramError()
    assert client.post("/telegram", json=update, headers=headers).status_code == 503
    assert len(storage.snapshot()["outbox"]) == 2
    telegram.error = None
    storage.update(lambda s: s["outbox"][0].pop("not_before"))
    assert client.post("/telegram", json=update, headers=headers).status_code == 200
    assert len(telegram.calls) == 2
    assert storage.snapshot()["processed"] == [100]


def test_minutely_cron_does_not_keep_database_awake_outside_noon(services, monkeypatch):
    storage, telegram, _ = services
    config = Config("123:test", 123456, storage.url, "w" * 32, "c" * 32)
    app = create_app(config, storage, telegram, clock=lambda: datetime(2026, 11, 16, 1, tzinfo=timezone.utc))
    client = app.test_client()
    def unexpected_database_call(*_args):
        raise AssertionError("Cron outside noon should not query an idle database")
    monkeypatch.setattr(storage, "update", unexpected_database_call)
    for _ in range(5):
        result = client.post("/cron", headers={"Authorization": "Bearer " + "c" * 32})
        assert result.status_code == 200
        assert result.json == {"ok": True, "lesson_created": False}


def test_app_scheduler_uses_updated_timezone_and_survives_restart(services):
    storage, telegram, _ = services
    config = Config("123:test", 123456, storage.url, "w" * 32, "c" * 32)
    now = [datetime(2026, 11, 16, 8, tzinfo=timezone.utc)]
    app = create_app(config, storage, telegram, clock=lambda: now[0])
    client = app.test_client()
    headers = {"X-Telegram-Bot-Api-Secret-Token": "w" * 32}
    def message(text, update_id):
        return {"update_id": update_id, "message": {"from": {"id": 123456},
                "chat": {"id": 123456, "type": "private"}, "text": text}}
    client.post("/telegram", json=message("/start", 1), headers=headers)
    client.post("/telegram", json=message("/timezone Asia/Kathmandu", 2), headers=headers)
    now[0] = datetime(2026, 11, 17, 6, 15, tzinfo=timezone.utc)
    restarted = create_app(config, storage, telegram, clock=lambda: now[0]).test_client()
    cron_headers = {"Authorization": "Bearer " + "c" * 32}
    assert restarted.post("/cron", headers=cron_headers).json["lesson_created"]
    assert not restarted.post("/cron", headers=cron_headers).json["lesson_created"]
    assert len(storage.snapshot()["user"]["words"]) == 6
