import io, json, os, sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "parser"))
from avito_parser import detect_category  # noqa: E402

tpl = io.open(os.path.join(ROOT, "site", "app.html"), encoding="utf-8").read()
data_path = os.path.join(ROOT, "data.json")
data = json.load(io.open(data_path, encoding="utf-8-sig"))

changed = 0
for it in data["items"]:
    if not it.get("city"):
        it["city"] = data.get("city") or "Сыктывкар"
        changed += 1

# заново определяем категорию: список типов одежды расширился,
# поэтому старые записи тоже должны попасть в новые разделы
recat = 0
for it in data["items"]:
    cat = detect_category(it.get("title", ""))
    if cat != it.get("category"):
        it["category"] = cat
        recat += 1

if changed or recat:
    io.open(data_path, "w", encoding="utf-8").write(json.dumps(data, ensure_ascii=False))

payload = json.dumps(data, ensure_ascii=False)
out = tpl.replace("__DATA__", payload)

target = os.path.join(ROOT, "Avito.html")
io.open(target, "w", encoding="utf-8", newline="\n").write(out)

cities, cats = {}, {}
for it in data["items"]:
    cities[it["city"]] = cities.get(it["city"], 0) + 1
    cats[it.get("category", "—")] = cats.get(it.get("category", "—"), 0) + 1

print("файл:", target)
print("размер, КБ:", round(os.path.getsize(target) / 1024, 1))
print("объявлений:", len(data["items"]), "| город проставлен у", changed,
      "| категория уточнена у", recat)
print("города:", ", ".join(f"{k}={v}" for k, v in cities.items()))
print("категории:", ", ".join(f"{k}={v}" for k, v in sorted(cats.items(), key=lambda x: -x[1])))
