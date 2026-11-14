#!/usr/bin/env python3
"""
Парсер Авито для «Авито-Финдер» (Firefox + SOCKS-прокси из config.json).

Что делает за один запуск:
  • берёт из очереди следующие пары «город + запрос» (курсор хранится в data.json),
    поэтому за несколько запусков обходятся все города и все категории;
  • делает по одному запросу на каждом доступном маршруте: напрямую и через прокси.
    Это два разных IP, значит Авито режет их независимо, и добыча удваивается;
  • сливает результат со старыми данными по ссылке, ничего не теряя;
  • пишет ../data.json со временем по Москве.

Запуск: python avito_parser.py            (напрямую + прокси)
        python avito_parser.py --no-proxy (только напрямую)
"""

import json
import hashlib
import os
import random
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from playwright.sync_api import sync_playwright, TimeoutError as PwTimeout

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_OUT = os.path.join(os.path.dirname(BASE_DIR), "data.json")
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")

# время всегда по Москве, независимо от часового пояса машины (ПК или сервер GitHub)
MOSCOW = timezone(timedelta(hours=3))

BLOCK_MARKERS = ["Доступ ограничен", "проблема с IP", "Подтвердите, что вы не робот"]

BRANDS = [
    "stone island", "supreme", "nike", "adidas", "the north face", "carhartt",
    "levi's", "levis", "balenciaga", "off-white", "off white", "new balance",
    "uniqlo", "zara", "h&m", "puma", "reebok", "lacoste", "tommy hilfiger",
    "columbia", "patagonia", "stussy", "kith", "bape", "gucci", "dior",
    "moncler", "cp company", "c.p. company", "vans", "converse", "asics",
    "jordan", "yeezy", "under armour", "alpha industries", "dickies",
    # добавлено по реальным объявлениям из Сыктывкара
    "ralph lauren", "calvin klein", "abercrombie", "jack wolfskin",
    "carhartt wip", "ed hardy", "onitsuka tiger", "giovanni vittori",
    "blue motion", "demix", "weekday", "monki", "mustang", "fischer",
    "athlex", "nanameel", "kappa", "george", "sohogirls", "phoenix",
    "hugo boss", "armani", "versace", "burberry", "acne studios",
    "barbour", "canada goose", "arcteryx", "salomon", "hoka", "timberland",
    # вторая волна (по аудиту «паль»-объявлений)
    "bershka", "champion", "vlone", "gap", "amiri", "diesel",
    "jack daniels", "balmain", "moschino", "palm angels", "rick owens",
    "comme des garcons", "issey miyake", "y-3", "adidas originals",
]

# модели, по которым можно понять бренд, если он не написан
MODEL_ALIASES = {
    "cortez": "Nike", "air force": "Nike", "air max": "Nike", "dunk": "Nike",
    "samba": "Adidas", "gazelle": "Adidas", "superstar": "Adidas",
    "nuptse": "The North Face", "501": "Levi's",
}

FAKE_MARKERS = ["паль", "реплика", "копия", "1:1", "аналог", "репл", "люкс качество"]

SIZE_RE = re.compile(r"\b(XXXL|XXL|XL|XS|S|M|L|\d{2})\b", re.IGNORECASE)

CATEGORY_KEYS = {
    # порядок важен: «футболка» проверяется раньше «рубашки», иначе t-shirt уйдёт в рубашки
    "футболка": ["футболк", "t-shirt", "t shirt", "тишот"],
    "поло": ["поло"],
    "рубашка": ["рубашк", "shirt"],
    "лонгслив": ["лонгслив", "longsleeve", "long sleeve"],
    "худи": ["худи", "hoodie"],
    "свитшот": ["свитшот", "crewneck"],
    "свитер": ["свитер", "джемпер", "sweater", "вязан"],
    "кардиган": ["кардиган"],
    "куртка": ["куртк", "бомбер", "jacket", "ветровк", "анорак", "парка"],
    "пуховик": ["пуховик", "пуховк", "down jacket"],
    "пальто": ["пальто", "плащ", "coat"],
    "жилет": ["жилет"],
    "джинсы": ["джинс", "jeans"],
    "брюки": ["брюк", "штаны", "чинос", "chino", "карго", "cargo"],
    "шорты": ["шорт", "бордшорт"],
    "кроссовки": ["кроссовк", "кеды", "sneaker"],
    "ботинки": ["ботинк", "туфли", "лофер", "loafer", "обувь"],
    "кепка": ["кепк", "бейсболк", "cap"],
    "шапка": ["шапк", "beanie", "шапочк"],
    "аксессуары": ["сумк", "рюкзак", "ремень", "ремн", "шарф", "перчатк", "носки", "очки"],
}


