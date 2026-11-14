"""Проверка: сколько запросов подряд выдерживает IP дата-центра (GitHub Actions).

Открывает 3 запроса к Авито с паузами, печатает по каждому: статус, число карточек,
маркер блокировки. Ничего не публикует.
"""
import asyncio
import random
import sys
from urllib.parse import quote

from playwright.async_api import async_playwright

QUERIES = ["шорты мужские", "футболка мужская", "худи мужское"]
URL = "https://www.avito.ru/syktyvkar?q={q}&pmin=1000&pmax=20000"
BLOCK_MARKERS = ["Доступ ограничен", "проблема с IP", "Подтвердите, что вы не робот"]


async def load(page, query):
    url = URL.format(q=quote(query))
    try:
        resp = await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        status = resp.status if resp else "?"
    except Exception as e:
        print(f"  goto error: {str(e)[:120]}", flush=True)
        status = "err"

    for _ in range(20):
        cards = await page.query_selector_all('[data-marker="item"]')
        if len(cards) >= 3:
            break
        html = (await page.content()).lower()
        if any(m.lower() in html for m in BLOCK_MARKERS):
            break
        await asyncio.sleep(1.5)

    cards = await page.query_selector_all('[data-marker="item"]')
    html = (await page.content()).lower()
    blocked = [m for m in BLOCK_MARKERS if m.lower() in html]
    print(f"  status={status} cards={len(cards)} blocked={blocked or 'none'}", flush=True)
    return len(cards), bool(blocked)


async def main() -> int:
    async with async_playwright() as pw:
        browser = await pw.firefox.launch(headless=True)
        ctx = await browser.new_context(
            locale="ru-RU", timezone_id="Europe/Moscow",
            viewport={"width": 1366, "height": 900},
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) "
                        "Gecko/20100101 Firefox/133.0"),
        )
        page = await ctx.new_page()
        results = []
        for i, q in enumerate(QUERIES, 1):
            print(f"[{i}/{len(QUERIES)}] {q}", flush=True)
            cards, blocked = await load(page, q)
            results.append((q, cards, blocked))
            if blocked:
                print("  -> блокировка, останавливаемся", flush=True)
                break
            if i < len(QUERIES):
                pause = random.randint(15, 25)
                print(f"  пауза {pause} c", flush=True)
                await asyncio.sleep(pause)
        await browser.close()

    ok = sum(1 for _, c, b in results if c >= 3 and not b)
    print(f"ИТОГ: успешных запросов {ok} из {len(results)}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
