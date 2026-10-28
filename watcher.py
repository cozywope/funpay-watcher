import os
import random
import re
import time
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

_u = urlparse(os.environ["SUPABASE_URL"].strip())
SB = f"{_u.scheme}://{_u.netloc}"
KEY = os.environ["SUPABASE_SERVICE_KEY"]
TG_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]

DB = {"apikey": KEY, "Authorization": "Bearer " + KEY, "Content-Type": "application/json"}
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
_rates = None
SYMBOLS = {"$": "USD", "€": "EUR", "£": "GBP", "₴": "UAH", "₸": "KZT", "zł": "PLN",
           "USD": "USD", "EUR": "EUR", "GBP": "GBP", "UAH": "UAH", "KZT": "KZT", "PLN": "PLN"}


def get_rates():
    """Курсы ЦБ РФ: сколько рублей стоит 1 единица валюты."""
    global _rates
    if _rates is None:
        _rates = {"RUB": 1.0}
        try:
            r = requests.get("https://www.cbr-xml-daily.ru/daily_json.js", timeout=15)
            for code, v in r.json()["Valute"].items():
                _rates[code] = float(v["Value"]) / float(v["Nominal"])
        except Exception as e:
            print("! курс ЦБ недоступен, беру запасные:", e)
            _rates.update({"USD": 90.0, "EUR": 98.0})
    return _rates


def to_rub(price, unit):
    """Переводит цену в рубли. Если валюта неизвестна, возвращает None."""
    u = (unit or "").strip()
    if u in ("", "₽", "руб", "RUB"):
        return price
    code = SYMBOLS.get(u)
    rate = get_rates().get(code) if code else None
    if rate is None:
        print(f"! неизвестная валюта {u!r}, цену в рубли не перевести")
        return None
    return price * rate


def norm(s):
    s = re.sub(r"\s+", " ", str(s)).strip().lower()
    s = re.sub(r"^%([^-]+)-.*$", r"\1", s)
    s = re.sub(r"\s*-\s*", "-", s)
    return re.sub(r"^\[free\]\s*", "", s)


def fetch_lots(url):
    r = requests.get(url, headers={"User-Agent": UA, "Accept-Language": "ru-RU,ru;q=0.9"}, timeout=30)
    if r.status_code != 200:
        print(f"! {url}: HTTP {r.status_code}")
        return None
    soup = BeautifulSoup(r.text, "html.parser")
    lots = []
    for a in soup.select("a.tc-item"):
        lot_id = a.get("href", "").split("id=")[-1]
        price_tag = a.select_one(".tc-price")
        if not lot_id.isdigit() or price_tag is None:
            continue
        try:
            price = float(price_tag.get("data-s"))
        except (TypeError, ValueError):
            continue
        attrs = {k: str(v) for k, v in a.attrs.items() if k.startswith("data-f-")}
        skins = attrs.get("data-f-skin")
        server = a.select_one(".tc-server")
        desc = a.select_one(".tc-desc-text")
        unit_tag = price_tag.select_one(".unit")
        unit = unit_tag.get_text(strip=True) if unit_tag else ""
        deal = next((attrs[k] for k in ("data-f-type", "data-f-type2")
                     if attrs.get(k) in ("аренда", "продажа")), "")
        lots.append({
            "id": lot_id, "url": a.get("href"), "price": price, "unit": unit,
            "price_rub": to_rub(price, unit),
            "platform": server.get_text(strip=True) if server else "",
            "title": desc.get_text(strip=True) if desc else "",
            "deal": deal, "attrs": attrs,
            "skins": int(skins) if skins and skins.lstrip("-").isdigit() else None,
            "online": a.get("data-online") == "1", "auto": a.get("data-auto") == "1",
        })
    return lots


def attr_ok(lot, attr_names, value):
    want = norm(value)
    for name in attr_names:
        got = lot["platform"] if name == "@server" else lot["attrs"].get(name)
        if got is None:
            continue
        got = norm(got)
        if got == want or got.startswith(want + " (") or (name == "@server" and want in got):
            return True
    return False