def now_msk():
    return datetime.now(MOSCOW)


URL_ID_RE = re.compile(r"_(\d{6,})(?:[?#]|$)")


def normalize_url(url):
    """Убирает ?context=... — иначе одна и та же вещь попадает в базу дважды."""
    if not url:
        return ""
    return str(url).split("?")[0].split("#")[0]


def item_id(url):
    """Постоянный id: номер объявления из ссылки (одинаковый на ПК и в облаке)."""
    clean = normalize_url(url)
    m = URL_ID_RE.search(clean)
    if m:
        return "avito-" + m.group(1)
    return "avito-" + hashlib.md5(clean.encode("utf-8")).hexdigest()[:10]


def parse_ts(value):
    """Строка времени -> datetime с часовым поясом (старые записи считаем московскими)."""
    try:
        dt = datetime.fromisoformat(str(value))
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=MOSCOW)
    return dt


def load_config():
    with open(CONFIG_PATH, encoding="utf-8-sig") as f:
        return json.load(f)


def load_old():
    if not os.path.exists(DATA_OUT):
        return {}
    try:
        with open(DATA_OUT, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def detect_brand(text):
    low = text.lower()
    for model, brand in MODEL_ALIASES.items():
        if model in low:
            return brand
    for b in BRANDS:
        if b in low:
            return "Levi's" if b in ("levi's", "levis") else b.title()
    return "NoName"


def detect_category(text):
    low = text.lower()
    for cat, keys in CATEGORY_KEYS.items():
        if any(k in low for k in keys):
            return cat
    return "другое"


def detect_size(text):
    low = text.lower()
    m = re.search(r"размер\s*[:\-]?\s*(\d{2}(?:\s*-\s*\d{2})?)", low)
    if m:
        return m.group(1).replace(" ", "").replace("-", "-")
    m = re.search(r"\b(\d{2})\s*-\s*(\d{2})\b", low)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    m = re.search(r"\b(XXXL|XXL|XL|XS|S|M|L)\b", text)
    if m:
        return m.group(1).upper()
    m = re.search(r"\b(4[0-9]|5[0-9])\b", low)
    return m.group(1) if m else ""


def detect_fake(text):
    low = text.lower()
    return any(k in low for k in FAKE_MARKERS)


def wait_for_content(page, timeout=40):
    """Ждём, пока пройдёт челлендж и появятся карточки."""
    deadline = time.time() + timeout
    block_since = None
    while time.time() < deadline:
        time.sleep(2)
        try:
            html = page.content()
        except Exception:
            continue
        if 'data-marker="item"' in html:
            return "ok"
        has_marker = any(m.lower() in html.lower() for m in BLOCK_MARKERS)
        if has_marker:
            if block_since is None:
                block_since = time.time()
            elif time.time() - block_since > 12:
                # маркер висит больше 12 секунд и карточек нет — это реальный блок
                return "blocked"
        else:
            block_since = None
    return "timeout"


def parse_page(page, limit):
    items = []
    for card in page.query_selector_all('[data-marker="item"]')[:limit]:
        try:
            title_el = card.query_selector('[itemprop="name"]') or card.query_selector('[data-marker="item-title"]')
            price_el = card.query_selector('[itemprop="price"]') or card.query_selector('[data-marker="item-price"]')
            link_el = card.query_selector('a[data-marker="item-title"]') or card.query_selector("a[href]")
            img_el = card.query_selector("img")
            date_el = card.query_selector('[data-marker="item-date"]')

            title = (title_el.inner_text() if title_el else "").strip()
            if not title or not price_el:
                continue

            raw_price = price_el.get_attribute("content") or price_el.inner_text()
            digits = re.sub(r"[^\d]", "", raw_price or "")
            if not digits:
                continue
            price = int(digits)

            href = link_el.get_attribute("href") if link_el else ""
            url = normalize_url(href if href and href.startswith("http")
                                else ("https://www.avito.ru" + (href or "")))

            img = ""
            # собираем все варианты картинки: src + srcset + <picture><source>
            candidates = []
            if img_el:
                src = (img_el.get_attribute("src") or img_el.get_attribute("data-src") or "").strip()
                if src and not src.startswith("data:") and "img.avito.st" in src:
                    candidates.append((0, src))
                for part in (img_el.get_attribute("srcset") or "").split(","):
                    chunk = part.strip().split(" ")
                    if chunk and chunk[0] and not chunk[0].startswith("data:") and "img.avito.st" in chunk[0]:
                        w = 0
                        if len(chunk) > 1 and chunk[1].endswith("w"):
                            try:
                                w = int(chunk[1][:-1])
                            except ValueError:
                                w = 0
                        candidates.append((w, chunk[0]))
            src_el = card.query_selector("picture source")
            if src_el:
                for part in (src_el.get_attribute("srcset") or "").split(","):
                    chunk = part.strip().split(" ")
                    if chunk and chunk[0] and not chunk[0].startswith("data:") and "img.avito.st" in chunk[0]:
                        w = 0
                        if len(chunk) > 1 and chunk[1].endswith("w"):
                            try:
                                w = int(chunk[1][:-1])
                            except ValueError:
                                w = 0
                        candidates.append((w, chunk[0]))
            if candidates:
                img = max(candidates, key=lambda c: c[0])[1]

            card_text = f"{title} {card.inner_text()}"
            brand = detect_brand(title)

            items.append({
                "id": item_id(url),
                "title": title,
                "price": price,
                "brand": brand,
                "category": detect_category(title),
                "size": detect_size(title),
                "condition": "used" if "б/у" in card_text.lower() else "new",
                # правило заказчика: паль = всё, где не указан бренд
                "fake": (brand == "NoName") or detect_fake(title),
                "img": img,
                "url": url,
                "date": (date_el.inner_text().strip() if date_el else now_msk().strftime("%Y-%m-%d")),
            })
        except Exception:
            continue
    return items


def build_url(cfg, slug, query):
    # без s=1: иначе Авито сортирует по возрастанию цены и мы получаем только самые дешёвые
    return (f"{cfg.get('base', 'https://www.avito.ru')}/{slug}"
            f"?q={quote(query)}&pmin={cfg['price_min']}&pmax={cfg['price_max']}")


def check_proxy(cfg, timeout=6):
    """Проверяем, что прокси вообще отвечает на TCP. Возвращает (ok, текст)."""
    proxy = (cfg.get("proxy") or "").strip()
    if not proxy:
        return True, "прокси не используется"
    m = re.match(r"^(?:socks5h?|http|https)://(?:[^@/]+@)?([^:/]+):(\d+)", proxy)
    if not m:
        return True, "адрес прокси не распознан, пропускаем проверку"
    host, port = m.group(1), int(m.group(2))
    import socket
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True, f"прокси {host}:{port} доступен"
    except Exception as e:
        return False, f"ПРОКСИ НЕДОСТУПЕН ({host}:{port}): {e}"


def all_pairs(cfg):
    """Все пары «город + запрос» — очередь, которую проходим по кругу."""
    return [(city["name"], city["slug"], q) for city in cfg["cities"] for q in cfg["queries"]]


def collect_route(cfg, proxy, label, pairs, limit, budget):
    """Один маршрут (прямой или через прокси): до `budget` успешных страниц."""
    items, used, blocked = [], 0, False
    print(f"маршрут: {label}")
    with sync_playwright() as pw:
        browser = pw.firefox.launch(
            headless=cfg.get("headless", True),
            proxy={"server": proxy} if proxy else None,
            firefox_user_prefs={
                "network.proxy.socks_remote_dns": True,
                # не грузим картинки: парсеру нужны только ссылки на них, это в разы легче
                "permissions.default.image": 2 if cfg.get("skip_images", True) else 1,
            },
        )
        ctx = browser.new_context(locale="ru-RU", timezone_id="Europe/Moscow",
                                  viewport={"width": 1366, "height": 900})
        page = ctx.new_page()

        for name, slug, query in pairs:
            print(f"[{name}] {query}")
            try:
                page.goto(build_url(cfg, slug, query), wait_until="domcontentloaded", timeout=60000)
            except PwTimeout:
                print("  таймаут")
                continue
            except Exception as e:
                print("  ошибка:", str(e)[:100])
                continue

            state = wait_for_content(page, cfg.get("challenge_timeout", 40))
            if state == "blocked":
                print("  !!! блок по IP")
                blocked = True
                break
            if state == "timeout":
                print("  карточки не появились")
                continue

            found = parse_page(page, limit)
            for it in found:
                it["city"] = name
            items.extend(found)
            used += 1
            print(f"  собрано: {len(found)}")

            if used >= budget:
                break
            pause = random.uniform(cfg.get("delay_min", 15), cfg.get("delay_max", 35))
            print(f"  пауза {pause:.0f} c")
            time.sleep(pause)

        browser.close()
    return items, used, blocked


def main():
    cfg = load_config()
    if "--no-proxy" in sys.argv:
        cfg["proxy"] = ""

    # маршруты: сначала напрямую, потом через прокси (если он есть и отвечает)
    routes = [("напрямую", "")]
    if cfg.get("proxy"):
        ok, msg = check_proxy(cfg)
        print(msg)
        if ok:
            routes.append(("прокси", cfg["proxy"]))
        else:
            print("прокси недоступен - работаем только напрямую")

    old = load_old()
    old_items = old.get("items", [])
    pairs = all_pairs(cfg)
    total = len(pairs)
    cursor = int(old.get("cursor") or 0)
    budget = int(cfg.get("requests_per_route", 1))
    limit = cfg.get("max_items_per_query", 40)

    print(f"Старт: {now_msk():%Y-%m-%d %H:%M} MSK · в очереди {total} пар · курсор {cursor} · маршрутов {len(routes)}")

    collected = []
    idx = cursor
    for label, proxy in routes:
        chunk = [pairs[(idx + k) % total] for k in range(budget)]
        found, used, blocked = collect_route(cfg, proxy, label, chunk, limit, budget)
        collected.extend(found)
        idx += used
        if blocked:
            print(f"  маршрут «{label}» закрыт Авито, идём дальше")

    uniq = {}
    # сначала прошлые данные, чтобы не терять объявления при частичном прогоне
    for it in old_items:
        if it.get("url"):
            uniq[it["url"]] = it
    for it in collected:
        uniq[it["url"]] = it
    items = list(uniq.values())

    if not collected:
        print("Ничего не собрано - data.json не изменён")
        return 2

    updated = now_msk()
    old_ts = parse_ts(old.get("updated"))
    if old_ts and old_ts > updated:
        updated = old_ts  # не откатываем время назад, если старые данные свежее

    data = {
        "city": cfg["city"],
        "updated": updated.isoformat(timespec="seconds"),
        "cursor": idx % total,
        "source": "avito.ru",
        "items": items,
    }
    with open(DATA_OUT, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)

    print(f"Готово: +{len(collected)} новых, всего {len(items)}, "
          f"следующий курсор {data['cursor']}, файл: {DATA_OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
