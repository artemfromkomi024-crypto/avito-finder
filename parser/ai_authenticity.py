"""Проверка подлинности вещей по фото через Gemini.

Читает data.json, для объявлений без поля "ai" отправляет фото в Gemini
и сохраняет вердикт: verdict / confidence / reasons.

Из России Gemini API напрямую недоступен ("User location is not supported"),
поэтому запросы идут через SOCKS5-прокси из parser/config.json (через curl).

Запуск:  python ai_authenticity.py [--limit N] [--test]
"""
import base64
import io
import json
import os
import subprocess
import sys
import tempfile
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data.json")
KEY_FILE = os.path.join(ROOT, "parser", "ai_key.txt")
CONFIG = os.path.join(ROOT, "parser", "config.json")

MODELS = ["gemini-3.1-flash-lite", "gemini-3.8-flash"]
API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"

PROMPT = """Ты — эксперт по проверке подлинности брендовой одежды на фотографиях с барахолки.

Смотри ТОЛЬКО на то, что реально видно на снимке. Не додумывай. Если ракурс не позволяет
оценить признак — так и напиши в reasons и снизь confidence.

Оцени по порядку:
1. Логотип/надпись: вышивка, вшитый тканевый ярлык, печать по ткани, наклеенная аппликация,
   термотрансфер. Для оригинала обычно вышивка или вшитый ярлык; наклейка поверх ткани — признак копии.
2. Бирки: вшивной ярлык с составом, размерник, страна производства — есть ли они вообще,
   аккуратность их пришива.
3. Швы и строчка: ровность, плотность, обработка краёв.
4. Шрифт и пропорции логотипа: типичные ошибки копий.
5. Фурнитура (молнии, кнопки, люверсы) и общий вид ткани.
6. Цена и название объявления, если они даны.

Верни СТРОГО JSON без пояснений:
{
  "verdict": "original" | "fake" | "unclear",
  "confidence": число от 0 до 1,
  "reasons": ["кратко по-русски, 2-4 пункта"],
  "logo": "как нанесён логотип, если виден, иначе null",
  "red_flags": ["что насторожило, если есть"]
}

Правила:
- Фото на Авито обычно около 200 пикселей — это нормально. Если видно, КАК нанесён логотип
  (вышивка, печать, наклейка, вшитый ярлык) — давай вердикт original или fake, не уходи в unclear.
- verdict "unclear" ставь только когда вещь снята издалека, логотипа бренда вообще не видно
  или снимок размыт.
- Не называй вещь подделкой только из-за низкой цены.
- Если бренд не указан (NoName) — вердикт "unclear": это не подделка бренда, а просто вещь без бренда.
- confidence: 0.8-0.95 — когда способ нанесения логотипа хорошо различим; 0.5-0.7 — когда видно, но не идеально;
  ниже 0.5 — когда почти ничего не понятно."""


def load_key():
    env_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if env_key:
        return env_key
    if not os.path.exists(KEY_FILE):
        sys.exit("нет ключа: ни GEMINI_API_KEY, ни parser/ai_key.txt")
    return io.open(KEY_FILE, encoding="utf-8").read().strip()


def proxy_url():
    try:
        cfg = json.load(io.open(CONFIG, encoding="utf-8-sig"))
        return cfg.get("proxy") or ""
    except Exception:
        return ""


PROXY = "" if ("--no-proxy" in sys.argv or os.environ.get("AI_NO_PROXY")) else proxy_url()


def curl(args, timeout=25):
    cmd = ["curl", "-sS", "--max-time", str(timeout)]
    if PROXY.startswith("socks5://"):
        cmd += ["--socks5-hostname", PROXY[len("socks5://"):]]
    cmd += args
    p = subprocess.run(cmd, capture_output=True)
    if p.returncode != 0:
        raise RuntimeError((p.stderr or b"").decode("utf-8", "replace").strip()[:200])
    return p.stdout


def http_json(url, payload):
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    try:
        io.open(path, "w", encoding="utf-8").write(json.dumps(payload))
        raw = curl(["-X", "POST", "-H", "Content-Type: application/json",
                    "--data-binary", "@" + path, "-w", "\n%{http_code}", url])
    finally:
        os.unlink(path)
    body, _, code = raw.decode("utf-8", "replace").rpartition("\n")
    if code.strip() != "200":
        raise RuntimeError(f"HTTP {code.strip()}: {body[:300]}")
    return json.loads(body)


