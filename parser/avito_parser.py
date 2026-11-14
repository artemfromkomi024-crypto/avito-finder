#!/usr/bin/env python3
"""
Парсер Авито для «Авито-Финдер» (Firefox + SOCKS-прокси из config.json).

Логика:
  • открывает поиск по списку запросов (город Сыктывкар, цена 1000–20000);
  • дожидается прохождения JS-челленджа (Авито отдаёт 439 и редирект);
  • вытаскивает название, цену, ссылку, фото, бренд, размер, состояние, «паль»;
  • пишет ../data.json — его читает index.html.

Запуск: python avito_parser.py
"""

import json
import os
import random
import re
import sys
import time
from datetime import datetime
from urllib.parse import quote

from playwright.sync_api import sync_playwright, TimeoutError as PwTimeout

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_OUT = os.path.join(os.path.dirname(BASE_DIR), "data.json")
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")

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
    "шорты": ["шорт", "бордшорт"],
    "футболка": ["футболк", "tee", "t-shirt", "поло"],
    "худи": ["худи", "hoodie"],
    "свитшот": ["свитшот", "crewneck", "лонгслив"],
    "джинсы": ["джинс", "jeans", "брюк", "штаны"],
    "куртка": ["куртк", "пуховик", "бомбер", "jacket", "ветровк", "парк"],
    "кроссовки": ["кроссовк", "кеды", "sneaker", "ботинк", "обувь"],
    "кепка": ["кепк", "cap", "бейсболк", "шапк"],
}


def load_config():
    with open(CONFIG_PATH, encoding="utf-8-sig") as f:
        return json.load(f)


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
            url = href if href and href.startswith("http") else ("https://www.avito.ru" + (href or ""))

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
                "id": "avito-" + str(abs(hash(url)) % 10**10),
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
                "date": (date_el.inner_text().strip() if date_el else datetime.now().strftime("%Y-%m-%d")),
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


def main():
    cfg = load_config()
    if "--no-proxy" in sys.argv:
        cfg["proxy"] = ""
        print("режим: без прокси (прямое подключение)")
    ok, msg = check_proxy(cfg)
    print(msg)
    if not ok:
        return 3
    limit = cfg.get("max_items_per_query", 40)
    print(f"Старт: {datetime.now():%Y-%m-%d %H:%M} · запросов: {len(cfg['queries'])}")

    items, blocked = [], False
    max_req = cfg.get("max_requests_per_run", 3)
    done = 0

    with sync_playwright() as pw:
        browser = pw.firefox.launch(
            headless=cfg.get("headless", True),
            proxy={"server": cfg["proxy"]} if cfg.get("proxy") else None,
            firefox_user_prefs={
                "network.proxy.socks_remote_dns": True,
                # не грузим картинки: парсеру нужны только ссылки на них, это в разы легче
                "permissions.default.image": 2 if cfg.get("skip_images", True) else 1,
            },
        )
        ctx = browser.new_context(locale="ru-RU", timezone_id="Europe/Moscow",
                                  viewport={"width": 1366, "height": 900})
        page = ctx.new_page()

        for city in cfg["cities"]:
            if blocked or done >= max_req:
                break
            for i, query in enumerate(cfg["queries"], 1):
                if blocked or done >= max_req:
                    break
                print(f"[{city['name']}] [{i}/{len(cfg['queries'])}] {query}")
                try:
                    page.goto(build_url(cfg, city["slug"], query), wait_until="domcontentloaded", timeout=60000)
                except PwTimeout:
                    print("  таймаут")
                    continue

                state = wait_for_content(page, cfg.get("challenge_timeout", 40))
                if state == "blocked":
                    print("  !!! блок по IP - останавливаемся")
                    blocked = True
                    break
                if state == "timeout":
                    print("  карточки не появились")
                    continue

                found = parse_page(page, limit)
                for it in found:
                    it["city"] = city["name"]
                print(f"  собрано: {len(found)}")
                items.extend(found)
                done += 1

                if done < max_req:
                    pause = random.uniform(cfg.get("delay_min", 15), cfg.get("delay_max", 35))
                    print(f"  пауза {pause:.0f} c")
                    time.sleep(pause)

        browser.close()

    uniq = {}
    # сначала прошлые данные, чтобы не терять объявления при частичном прогоне
    if os.path.exists(DATA_OUT):
        try:
            with open(DATA_OUT, encoding="utf-8") as f:
                old = json.load(f)
            for it in old.get("items", []):
                if it.get("url"):
                    uniq[it["url"]] = it
        except Exception:
            pass

    for it in items:
        uniq[it["url"]] = it
    items = list(uniq.values())

    if not items:
        print("Ничего не собрано - data.json не изменён")
        return 1 if not blocked else 2

    data = {
        "city": cfg["city"],
        "updated": datetime.now().isoformat(timespec="seconds"),
        "source": "avito.ru",
        "items": items,
    }
    with open(DATA_OUT, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)

    print(f"Готово: {len(items)} объявлений, файл: {DATA_OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
