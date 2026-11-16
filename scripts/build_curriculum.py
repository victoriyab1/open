"""Rebuild shipped JSON from the official syllabus and original bilingual content.

Build-only dependencies: pypdf and pypinyin. Not run on Render.
Usage: python scripts/build_curriculum.py --source data/syllabus-source.pdf
"""
import argparse
import hashlib
import json
import re
from pathlib import Path

from pypdf import PdfReader
from pypinyin import Style, lazy_pinyin

ROOT = Path(__file__).resolve().parents[1]
SOURCE_URL = "https://hsk.cn-bj.ufileos.com/3.0/%E6%96%B0%E7%89%88HSK%E8%80%83%E8%AF%95%E5%A4%A7%E7%BA%B21219.pdf"


def sentence_pinyin(sentence, source):
    # Prefer the official word readings, including neutral tones and erhua.
    # Keep dictionary tone spelling; recordings naturally include speech sandhi.
    lexicon = {w["hanzi"]: w["pinyin"].split("/")[0] for w in source}
    result = []
    pos = 0
    while pos < len(sentence):
        match = next((sentence[pos:pos + length] for length in range(6, 0, -1)
                      if sentence[pos:pos + length] in lexicon), None)
        if match:
            result.append(lexicon[match])
            pos += len(match)
        else:
            char = sentence[pos]
            if char in "。，？！、：；":
                result.append(char)
            else:
                result.extend(lazy_pinyin(char, style=Style.TONE))
            pos += 1
    return re.sub(r"\s+([。，？！、：；])", r"\1", " ".join(result))


def extract_source(path):
    found = {}
    pages = [path.read_text()] if path.suffix == ".txt" else (p.extract_text() for p in PdfReader(path).pages)
    for page in pages:
        for line in page.splitlines():
            match = re.match(r"^(\d+)\s+1(?:（\d+）)*\s+(\S+)\s+(.+)$", line)
            if not match or not 1 <= int(match[1]) <= 300:
                continue
            wid = int(match[1])
            hanzi = re.sub(r"\d+$", "", match[2])
            pinyin = re.split(r"[\u4e00-\u9fff]", match[3])[0].strip()
            found[wid] = {"id": wid, "hanzi": hanzi, "pinyin": pinyin}
            if wid == 300 and len(found) == 300:
                return [found[i] for i in range(1, 301)]
    if set(found) != set(range(1, 301)):
        raise ValueError("Official HSK 1 extraction incomplete")
    return [found[i] for i in range(1, 301)]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--extracted-text", type=Path)
    args = parser.parse_args()
    source = extract_source(args.extracted_text or args.source)
    content = {}
    for line in (ROOT / "data/content.tsv").read_text().splitlines():
        if not line or line.startswith("#"):
            continue
        wid, ru, en, example, example_ru, example_en = line.split("|")
        if int(wid) in content:
            raise ValueError(f"Duplicate content ID {wid}")
        content[int(wid)] = dict(ru=ru, en=en, example=example, example_ru=example_ru, example_en=example_en)
    if set(content) != set(range(1, 301)):
        raise ValueError("Need exactly 300 original content rows")
    words = []
    for item in source:
        word = {**item, **content[item["id"]]}
        word["example_pinyin"] = sentence_pinyin(word["example"], source)
        if word["hanzi"] not in word["example"]:
            raise ValueError(f"Example must use {word['hanzi']}")
        words.append(word)
    # Start with useful greetings/pronouns, then continue through the full official list.
    first = [147, 146, 221, 188, 248, 107, 241, 14, 271, 200, 204, 202,
             222, 148, 150, 182, 181, 128, 91, 156, 171, 254, 49, 173,
             198, 223, 113, 160, 2, 95, 184, 112, 111, 60, 263, 122,
             13, 115, 144, 71, 79, 253, 21, 77, 192, 17, 125, 159]
    by_id = {w["id"]: w for w in words}
    ordered = [by_id[i] for i in first] + [w for w in words if w["id"] not in first]
    (ROOT / "data/hsk1-source.json").write_text(json.dumps(source, ensure_ascii=False, indent=2) + "\n")
    (ROOT / "data/hsk1.json").write_text(json.dumps(ordered, ensure_ascii=False, indent=2) + "\n")
    metadata = {"source_url": SOURCE_URL, "source_sha256": hashlib.sha256(args.source.read_bytes()).hexdigest(),
                "syllabus_publication": "2025-11", "syllabus_effective": "2026-07", "level": 1,
                "word_count": 300, "content": "Original Russian/English translations and beginner examples",
                "example_pinyin": "Official word readings, dictionary tone spelling; pypinyin 0.55.0 for extra characters"}
    (ROOT / "data/provenance.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n")
    print(f"Built {len(words)} complete HSK 1 entries")


if __name__ == "__main__":
    main()