def matches(f, l):
    if f.get("platform") and norm(f["platform"]) != norm(l["platform"]):
        return False
    deal = f.get("deal_type") or "all"
    if deal == "sale" and l["deal"] == "аренда":
        return False
    if deal == "rent" and l["deal"] != "аренда":
        return False
    if f.get("skins_min") is not None and (l["skins"] is None or l["skins"] < f["skins_min"]):
        return False
    if f.get("skins_max") is not None and (l["skins"] is None or l["skins"] > f["skins_max"]):
        return False
    if (f.get("price_min") is not None or f.get("price_max") is not None) and l["price_rub"] is None:
        return False
    if f.get("price_min") is not None and l["price_rub"] < float(f["price_min"]):
        return False
    if f.get("price_max") is not None and l["price_rub"] > float(f["price_max"]):
        return False
    if f.get("online") == "yes" and not l["online"]:
        return False
    if f.get("online") == "no" and l["online"]:
        return False
    if f.get("deliv") == "yes" and not l["auto"]:
        return False
    if f.get("deliv") == "no" and l["auto"]:
        return False
    for item in f.get("extra") or []:
        if not attr_ok(l, item[0], item[1]):
            return False
    return True


def insert_seen(user_id, ids):
    """Записывает лоты как просмотренные, возвращает только реально новые."""
    inserted = set()
    for i in range(0, len(ids), 500):
        rows = [{"user_id": user_id, "lot_id": x} for x in ids[i:i + 500]]
        r = requests.post(
            f"{SB}/rest/v1/seen_lots",
            headers={**DB, "Prefer": "resolution=ignore-duplicates,return=representation"},
            params={"on_conflict": "user_id,lot_id"}, json=rows, timeout=30)
        r.raise_for_status()
        inserted |= {x["lot_id"] for x in r.json()}
    return inserted


FIRST_LIMIT = 10  # сколько уже существующих лотов присылать при создании поиска


def notify(chat_id, text):
    r = requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                      json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True}, timeout=30)
    if not r.ok:
        print("! telegram:", r.status_code, r.text[:200])


def describe(f, l, head="Появилось"):
    if l["price_rub"] is not None:
        price = f"{round(l['price_rub']):,} ₽".replace(",", " ")
    else:
        price = f"{l['price']:g} {l['unit']}"
    parts = [f"{head}: {f['game']}, {f['category'].lower()}"]
    if l["skins"] is not None and l["skins"] >= 0:
        parts.append(f"скинов: {l['skins']}")
    parts.append(price)
    return ", ".join(parts) + "\n" + l["title"][:120] + "\n" + l["url"]


def process(f, lots):
    chat = (f.get("users") or {}).get("telegram_id")
    if not chat:
        print(f"filter {f['id']}: пользователь не вошёл в бота, пропуск")
        return
    found = [l for l in lots if matches(f, l)]
    if not f.get("baselined"):
        insert_seen(f["user_id"], [l["id"] for l in found])
        requests.patch(f"{SB}/rest/v1/filters", headers=DB, params={"id": f"eq.{f['id']}"},
                       json={"baselined": True}, timeout=30).raise_for_status()
        first = found[:FIRST_LIMIT]
        for l in first:
            notify(chat, describe(f, l, "Уже есть"))
            time.sleep(0.5)
        if len(found) > len(first):
            notify(chat, f"Это {len(first)} из {len(found)} подходящих лотов. Остальные не показываю, дальше буду присылать только новые.")
        print(f"filter {f['id']}: первый запуск, отправил {len(first)} из {len(found)} лотов")
        return
    new_ids = insert_seen(f["user_id"], [l["id"] for l in found])
    sent = 0
    for l in found:
        if l["id"] in new_ids:
            notify(chat, describe(f, l))
            sent += 1
            time.sleep(0.5)
    print(f"filter {f['id']}: подходит {len(found)}, новых {sent}")


def main():
    r = requests.get(f"{SB}/rest/v1/filters", headers=DB,
                     params={"enabled": "eq.true", "select": "*,users(telegram_id)"}, timeout=30)
    if not r.ok:
        print("! supabase:", r.status_code, r.text[:300])
        r.raise_for_status()
    by_url = {}
    for f in r.json():
        if f.get("section_url"):
            by_url.setdefault(f["section_url"], []).append(f)
    print(f"фильтров: {sum(len(v) for v in by_url.values())}, страниц: {len(by_url)}")
    for url, flist in by_url.items():
        try:
            lots = fetch_lots(url)
        except Exception as e:
            print("! ошибка загрузки", url, e)
            continue
        if lots is None:
            continue
        print(f"{url}: лотов {len(lots)}")
        for f in flist:
            try:
                process(f, lots)
            except Exception as e:
                print(f"! filter {f.get('id')}: {e}")
        time.sleep(random.uniform(2, 5))


if __name__ == "__main__":
    main()
