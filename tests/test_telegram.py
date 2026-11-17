import requests
import pytest

from hsk_bot.telegram import Telegram, TelegramError


class RecordingTelegram(Telegram):
    def __init__(self):
        self.calls = []
        self.uploads = []
        self.invalid_cached_file = False
        self.delete_error = None
        self.message_id = 10

    def call(self, method, payload=None, files=None):
        self.calls.append((method, dict(payload or {}), bool(files)))
        if files:
            self.uploads.append({key: (value[0], value[2]) for key, value in files.items()})
        if method == "deleteMessage":
            if self.delete_error:
                raise self.delete_error
            return True
        if self.invalid_cached_file and method == "sendVoice" and not files:
            self.invalid_cached_file = False
            raise TelegramError(400)
        self.message_id += 1
        if method == "sendVoice":
            return {"message_id": self.message_id, "voice": {"file_id": "cached-voice-file"}}
        return {"message_id": self.message_id}


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
    assert api.calls[0][0] == "sendVoice"
    assert api.calls[0][2]  # Uploaded file first time.
    assert api.uploads == [{"voice": ("word-147.mp3", "audio/mpeg")}]
    assert state["pronunciation_voices"]["147"] == "cached-voice-file"
    api.execute(audio, state)
    assert not api.calls[-1][2]  # Reuse Telegram file_id, don't regenerate audio.
    assert api.calls[-1][1]["voice"] == "cached-voice-file"


def test_expired_audio_file_id_regenerates(audio):
    api = RecordingTelegram()
    api.invalid_cached_file = True
    state = {"pronunciation_voices": {"147": "expired"}}
    api.execute(audio, state)
    assert len(api.calls) == 2
    assert api.calls[-1][2]
    assert "voice" not in api.calls[-1][1]  # Do not send an expired ID alongside the upload.
    assert state["pronunciation_voices"]["147"] == "cached-voice-file"


def test_legacy_music_cache_is_not_reused_for_pronunciation(audio):
    api = RecordingTelegram()
    state = {"audio_files": {"147": "legacy-music-file", "146": "another-music-file"},
             "pronunciation_documents": {"147": "legacy-document"}}
    api.execute(audio, state)
    assert len(api.calls) == 1
    assert api.calls[0][0] == "sendVoice"
    assert api.calls[0][2]
    assert "audio" not in api.calls[0][1]
    assert state["pronunciation_voices"]["147"] == "cached-voice-file"


def test_click_sends_only_the_selected_word(audio):
    api = RecordingTelegram()
    state = {"pronunciation_voices": {"147": "selected-word", "146": "other-word", "221": "third-word"}}
    api.execute(audio, state)
    assert api.calls == [("sendVoice", {"chat_id": 123456, "caption": "你好 · nǐ hǎo",
                                       "voice": "selected-word"}, False)]


def test_next_word_replaces_the_previous_voice_player(audio):
    api = RecordingTelegram()
    state = {"pronunciation_voices": {"147": "hello", "146": "you"}}
    first = api.execute(audio, state)
    second = {"method": "audio", "payload": {**audio["payload"], "word_id": 146,
                                                "text": "你", "caption": "你 · nǐ"}}
    api.execute(second, state)
    assert [method for method, _, _ in api.calls] == ["sendVoice", "deleteMessage", "sendVoice"]
    assert api.calls[1][1] == {"chat_id": 123456, "message_id": first["message_id"]}
    assert api.calls[-1][1]["voice"] == "you"
    assert state["pronunciation_message"]["message_id"] != first["message_id"]


def test_manually_removed_previous_voice_does_not_block_next_word(audio):
    api = RecordingTelegram()
    api.delete_error = TelegramError(400)
    state = {"pronunciation_message": {"chat_id": 123456, "message_id": 2}}
    api.execute(audio, state)
    assert [method for method, _, _ in api.calls] == ["deleteMessage", "sendVoice"]
    assert state["pronunciation_message"]["message_id"] != 2


def test_transient_delete_failure_is_retried_before_creating_another_player(audio):
    api = RecordingTelegram()
    api.delete_error = TelegramError(429)
    state = {"pronunciation_message": {"chat_id": 123456, "message_id": 2},
             "pronunciation_voices": {"147": "hello"}}
    with pytest.raises(TelegramError):
        api.execute(audio, state)
    assert [method for method, _, _ in api.calls] == ["deleteMessage"]
    assert state["pronunciation_message"]["message_id"] == 2


def test_previous_player_from_another_chat_is_never_deleted(audio):
    api = RecordingTelegram()
    state = {"pronunciation_message": {"chat_id": 42, "message_id": 2}}
    api.execute(audio, state)
    assert [method for method, _, _ in api.calls] == ["sendVoice"]


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
