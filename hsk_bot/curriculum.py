import json
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def load_words():
    words = json.loads((DATA_DIR / "hsk1.json").read_text(encoding="utf-8"))
    ids = [w["id"] for w in words]
    if len(words) != 300 or len(set(ids)) != 300:
        raise ValueError("Expected the 300-entry HSK 1 exam syllabus (2025/2026)")
    for word in words:
        for key in ("hanzi", "pinyin", "ru", "en", "example", "example_pinyin", "example_ru", "example_en"):
            if not word.get(key):
                raise ValueError(f"Missing {key} for word {word['id']}")
    return words
