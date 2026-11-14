"""Проверка: пускает ли Авито headless-браузер с IP дата-центра (GitHub Actions).

Ничего не публикует. Печатает в лог: статус, число карточек, маркер блокировки.
"""
import asyncio
import sys
from urllib.parse import quote

from playwright.async_api import async_playwright

QUERY = "шорты мужские"
URL = f"https://www.avito.ru/syktyvkar?q={quote(QUERY)}&pmin=1000&pmax=20000"
BLOCK_MARKERS = ["Доступ ограничен", "проблема с IP", "Подтвердите, что вы не робот", "заблокирован"]


async def main() -> int:
    async with async_playwright() as pw:
        browser = await pw.firefox.launch(headless=True)
        ctx = await browser.new_context(
            locale="ru-RU",
            timezone_id="Europe/Moscow",
            viewport={"width": 1366, "height": 768},
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) "
                        "Gecko/20100101 Firefox/133.0"),
        )
        page = await ctx.new_page()
        resp = await page.goto(URL, wait_until="domcontentloaded", timeout=60000)
        print("STATUS:", resp.status if resp else "no-response", flush=True)
        await asyncio.sleep(10)
        cards = await page.query_selector_all('[data-marker="item"]')
        html = (await page.content()).lower()
        blocked = [m for m in BLOCK_MARKERS if m.lower() in html]
        print("CARDS:", len(cards), flush=True)
        print("TITLE:", (await page.title())[:120], flush=True)
        print("BLOCK_MARKERS:", blocked or "none", flush=True)
        print("VERDICT:", "OK" if len(cards) >= 3 and not blocked else "BLOCKED", flush=True)
        await browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
