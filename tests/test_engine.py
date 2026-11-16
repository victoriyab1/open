import copy
import json
import random
from datetime import datetime, date, timedelta, timezone

import pytest

from hsk_bot.curriculum import DATA_DIR, load_words
from hsk_bot.engine import Engine, MEANING_GROUPS, schedule_word, weighted_sample
from hsk_bot.i18n import TEXT
from hsk_bot.storage import empty_state

OWNER = 123456
NOW = datetime(2026, 11, 16, 9, tzinfo=timezone.utc)  # 12:00 Moscow


@pytest.fixture
def bot():
    engine = Engine(load_words(), OWNER, rng=random.Random(11))
    state = empty_state()
    count = 0

    def send(text=None, callback=None, now=NOW, owner=OWNER, private=True, update_id=None):
        nonlocal count
        count += 1
        message = {"message_id": count, "chat": {"id": owner, "type": "private" if private else "group"},
                   "from": {"id": owner}, "text": text or ""}
        update = {"update_id": update_id if update_id is not None else count}
        if callback:
            update["callback_query"] = {"id": str(count), "from": {"id": owner},
                                        "message": message, "data": callback}
        else:
            update["message"] = message
        engine.handle(state, update, now)
        return state

    return engine, state, send


def onboard(bot, now=NOW, language="ru", zone="Europe/Moscow"):
    engine, state, send = bot
    send("/start", now=now)
    send(callback="lang:" + language, now=now)
    send(callback="tz:" + zone, now=now)
    return state["user"]


def answer_session(bot, now=NOW, wrong_first=False):
    _, state, send = bot
    count = 0
    while state["user"]["session"]:
        session = state["user"]["session"]
        q = session["q"]
        if q["selected"] is None:
            pick = (q["correct"] + 1) % 3 if wrong_first and count == 0 else q["correct"]
            send(callback=f"a:{q['nonce']}:{pick}", now=now)
            count += 1
        send(callback="n:" + q["nonce"], now=now)
        assert count < 50


def complete_first_day(bot, wrong_first=False):
    user = onboard(bot)
    bot[2](callback="begin:2026-11-16")
    answer_session(bot, wrong_first=wrong_first)
    return user


def test_full_curriculum_matches_official_snapshot():
    words = load_words()
    source = json.loads((DATA_DIR / "hsk1-source.json").read_text())
    expected = {w["id"]: w for w in source}
    assert set(expected) == set(range(1, 301))
    for word in words:
        assert {k: word[k] for k in ("id", "hanzi", "pinyin")} == expected[word["id"]]
        assert word["hanzi"] in word["example"]
        assert all(word[k].strip() for k in ("ru", "en", "example_ru", "example_en", "example_pinyin"))
    assert words[0]["hanzi"] == "你好"


def test_bilingual_keys_match():
    assert TEXT["ru"].keys() == TEXT["en"].keys()


@pytest.mark.parametrize("owner,private", [(42, True), (OWNER, False)])
def test_only_owner_private_chat(bot, owner, private):
    before = copy.deepcopy(bot[1])
    bot[2]("/start", owner=owner, private=private)
    assert bot[1] == before


def test_onboarding_language_timezone(bot):
    user = onboard(bot, language="en", zone="Asia/Shanghai")
    assert user["lang"] == "en"
    assert user["timezone"] == "Asia/Shanghai"
    assert user["onboarded"]
    assert len(user["words"]) == 3
    assert "Today's" in bot[1]["outbox"][-2]["payload"]["text"]


def test_invalid_timezone_does_not_break_setup(bot):
    onboard(bot)
    bot[2]("/timezone Invalid/City")
    assert bot[1]["user"]["timezone"] == "Europe/Moscow"


def test_3_new_words_once_per_day_then_practice(bot):
    user = complete_first_day(bot)
    assert len(user["words"]) == 3
    for _ in range(3):
        bot[2]("/lesson")
        assert user["session"]["kind"] == "practice"
        answer_session(bot)
    assert len(user["words"]) == 3
    assert user["days"]["2026-11-16"]["total"] == 3


def test_error_on_either_field_resets_whole_word(bot):
    user = complete_first_day(bot, wrong_first=True)
    first = user["words"][str(user["days"]["2026-11-16"]["new"][0])]
    assert first["due"] == "2026-11-17"
    assert first["stage"] == 0
    assert first["errors"] == 1
    assert first["attempts"] == 1


