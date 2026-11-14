"""Сливает две базы по ссылке объявления: python merge_remote.py <другой.json>

Используется в облачном прогоне: если кто-то успел опубликовать свежую базу раньше нас,
мы берём её за основу и добавляем туда то, что собрали сами, — вместо того чтобы затирать.
"""
import io
import json
import os
import sys
from datetime import datetime, timedelta, timezone

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data.json")
MOSCOW = timezone(timedelta(hours=3))


def ts(value):
    try:
        dt = datetime.fromisoformat(str(value))
    except Exception:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=MOSCOW)


def load(path):
    try:
        return json.load(io.open(path, encoding="utf-8-sig"))
    except Exception:
        return {}


def main():
    if len(sys.argv) < 2:
        print("нужен путь к другой базе")
        return 1

    base = load(DATA)          # то, что уже лежит в репозитории (свежее)
    other = load(sys.argv[1])   # то, что собрали мы
    if not other.get("items"):
        print("нечего сливать")
        return 0

    index = {}
    for it in base.get("items", []):
        if it.get("url"):
            index[it["url"]] = it
    added = 0
    for it in other["items"]:
        url = it.get("url")
        if not url:
            continue
        current = index.get(url)
        if not current:
            index[url] = it
            added += 1
            continue
        for key, value in it.items():
            if value and not current.get(key):
                current[key] = value

    a, b = ts(base.get("updated")), ts(other.get("updated"))
    updated = max([t for t in (a, b) if t], default=None)
    merged = {
        "updated": updated.isoformat(timespec="seconds") if updated else "",
        "cursor": max(int(base.get("cursor") or 0), int(other.get("cursor") or 0)),
        "city": base.get("city") or other.get("city"),
        "source": "avito.ru",
        "items": list(index.values()),
    }
    io.open(DATA, "w", encoding="utf-8").write(json.dumps(merged, ensure_ascii=False))
    print(f"слияние: добавлено {added}, всего {len(merged['items'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
