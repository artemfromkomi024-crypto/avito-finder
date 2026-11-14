"""Разовая чистка базы: убирает ?context=... из ссылок, ставит постоянные id,
склеивает дубли (одна вещь могла попасть дважды из разных запросов или источников).

Запуск: python normalize_urls.py
"""
import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from avito_parser import DATA_OUT, item_id, normalize_url  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def score(it):
    """Чем больше, тем полезнее запись: с фото, с оценкой ИИ, с брендом."""
    return (2 if it.get("ai") else 0) + (1 if it.get("img") else 0) + \
           (1 if it.get("brand") and it.get("brand") != "NoName" else 0)


def main():
    data = json.load(io.open(DATA_OUT, encoding="utf-8-sig"))
    items = data.get("items", [])
    before = len(items)

    merged = {}
    changed_urls = 0
    for it in items:
        raw = it.get("url")
        clean = normalize_url(raw)
        if raw != clean:
            changed_urls += 1
        it["url"] = clean
        it["id"] = item_id(clean)
        cur = merged.get(clean)
        if cur is None or score(it) > score(cur):
            if cur is not None:
                # переносим полезное из отброшенной записи
                for k, v in cur.items():
                    if v and not it.get(k):
                        it[k] = v
            merged[clean] = it
        else:
            for k, v in it.items():
                if v and not cur.get(k):
                    cur[k] = v

    data["items"] = list(merged.values())
    io.open(DATA_OUT, "w", encoding="utf-8").write(json.dumps(data, ensure_ascii=False))
    print(f"было {before}, стало {len(data['items'])}, склеено {before - len(data['items'])}, "
          f"ссылок почищено {changed_urls}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
