"""Learning state machine. No network or database calls; easy to test with a clock."""
import random
import secrets
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .i18n import tr

# Equivalent/overlapping meanings must not appear as mutually exclusive options.
MEANING_GROUPS = (
    {7, 255}, {39, 174}, {42, 43, 99}, {54, 79, 206, 285}, {55, 56}, {69, 290},
    {49, 111}, {88, 101}, {105, 268}, {133, 134}, {136, 138}, {139, 140},
    {178, 275}, {185, 186}, {194, 195}, {196, 197}, {212, 213}, {244, 245},
    {247, 249}, {233, 252}, {239, 258, 264, 266}, {256, 265}, {279, 281}, {282, 283},
    {82, 102, 145}, {73, 158}, {12, 183}, {15, 13}, {131, 132, 135},
)


def button(text, data):
    return {"text": text, "callback_data": data}


def keyboard(rows):
    return {"inline_keyboard": rows}


def local_day(user, now):
    return now.astimezone(ZoneInfo(user["timezone"])).date()


def schedule_word(progress, correct, today, practice=False):
    """One decision per word (translation AND pinyin must both be correct).

    Early successful extra practice doesn't move the Leitner schedule forward.
    A mistake during any practice resets the word to tomorrow.
    """
    progress["attempts"] += 1
    progress["tested"] = True
    if correct:
        progress["correct"] += 1
        if not practice or date.fromisoformat(progress["due"]) <= today:
            progress["stage"] = min(2, progress["stage"] + 1)
            days = 3 if progress["stage"] == 1 else 7
            progress["due"] = (today + timedelta(days=days)).isoformat()
    else:
        progress["errors"] += 1
        progress["stage"] = 0
        progress["due"] = (today + timedelta(days=1)).isoformat()


def weighted_sample(ids, progress, count, rng):
    """Weighted random draw WITHOUT replacement; error count increases weight."""
    candidates = list(ids)
    selected = []
    while candidates and len(selected) < count:
        weights = [1 + 2 * progress[str(i)]["errors"] for i in candidates]
        pick = rng.choices(candidates, weights=weights, k=1)[0]
        candidates.remove(pick)
        selected.append(pick)
    return selected