def test_error_highlights_correct_choice_without_retry(bot):
    user = onboard(bot)
    bot[2](callback="begin:2026-11-16")
    q = user["session"]["q"]
    wrong = (q["correct"] + 1) % 3
    bot[2](callback=f"a:{q['nonce']}:{wrong}")
    feedback = bot[1]["outbox"][-1]
    rows = feedback["payload"]["reply_markup"]["inline_keyboard"]
    assert rows[q["correct"]][0]["text"].startswith("✅")
    assert rows[wrong][0]["text"].startswith("❌")
    assert all(r[0]["callback_data"] == "noop" for r in rows[:3])
    bot[2](callback=f"a:{q['nonce']}:{q['correct']}")
    assert q["selected"] == wrong


def test_duplicate_update_and_double_click_do_not_double_score(bot):
    user = onboard(bot)
    bot[2](callback="begin:2026-11-16")
    q = user["session"]["q"]
    bot[2](callback=f"a:{q['nonce']}:{q['correct']}", update_id=500)
    previous = copy.deepcopy(bot[1])
    bot[2](callback=f"a:{q['nonce']}:{q['correct']}", update_id=500)
    assert bot[1] == previous
    bot[2](callback="n:" + q["nonce"])
    q = user["session"]["q"]
    wid = user["session"]["ids"][0]
    bot[2](callback=f"a:{q['nonce']}:{q['correct']}")
    bot[2](callback=f"a:{q['nonce']}:{q['correct']}")
    assert user["words"][str(wid)]["attempts"] == 1


def test_archiving_and_restoring(bot):
    user = complete_first_day(bot)
    wid = next(iter(user["words"]))
    bot[2](callback="archive:" + wid)
    assert user["words"][wid]["archived"]
    bot[2]("/archive")
    assert "restore:" + wid in str(bot[1]["outbox"][-1])
    bot[2](callback="restore:" + wid)
    assert not user["words"][wid]["archived"]
    assert user["words"][wid]["due"] == "2026-11-17"


def test_max_two_due_reviews_and_no_early_reviews(bot):
    user = complete_first_day(bot)
    future = NOW + timedelta(days=1)
    bot[2]("/lesson", now=future)
    assert not user["days"]["2026-11-17"]["review"]
    day4 = NOW + timedelta(days=3)
    bot[2]("/lesson", now=day4)
    due = user["days"]["2026-11-19"]["review"]
    assert len(due) == 2
    assert len(set(due)) == 2
    assert len(user["words"]) == 9  # skipped Nov 18 never gets a batch


def test_archived_word_excluded_from_review(bot):
    user = complete_first_day(bot)
    archived = next(iter(user["words"]))
    bot[2](callback="archive:" + archived)
    future = NOW + timedelta(days=3)
    bot[2]("/lesson", now=future)
    assert int(archived) not in user["days"]["2026-11-19"]["review"]


def test_leitner_3_7_7_and_reset():
    p = {"stage": 0, "due": "2026-11-16", "attempts": 0, "correct": 0, "errors": 0}
    today = date(2026, 11, 16)
    for interval in (3, 7, 7):
        schedule_word(p, True, today)
        assert date.fromisoformat(p["due"]) == today + timedelta(days=interval)
        today = date.fromisoformat(p["due"])
    schedule_word(p, False, today)
    assert p["stage"] == 0
    assert p["due"] == (today + timedelta(days=1)).isoformat()


def test_early_practice_success_does_not_advance_schedule(bot):
    user = complete_first_day(bot)
    before = {wid: p["due"] for wid, p in user["words"].items()}
    bot[2]("/lesson")
    answer_session(bot)
    assert before == {wid: p["due"] for wid, p in user["words"].items()}


def test_weighted_selection_prefers_error_prone_words():
    progress = {"1": {"errors": 0}, "2": {"errors": 10}}
    rng = random.Random(1)
    picks = [weighted_sample([1, 2], progress, 1, rng)[0] for _ in range(1000)]
    assert picks.count(2) > 900
    assert weighted_sample([1, 2], progress, 3, rng) in ([1, 2], [2, 1])


@pytest.mark.parametrize("zone,instant,expected", [
    ("Europe/Moscow", NOW, True),
    ("Asia/Shanghai", NOW, False),
    ("Asia/Kathmandu", datetime(2026, 11, 16, 6, 15, tzinfo=timezone.utc), True),
    ("Europe/Berlin", datetime(2026, 7, 1, 10, tzinfo=timezone.utc), True),
    ("Europe/Berlin", datetime(2026, 11, 16, 11, tzinfo=timezone.utc), True),
    ("Europe/Moscow", NOW + timedelta(minutes=15), False),
])
def test_noon_scheduler_local_time_and_dst(bot, zone, instant, expected):
    user = onboard(bot, zone=zone)
    user["days"] = {}
    user["words"] = {}
    assert bot[0].tick(bot[1], instant) == expected
    if expected:
        assert not bot[0].tick(bot[1], instant + timedelta(minutes=1))