def download(url, timeout=30):
    blob = curl(["-L", "-A", "Mozilla/5.0", url], timeout=timeout)
    mime = "image/webp"
    if blob[:2] == b"\xff\xd8":
        mime = "image/jpeg"
    elif blob[:8] == b"\x89PNG\r\n\x1a\n":
        mime = "image/png"
    return blob, mime


def ask(key, model, blob, mime, item):
    ctx = (f"Название: {item.get('title')}\n"
           f"Бренд: {item.get('brand')}\n"
           f"Цена: {item.get('price')} руб\n"
           f"Категория: {item.get('category')}")
    payload = {
        "contents": [{
            "parts": [
                {"text": PROMPT},
                {"text": ctx},
                {"inline_data": {"mime_type": mime, "data": base64.b64encode(blob).decode("ascii")}},
            ]
        }],
        "generationConfig": {"temperature": 0.1, "responseMimeType": "application/json",
                             "thinkingConfig": {"thinkingBudget": 0}},
    }
    url = API.format(model=model, key=key)
    for attempt in (1, 2, 3):
        try:
            res = http_json(url, payload)
            break
        except Exception as e:
            msg = str(e)
            retryable = any(k in msg for k in ("timed out", "503", "429", "UNAVAILABLE", "500"))
            if attempt == 3 or not retryable:
                raise
            time.sleep(2)
    txt = res["candidates"][0]["content"]["parts"][0]["text"].strip()
    if txt.startswith("```"):
        txt = txt.split("```")[1]
        if txt.startswith("json"):
            txt = txt[4:]
    return json.loads(txt.strip())


def main():
    limit = 100
    test = False
    args = sys.argv[1:]
    for i, a in enumerate(args):
        if a == "--limit" and i + 1 < len(args):
            limit = int(args[i + 1])
        if a == "--test":
            test = True

    key = load_key()
    data = json.load(io.open(DATA, encoding="utf-8-sig"))
    items = data["items"]

    pending = [it for it in items if not it.get("ai") and it.get("img")
               and it.get("brand") and it.get("brand") != "NoName"]
    print(f"к проверке: {len(pending)} (лимит {limit}, прокси: {PROXY or 'нет'})", flush=True)
    if test:
        pending = pending[:3]
        limit = len(pending)

    done = 0
    for it in pending:
        if done >= limit:
            break
        try:
            blob, mime = download(it["img"])
            if len(blob) < 1500:
                it["ai"] = {"verdict": "unclear", "confidence": 0.0,
                            "reasons": ["фото слишком маленькое"], "model": MODELS[0]}
                done += 1
                continue
            out = ask(key, MODELS[0], blob, mime, it)
            out["model"] = MODELS[0]
            out["at"] = time.strftime("%Y-%m-%d %H:%M")
            it["ai"] = out
            done += 1
            print(f"  {done}. {it.get('brand')} — {out.get('verdict')} {out.get('confidence')}")
            io.open(DATA, "w", encoding="utf-8").write(json.dumps(data, ensure_ascii=False))
        except Exception as e:
            msg = str(e)
            print("  ошибка:", msg[:200])
            if "429" in msg:
                print("  лимит запросов исчерпан — останавливаюсь")
                break
            if "404" in msg and MODELS[0] != MODELS[-1]:
                try:
                    out = ask(key, MODELS[-1], blob, mime, it)
                    out["model"] = MODELS[-1]
                    out["at"] = time.strftime("%Y-%m-%d %H:%M")
                    it["ai"] = out
                    done += 1
                    continue
                except Exception as e2:
                    print("  fallback не сработал:", str(e2)[:160])
                    break
            if "location is not supported" in msg or "403" in msg:
                break
        time.sleep(1.2)

    data["items"] = items
    io.open(DATA, "w", encoding="utf-8").write(json.dumps(data, ensure_ascii=False))
    have = sum(1 for it in items if it.get("ai"))
    print(f"готово: проверено {have} из {len(items)}")


if __name__ == "__main__":
    main()
