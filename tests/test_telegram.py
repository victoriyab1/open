import requests
import pytest

from hsk_bot.telegram import Telegram, TelegramError


class RecordingTelegram(Telegram):
    def __init__(self):
        self.calls = []
        self.invalid_cached_file = False

    def call(self, method, payload=None, files=None):
        self.calls.append((method, payload, bool(files)))
        if self.invalid_cached_file and method == "sendAudio" and not files:
            self.invalid_cached_file = False
            raise TelegramError(400)
        return {"audio": {"file_id": "cached-telegram-file"}}


@pytest.fixture
def audio(tmp_path, monkeypatch):
    monkeypatch.setattr("hsk_bot.telegram.tempfile.gettempdir", lambda: str(tmp_path))
    class Speech:
        def __init__(self, *args, **kwargs):
            assert kwargs["lang"] == "zh-CN"
        def write_to_fp(self, stream):
            stream.write(b"audio-test-data")
    monkeypatch.setattr("hsk_bot.telegram.gTTS", Speech)
    effect = {"method": "audio", "payload": {"word_id": 147, "text": "你好", "chat_id": 123456,
               "caption": "你好 · nǐ hǎo", "failure_text": "Audio unavailable"}}
    return effect


def test_audio_upload_and_persistent_telegram_cache(audio):
    api = RecordingTelegram()
    state = {}
    api.execute(audio, state)
    assert api.calls[0][0] == "sendAudio"
    assert api.calls[0][2]  # Uploaded file first time.
    assert state["audio_files"]["147"] == "cached-telegram-file"
    api.execute(audio, state)
    assert not api.calls[-1][2]  # Reuse Telegram file_id, don't regenerate audio.
    assert api.calls[-1][1]["audio"] == "cached-telegram-file"


def test_expired_audio_file_id_regenerates(audio):
    api = RecordingTelegram()
    api.invalid_cached_file = True
    state = {"audio_files": {"147": "expired"}}
    api.execute(audio, state)
    assert len(api.calls) == 2
    assert api.calls[-1][2]
    assert state["audio_files"]["147"] == "cached-telegram-file"


def test_speech_service_failure_gives_text_fallback(audio, monkeypatch):
    def fail(*_args, **_kwargs):
        raise RuntimeError("Speech unavailable")
    monkeypatch.setattr("hsk_bot.telegram.gTTS", fail)
    api = RecordingTelegram()
    api.execute(audio, {})
    assert api.calls[-1][0] == "sendMessage"
    assert api.calls[-1][1]["text"] == "Audio unavailable"


def test_network_exception_does_not_expose_token(monkeypatch):
    api = Telegram("123:secret-token-never-log")
    def fail(*_args, **_kwargs):
        raise requests.ConnectionError(api.base)
    monkeypatch.setattr(api.http, "post", fail)
    with pytest.raises(TelegramError) as error:
        api.call("getMe")
    assert "secret-token" not in str(error.value)


def test_expired_feedback_message_falls_back_to_new_message():
    api = RecordingTelegram()
    original = api.call
    def call(method, payload=None, files=None):
        if method == "editMessageText":
            raise TelegramError(400)
        return original(method, payload, files)
    api.call = call
    api.execute({"method": "editMessageText", "payload": {"chat_id": 123456, "message_id": 1,
                 "text": "✅ Correct", "reply_markup": {"inline_keyboard": []}}}, {})
    assert api.calls[-1][0] == "sendMessage"
    assert "message_id" not in api.calls[-1][1]
