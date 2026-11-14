"""Подтягивает базу из GitHub и сливает с локальной по id.

Нужно, потому что базу теперь пополняют два источника: этот ПК и GitHub Actions.
Без слияния публикация с ПК затёрла бы то, что добавил облачный прогон.

Запуск: python sync_cloud.py  (ошибки не критичны — просто работаем с локальной)
"""
import io
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data.json")
URL = "https://raw.githubusercontent.com/artemfromkomi024-crypto/avito-finder/main/data.json"

MOSCOW = timezone(timedelta(hours=3))
FIELDS = ("ai", "img", "city", "size", "condition", "brand", "category", "cursor")


def ts(value):
    """Строка времени -> datetime с поясом; старые записи без пояса считаем московскими."""
    try:
        dt = datetime.fromisoformat(str(value))
    except Exception:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=MOSCOW)


def load_remote():
    try:
        out = subprocess.run(
            ["curl", "-sS", "-L", "--max-time", "40", URL],
            capture_output=True, timeout=60,
        )
        if out.returncode != 0 or not out.stdout:
            return None
        return json.loads(out.stdout.decode("utf-8"))
    except Exception as e:
        print("облачная база недоступна:", str(e)[:120])
        return None


def main():
    try:
        local = json.load(io.open(DATA, encoding="utf-8-sig"))
    except Exception as e:
        print("локальная база не читается:", str(e)[:120])
        return 1

    remote = load_remote()
    if not remote or not remote.get("items"):
        print("работаем только с локальной базой")
        return 0

    index = {}
    for it in remote["items"]:
        index[it["id"]] = it

    added = 0
    enriched = 0
    for it in local["items"]:
        old = index.get(it["id"])
        if not old:
            index[it["id"]] = it
            added += 1
            continue
        # дополняем облачную запись тем, чего в ней нет, но есть локально
        changed = False
        for key in FIELDS:
            if key == "cursor":
                continue
            if it.get(key) and not old.get(key):
                old[key] = it[key]
                changed = True
        if changed:
            enriched += 1

    local_ts, remote_ts = ts(local.get("updated")), ts(remote.get("updated"))
    updated = max([t for t in (local_ts, remote_ts) if t], default=None)
    cursor = max(int(local.get("cursor") or 0), int(remote.get("cursor") or 0))

    merged = {
        "updated": updated.isoformat(timespec="seconds") if updated else "",
        "cursor": cursor,
        "city": remote.get("city") or local.get("city"),
        "source": "avito.ru",
        "items": list(index.values()),
    }
    io.open(DATA, "w", encoding="utf-8").write(json.dumps(merged, ensure_ascii=False))
    print(f"слияние с облаком: добавлено локальных {added}, дополнено {enriched}, "
          f"всего {len(merged['items'])}, курсор {cursor}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