def test_pause_and_missed_days(bot):
    user = onboard(bot)
    bot[2]("/pause")
    assert not bot[0].tick(bot[1], NOW + timedelta(days=1))
    bot[2]("/resume")
    assert bot[0].tick(bot[1], NOW + timedelta(days=4))
    assert len(user["words"]) == 6
    assert "2026-11-17" not in user["days"]


def test_old_daily_buttons_cannot_start_old_lesson(bot):
    user = onboard(bot)
    bot[2](callback="begin:2026-11-16", now=NOW + timedelta(days=1))
    assert user["session"] is None
    assert "неактуальна" in bot[1]["outbox"][-1]["payload"]["text"]


def test_delete_requires_confirmation_and_clears_progress(bot):
    user = complete_first_day(bot)
    bot[2]("/delete_me")
    bot[2](callback="delete:wrong")
    assert bot[1]["user"] is user
    nonce = user["delete_nonce"]
    bot[2](callback="delete:" + nonce)
    assert bot[1]["user"] is None
    assert len(bot[1]["outbox"]) == 1
    assert not bot[0].tick(bot[1], NOW + timedelta(days=1))


def test_changing_language_invalidates_unanswered_question(bot):
    user = onboard(bot)
    bot[2](callback="begin:2026-11-16")
    q = copy.deepcopy(user["session"]["q"])
    bot[2](callback="lang:en")
    bot[2](callback=f"a:{q['nonce']}:{q['correct']}")
    assert user["session"]["q"] is None
    bot[2]("/lesson")
    assert user["session"]["q"]["nonce"] != q["nonce"]
    assert "Choose the translation" in bot[1]["outbox"][-1]["payload"]["text"]


def test_every_word_has_three_unique_options_without_equivalent_distractors(bot):
    engine, _, _ = bot
    user = onboard(bot)
    for lang in ("ru", "en"):
        user["lang"] = lang
        for word in engine.words:
            group = next((g for g in MEANING_GROUPS if word["id"] in g), set())
            user["session"] = {"ids": [word["id"]] + list(group - {word["id"]}), "pos": 0, "field": "translation"}
            q = engine.make_question(user, user["session"])
            assert len(set(q["options"])) == 3
            assert q["options"][q["correct"]] == word[lang]
            forbidden = {engine.by_id[i][lang] for i in group - {word["id"]}} - {word[lang]}
            assert not forbidden.intersection(q["options"])


def test_end_of_vocabulary_continues_review(bot):
    user = onboard(bot)
    p = next(iter(user["words"].values()))
    user["words"] = {str(w["id"]): {**p, "tested": True, "due": "2026-11-17"} for w in load_words()}
    user["days"] = {}
    bot[2]("/lesson", now=NOW + timedelta(days=1))
    assert user["days"]["2026-11-17"]["new"] == []
    assert user["session"]["kind"] == "daily_review"


def test_archive_during_practice_skips_current_word(bot):
    user = complete_first_day(bot)
    bot[2]("/lesson")
    wid = user["session"]["ids"][0]
    q = copy.deepcopy(user["session"]["q"])
    bot[2](callback="archive:" + str(wid))
    assert user["words"][str(wid)]["archived"]
    assert user["session"]["ids"][user["session"]["pos"]] != wid
    assert user["session"]["field"] == "translation"
    before = user["words"][str(wid)]["attempts"]
    bot[2](callback=f"a:{q['nonce']}:{q['correct']}")
    assert user["words"][str(wid)]["attempts"] == before


def test_full_course_100_days_and_continuing_review(bot):
    user = onboard(bot)
    for index in range(100):
        now = NOW + timedelta(days=index)
        if index:
            bot[2]("/lesson", now=now)
        bot[2](callback="begin:" + now.date().isoformat(), now=now)
        answer_session(bot, now=now)
        day = user["days"][now.date().isoformat()]
        assert len(day["new"]) == 3
        assert len(day["review"]) <= 2
        bot[1]["outbox"].clear()
    assert len(user["words"]) == 300
    now = NOW + timedelta(days=100)
    bot[2]("/lesson", now=now)
    answer_session(bot, now=now)
    assert user["days"][now.date().isoformat()]["new"] == []
    assert len(user["words"]) == 300
