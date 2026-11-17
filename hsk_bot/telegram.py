import io
import logging
import time
from pathlib import Path
import tempfile

import requests
from gtts import gTTS

log = logging.getLogger(__name__)


class TelegramError(Exception):
    def __init__(self, code=0, retry_after=30):
        super().__init__(f"Telegram API error {code}")  # Never include token-bearing URLs.
        self.code = code
        self.retry_after = retry_after


class Telegram:
    def __init__(self, token):
        self.base = f"https://api.telegram.org/bot{token}/"
        self.http = requests.Session()

    def call(self, method, payload=None, files=None):
        try:
            response = self.http.post(self.base + method, json=payload if not files else None,
                                      data=payload if files else None, files=files, timeout=(5, 20))
            body = response.json()
        except (requests.RequestException, ValueError):
            raise TelegramError() from None
        if not body.get("ok"):
            raise TelegramError(body.get("error_code", response.status_code),
                                body.get("parameters", {}).get("retry_after", 30))
        return body["result"]

    def send_pronunciation_voice(self, state, payload, files=None):
        """Keep one bot-created voice player instead of building a media queue."""
        previous = state.get("pronunciation_message")
        if previous and previous["chat_id"] == payload["chat_id"]:
            try:
                self.call("deleteMessage", {"chat_id": previous["chat_id"],
                                            "message_id": previous["message_id"]})
            except TelegramError as error:
                if error.code != 400:
                    raise
                # Already removed or older than Telegram's deletion window.
                log.info("Previous pronunciation message is no longer removable")
            state.pop("pronunciation_message", None)
        result = self.call("sendVoice", payload, files=files)
        if "voice" in result:
            state["pronunciation_message"] = {"chat_id": payload["chat_id"],
                                                "message_id": result["message_id"]}
        return result

    def execute(self, effect, state):
        method, payload = effect["method"], effect["payload"]
        if method == "audio":
            wid = str(payload["word_id"])
            # Voice messages play inside the chat. Never reuse music/document
            # IDs: Telegram preserves the original attachment's media type.
            cache = state.setdefault("pronunciation_voices", {})
            voice = cache.get(wid)
            args = {"chat_id": payload["chat_id"], "caption": payload["caption"]}
            if voice:
                args["voice"] = voice
                try:
                    return self.send_pronunciation_voice(state, args)
                except TelegramError as error:
                    if error.code != 400:
                        raise
                    cache.pop(wid, None)
                    args.pop("voice", None)
            # Cache only public Chinese word recordings on disk; no user data.
            folder = Path(tempfile.gettempdir()) / "hsk-word-audio"
            folder.mkdir(exist_ok=True)
            path = folder / f"{wid}.mp3"
            try:
                if not path.exists():
                    stream = io.BytesIO()
                    gTTS(payload["text"], lang="zh-CN", timeout=(5, 10)).write_to_fp(stream)
                    path.write_bytes(stream.getvalue())
                with path.open("rb") as stream:
                    result = self.send_pronunciation_voice(
                        state, args, files={"voice": (f"word-{wid}.mp3", stream, "audio/mpeg")})
                cache[wid] = result["voice"]["file_id"]
                return result
            except TelegramError as error:
                if error.code != 400:
                    raise
                path.unlink(missing_ok=True)
                return self.call("sendMessage", {"chat_id": payload["chat_id"], "text": payload["failure_text"]})
            except Exception:
                log.warning("Speech generation failed; sending text fallback")
                return self.call("sendMessage", {"chat_id": payload["chat_id"], "text": payload["failure_text"]})
        try:
            return self.call(method, payload)
        except TelegramError as error:
            if method == "answerCallbackQuery" and error.code == 400:
                return None  # Callback confirmation has expired; the learning answer still applies.
            if method == "editMessageText" and error.code == 400:
                # Deleted/old message: keep feedback accessible in a new message instead.
                fallback = {k: v for k, v in payload.items() if k != "message_id"}
                return self.call("sendMessage", fallback)
            raise


def flush_outbox(storage, telegram, limit=8):
    """Durable, ordered, at-least-once delivery; caller retries after network errors.

    Telegram has no idempotency key for sendMessage. A process crash between an
    accepted Telegram request and the DB commit can duplicate that message, but
    the learning result cannot be applied twice (persisted nonce/update guards).
    """
    for _ in range(limit):
        def deliver(state):
            if not state["outbox"]:
                return "empty"
            effect = state["outbox"][0]
            if effect.get("expires", float("inf")) <= time.time():
                state["outbox"].pop(0)
                return "delivered"
            if effect.get("not_before", 0) > time.time():
                return "waiting"
            try:
                telegram.execute(effect, state)
            except TelegramError as error:
                if error.code == 403:
                    if state["user"]:
                        state["user"]["paused"] = True
                    state["outbox"] = []
                    return "empty"
                effect["not_before"] = time.time() + max(1, error.retry_after)
                log.warning("Telegram delivery deferred (code=%s)", error.code)
                return "waiting"
            state["outbox"].pop(0)
            return "delivered"
        status = storage.update(deliver)
        if status != "delivered":
            return status != "waiting"
    return storage.update(lambda s: not s["outbox"])
