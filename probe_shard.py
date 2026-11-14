"""Один шард: один запрос к Авито со своего IP (каждый job = свой runner = свой IP).

Печатает IP раннера, статус, число карточек и вердикт.
Номер шарда берётся из переменной окружения SHARD.
"""
import asyncio
import os
import sys
import urllib.request
from urllib.parse import quote

from playwright.async_api import async_playwright

QUERIES = [
    "шорты мужские",
    "футболка мужская",
    "худи мужское",
    "куртка мужская",
    "джинсы мужские",
    "кроссовки мужские",
]
BLOCK_MARKERS = ["Доступ ограничен", "проблема с IP", "Подтвердите, что вы не робот"]


def my_ip() -> str:
    try:
        with urllib.request.urlopen("https://api.ipify.org", timeout=15) as r:
            return r.read().decode().strip()
    except Exception as e:
        return "ip? " + str(e)[:60]


async def main() -> int:
    shard = int(os.environ.get("SHARD", "0"))
    query = QUERIES[shard % len(QUERIES)]
    url = f"https://www.avito.ru/syktyvkar?q={quote(query)}&pmin=1000&pmax=20000"

    print(f"SHARD {shard} · IP {my_ip()}", flush=True)
    print(f"QUERY {query}", flush=True)

    async with async_playwright() as pw:
        browser = await pw.firefox.launch(headless=True)
        ctx = await browser.new_context(
            locale="ru-RU",
            timezone_id="Europe/Moscow",
            viewport={"width": 1366, "height": 900},
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) "
                        "Gecko/20100101 Firefox/133.0"),
        )
        page = await ctx.new_page()
        status = "?"
        try:
            resp = await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            status = resp.status if resp else "?"
        except Exception as e:
            print("goto error:", str(e)[:140], flush=True)

        cards = 0
        for _ in range(20):
            cards = len(await page.query_selector_all('[data-marker="item"]'))
            if cards >= 3:
                break
            html = (await page.content()).lower()
            if any(m.lower() in html for m in BLOCK_MARKERS):
                break
            await asyncio.sleep(1.5)

        html = (await page.content()).lower()
        blocked = [m for m in BLOCK_MARKERS if m.lower() in html]
        print(f"RESULT shard={shard} status={status} cards={cards} "
              f"blocked={blocked or 'none'}", flush=True)
        print(f"VERDICT={('OK' if cards >= 3 and not blocked else 'BLOCKED')}", flush=True)
        await browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