class Engine:
    def __init__(self, words, owner_id, default_timezone="Europe/Moscow", rng=None):
        self.words = words
        self.by_id = {w["id"]: w for w in words}
        self.owner = owner_id
        self.default_timezone = default_timezone
        self.rng = rng or random.SystemRandom()

    def emit(self, state, method, **payload):
        state["outbox"].append({"method": method, "payload": payload})

    def send(self, state, user, text, rows=None, expires=None):
        payload = {"chat_id": user["id"], "text": text}
        if rows is not None:
            payload["reply_markup"] = keyboard(rows)
        self.emit(state, "sendMessage", **payload)
        if expires:
            state["outbox"][-1]["expires"] = expires

    def language(self, state, user):
        self.send(state, user, tr(user, "language"), [
            [button("Русский 🇷🇺", "lang:ru"), button("English 🇬🇧", "lang:en")]
        ])

    def timezones(self, state, user):
        zones = ["Europe/Moscow", "Europe/London", "Europe/Berlin", "Asia/Shanghai",
                 "Asia/Yekaterinburg", "Asia/Novosibirsk", "Asia/Vladivostok", "America/New_York"]
        self.send(state, user, tr(user, "timezone"), [
            [button(z, "tz:" + z)] for z in zones
        ])

    def handle(self, state, update, now=None):
        now = now or datetime.now(timezone.utc)
        callback = update.get("callback_query")
        message = callback.get("message", {}) if callback else update.get("message", {})
        sender = callback.get("from", {}) if callback else message.get("from", {})
        # Fail closed: only owner's own private chat, never groups or forwarded identities.
        if sender.get("id") != self.owner or message.get("chat", {}).get("id") != self.owner:
            return False
        if message.get("chat", {}).get("type") != "private":
            return False
        update_id = update.get("update_id")
        if not isinstance(update_id, int) or update_id in state["processed"]:
            return False
        state["processed"] = (state["processed"] + [update_id])[-1024:]
        if state["user"] is None:
            state["user"] = {"id": self.owner, "lang": "ru", "timezone": self.default_timezone,
                             "onboarded": False, "paused": False, "words": {}, "days": {}, "session": None}
        user = state["user"]
        if callback:
            self.emit(state, "answerCallbackQuery", callback_query_id=callback["id"])
            self.callback(state, user, callback, now)
            return True
        text = message.get("text", "").strip()
        pieces = text.split(maxsplit=1)
        cmd = pieces[0].split("@")[0].lower() if pieces else ""
        if cmd == "/start":
            if user["onboarded"]:
                self.send(state, user, tr(user, "home"))
            else:
                self.send(state, user, tr(user, "welcome"))
                self.language(state, user)
        elif cmd in ("/language", "/lang"):
            self.language(state, user)
        elif cmd == "/timezone":
            if len(pieces) == 2:
                self.set_timezone(state, user, pieces[1], now)
            else:
                self.timezones(state, user)
        elif cmd == "/settings":
            self.send(state, user, tr(user, "settings"), [
                [button(tr(user, "set_language"), "settings:lang")],
                [button(tr(user, "set_timezone"), "settings:tz")],
            ])
        elif cmd in ("/lesson", "/practice"):
            if user["onboarded"]:
                self.lesson(state, user, now)
            else:
                self.language(state, user)
        elif cmd == "/archive":
            self.archive_page(state, user, 0)
        elif cmd == "/stats":
            self.stats(state, user, now)
        elif cmd in ("/pause", "/resume"):
            user["paused"] = cmd == "/pause"
            self.send(state, user, tr(user, "paused" if user["paused"] else "resumed"))
        elif cmd == "/privacy":
            self.send(state, user, tr(user, "privacy"))
        elif cmd == "/delete_me":
            nonce = secrets.token_hex(5)
            user["delete_nonce"] = nonce
            self.send(state, user, tr(user, "delete"), [
                [button(tr(user, "confirm_delete"), "delete:" + nonce)],
                [button(tr(user, "cancel"), "cancel_delete")],
            ])
        else:
            self.send(state, user, tr(user, "home"))
        return True

    def set_timezone(self, state, user, zone, now):
        try:
            ZoneInfo(zone)
        except (ZoneInfoNotFoundError, ValueError):
            self.send(state, user, tr(user, "bad_timezone"))
            return
        first = not user["onboarded"]
        user["timezone"] = zone
        user["onboarded"] = True
        self.send(state, user, tr(user, "saved") + "\n🕛 " + zone)
        self.send(state, user, tr(user, "home"))
        if first:
            self.lesson(state, user, now)

    def callback(self, state, user, callback, now):
        data = callback.get("data", "")
        if data == "noop":
            return
        if data == "settings:lang":
            self.language(state, user)
        elif data == "settings:tz":
            self.timezones(state, user)
        elif data.startswith("lang:") and data[5:] in ("ru", "en"):
            user["lang"] = data[5:]
            session = user.get("session")
            if session and session.get("q") and session["q"]["selected"] is None:
                session["q"] = None  # Old question buttons expire, new labels use the new language.
            self.send(state, user, tr(user, "saved"))
            if not user["onboarded"]:
                self.timezones(state, user)
        elif data.startswith("tz:"):
            self.set_timezone(state, user, data[3:], now)
        elif data.startswith("audio:"):
            word = self.lookup_seen(user, data[6:])
            if word:
                self.emit(state, "audio", chat_id=user["id"], word_id=word["id"],
                          text=word["hanzi"], caption=word["hanzi"] + " · " + word["pinyin"],
                          failure_text=tr(user, "audio_failed"))
        elif data.startswith("archive:"):
            word = self.lookup_seen(user, data[8:])
            if word:
                user["words"][str(word["id"])]["archived"] = True
                self.send(state, user, tr(user, "archived"))
                session = self.active_session(user, local_day(user, now).isoformat())
                if (session and session["kind"] != "daily_new"
                        and session["ids"][session["pos"]] == word["id"]
                        and session.get("q") and session["q"]["selected"] is None):
                    session["pos"] += 1
                    session["field"], session["ok"], session["q"] = "translation", True, None
                    self.continue_session(state, user, now)
        elif data.startswith("restore:"):
            word = self.lookup_seen(user, data[8:])
            if word:
                progress = user["words"][str(word["id"])]
                progress["archived"] = False
                progress["stage"] = 0
                progress["due"] = (local_day(user, now) + timedelta(days=1)).isoformat()
                self.send(state, user, tr(user, "restored"))
        elif data.startswith("archive_page:"):
            try:
                page = max(0, int(data[13:]))
            except ValueError:
                return
            self.archive_page(state, user, page)
        elif data.startswith("begin:"):
            today = local_day(user, now).isoformat()
            if data[6:] == today and today in user["days"] and not user["days"][today]["new_done"]:
                if not self.active_session(user, today):
                    self.start_session(user, user["days"][today]["new"], "daily_new", today)
                self.ask(state, user, now)
            else:
                self.send(state, user, tr(user, "stale"))
        elif data.startswith("a:"):
            self.answer(state, user, callback, now)
        elif data.startswith("n:"):
            self.next_question(state, user, data[2:], now)
        elif data == "cancel_delete":
            user.pop("delete_nonce", None)
            self.send(state, user, tr(user, "home"))
        elif data.startswith("delete:"):
            if data[7:] == user.get("delete_nonce"):
                state["outbox"] = []
                self.send(state, user, tr(user, "deleted"))
                state["user"] = None
                state.pop("pronunciation_message", None)
                state["processed"] = [state["processed"][-1]]

    def lookup_seen(self, user, value):
        try:
            wid = int(value)
        except ValueError:
            return None
        return self.by_id.get(wid) if str(wid) in user["words"] else None

    def day(self, user, now):
        today = local_day(user, now)
        key = today.isoformat()
        if key not in user["days"]:
            due = [int(wid) for wid, p in user["words"].items()
                   if not p["archived"] and p["due"] <= key]
            review = weighted_sample(due, user["words"], 2, self.rng)
            fresh = [w["id"] for w in self.words if str(w["id"]) not in user["words"]][:3]
            for wid in fresh:
                user["words"][str(wid)] = {
                    "stage": 0, "due": (today + timedelta(days=1)).isoformat(),
                    "errors": 0, "attempts": 0, "correct": 0, "archived": False, "tested": False,
                }
            user["days"][key] = {"new": fresh, "review": review, "new_done": not fresh,
                                 "review_done": False, "right": 0, "total": 0}
        return user["days"][key]

    def active_session(self, user, today):
        session = user.get("session")
        if session and session["day"] != today:
            user["session"] = None
            return None
        return session

    def lesson(self, state, user, now):
        today = local_day(user, now).isoformat()
        day = self.day(user, now)
        if self.active_session(user, today):
            self.ask(state, user, now)
        elif not day["new_done"]:
            self.cards(state, user, day, today)
        elif not day["review_done"]:
            self.begin_reviews(state, user, now)
        else:
            ids = [int(wid) for wid, p in user["words"].items() if not p["archived"] and p["tested"]]
            ids = weighted_sample(ids, user["words"], 2, self.rng)
            if not ids:
                self.send(state, user, tr(user, "nothing"))
                return
            self.start_session(user, ids, "practice", today)
            self.ask(state, user, now)

    def cards(self, state, user, day, today):
        texts = [tr(user, "learn")]
        rows = []
        for wid in day["new"]:
            w = self.by_id[wid]
            texts.append(f"{w['hanzi']} · {w['pinyin']}\n{w[user['lang']]}\n"
                         f"{tr(user, 'example_label')}: {w['example']}\n{w['example_pinyin']}\n{w['example_' + user['lang']]}")
            rows.append([button("🔊 " + w["hanzi"], f"audio:{wid}")])
        # Expire morning/new-card deliveries at the end of their local calendar day.
        end = datetime.combine(date.fromisoformat(today) + timedelta(days=1), datetime.min.time(),
                               ZoneInfo(user["timezone"])).timestamp()
        self.send(state, user, "\n\n".join(texts), rows, expires=end)
        self.send(state, user, tr(user, "start"), [[button(tr(user, "start"), "begin:" + today)]], expires=end)

    def start_session(self, user, ids, kind, today):
        user["session"] = {"ids": ids, "kind": kind, "day": today, "pos": 0,
                           "field": "translation", "ok": True, "q": None, "right": 0, "total": 0}

    def begin_reviews(self, state, user, now):
        day = self.day(user, now)
        today = local_day(user, now).isoformat()
        ids = [wid for wid in day["review"] if not user["words"][str(wid)]["archived"]
               and user["words"][str(wid)]["due"] <= today]
        if ids:
            self.send(state, user, tr(user, "review"))
            self.start_session(user, ids, "daily_review", local_day(user, now).isoformat())
            self.ask(state, user, now)
        else:
            day["review_done"] = True
            self.send(state, user, tr(user, "done", right=day["right"], total=day["total"]))
            if not day["new"]:
                self.send(state, user, tr(user, "finished"))

    def make_question(self, user, session):
        word = self.by_id[session["ids"][session["pos"]]]
        field = user["lang"] if session["field"] == "translation" else "pinyin"
        answer = word[field]
        excluded = set()
        if field != "pinyin":
            for group in MEANING_GROUPS:
                if word["id"] in group:
                    excluded.update(group)
        # Daily new words supply the three shuffled options where values are distinct.
        pool = [self.by_id[i][field] for i in session["ids"] if i not in excluded]
        pool += [w[field] for w in self.words if w["id"] not in excluded]
        unique = list(dict.fromkeys(v for v in pool if v != answer))
        primary = list(dict.fromkeys(self.by_id[i][field] for i in session["ids"]
                                    if i not in excluded and self.by_id[i][field] != answer))
        others = [v for v in unique if v not in primary]
        self.rng.shuffle(others)
        options = [answer] + (primary + others)[:2]
        self.rng.shuffle(options)
        return {"nonce": secrets.token_hex(5), "options": options, "correct": options.index(answer), "selected": None}

    def question_view(self, user, session):
        w = self.by_id[session["ids"][session["pos"]]]
        q = session["q"]
        title = "practice" if session["kind"] == "practice" else "lesson"
        field = "translate" if session["field"] == "translation" else "pinyin"
        text = f"{tr(user, title)} · {session['pos'] + 1}/{len(session['ids'])}\n\n{w['hanzi']}\n{tr(user, field)}"
        rows = []
        for i, value in enumerate(q["options"]):
            prefix = ""
            if q["selected"] is not None:
                if i == q["correct"]:
                    prefix = "✅ "
                elif i == q["selected"]:
                    prefix = "❌ "
            action = f"a:{q['nonce']}:{i}" if q["selected"] is None else "noop"
            rows.append([button(prefix + value, action)])
        if q["selected"] is not None:
            text += "\n\n" + tr(user, "correct" if q["selected"] == q["correct"] else "wrong")
            rows.append([button(tr(user, "next"), "n:" + q["nonce"])])
            # Audio only after the pinyin answer, so it doesn't reveal the answer beforehand.
            if session["field"] == "pinyin":
                rows.append([button(tr(user, "audio"), f"audio:{w['id']}")])
                rows.append([button(tr(user, "archive_word"), f"archive:{w['id']}")])
        return text, rows

    def ask(self, state, user, now):
        session = self.active_session(user, local_day(user, now).isoformat())
        if not session:
            self.send(state, user, tr(user, "stale"))
            return
        if session["q"] is None:
            session["q"] = self.make_question(user, session)
        text, rows = self.question_view(user, session)
        self.send(state, user, text, rows)

    def answer(self, state, user, callback, now):
        session = self.active_session(user, local_day(user, now).isoformat())
        parts = callback["data"].split(":")
        if not session or not session.get("q") or len(parts) != 3:
            self.send(state, user, tr(user, "stale"))
            return
        q = session["q"]
        if parts[1] != q["nonce"] or q["selected"] is not None:
            return
        try:
            choice = int(parts[2])
        except ValueError:
            return
        if choice not in range(len(q["options"])):
            return
        q["selected"] = choice
        session["ok"] = session["ok"] and choice == q["correct"]
        # Commit the word when the second answer arrives, not when Continue is tapped.
        if session["field"] == "pinyin":
            wid = session["ids"][session["pos"]]
            schedule_word(user["words"][str(wid)], session["ok"], local_day(user, now),
                          practice=session["kind"] == "practice")
            session["right"] += int(session["ok"])
            session["total"] += 1
            if session["kind"] != "practice":
                day = user["days"][session["day"]]
                day["right"] += int(session["ok"])
                day["total"] += 1
        text, rows = self.question_view(user, session)
        self.emit(state, "editMessageText", chat_id=user["id"],
                  message_id=callback["message"]["message_id"], text=text, reply_markup=keyboard(rows))

    def next_question(self, state, user, nonce, now):
        session = self.active_session(user, local_day(user, now).isoformat())
        if not session or not session.get("q"):
            self.send(state, user, tr(user, "stale"))
            return
        q = session["q"]
        if nonce != q["nonce"] or q["selected"] is None:
            return
        if session["field"] == "translation":
            session["field"] = "pinyin"
        else:
            session["pos"] += 1
            session["field"] = "translation"
            session["ok"] = True
        session["q"] = None
        self.continue_session(state, user, now)

    def continue_session(self, state, user, now):
        session = user["session"]
        if session["kind"] != "daily_new":
            while (session["pos"] < len(session["ids"])
                   and user["words"][str(session["ids"][session["pos"]])]["archived"]):
                session["pos"] += 1
                session["field"], session["ok"], session["q"] = "translation", True, None
        if session["pos"] < len(session["ids"]):
            self.ask(state, user, now)
            return
        kind = session["kind"]
        user["session"] = None
        if kind == "daily_new":
            user["days"][session["day"]]["new_done"] = True
            self.begin_reviews(state, user, now)
        elif kind == "daily_review":
            day = user["days"][session["day"]]
            day["review_done"] = True
            self.send(state, user, tr(user, "done", right=day["right"], total=day["total"]))
        else:
            self.send(state, user, tr(user, "done", right=session["right"], total=session["total"]))

    def archive_page(self, state, user, page):
        ids = [int(wid) for wid, p in user["words"].items() if p["archived"]]
        if not ids:
            self.send(state, user, tr(user, "empty_archive"))
            return
        page = min(page, (len(ids) - 1) // 8)
        rows = []
        for wid in ids[page * 8:(page + 1) * 8]:
            w = self.by_id[wid]
            rows.append([button(f"{w['hanzi']} · {w[user['lang']]} — {tr(user, 'restore')}", f"restore:{wid}")])
        nav = []
        if page:
            nav.append(button("←", f"archive_page:{page - 1}"))
        if (page + 1) * 8 < len(ids):
            nav.append(button("→", f"archive_page:{page + 1}"))
        if nav:
            rows.append(nav)
        self.send(state, user, tr(user, "archive"), rows)

    def stats(self, state, user, now):
        progress = list(user["words"].values())
        attempts = sum(p["attempts"] for p in progress)
        correct = sum(p["correct"] for p in progress)
        today = local_day(user, now).isoformat()
        self.send(state, user, tr(user, "stats", seen=len(progress), all=len(self.words),
                  tested=sum(p["tested"] for p in progress), archived=sum(p["archived"] for p in progress),
                  due=sum(not p["archived"] and p["due"] <= today for p in progress),
                  accuracy=round(100 * correct / attempts) if attempts else 0, timezone=user["timezone"]))

    def tick(self, state, now=None):
        now = now or datetime.now(timezone.utc)
        user = state["user"]
        if not user or not user["onboarded"] or user["paused"]:
            return False
        local = now.astimezone(ZoneInfo(user["timezone"]))
        # Only today's noon window; missed calendar days are never replayed.
        if local.hour != 12 or local.minute >= 15:
            return False
        today = local.date().isoformat()
        if today in user["days"]:
            return False
        self.lesson(state, user, now)
        return True
