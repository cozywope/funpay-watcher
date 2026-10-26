import json
import random
import re
import sys
import time

import requests
from bs4 import BeautifulSoup

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
LOT = re.compile(r"/lots/(\d+)/?$")


def get(url):
    try:
        r = requests.get(url, headers={"User-Agent": UA, "Accept-Language": "ru-RU,ru;q=0.9"},
                         cookies={"cy": "RUB"}, timeout=30)
    except requests.RequestException as e:
        print("!", url, e)
        return None
    if r.status_code != 200:
        print("!", url, "HTTP", r.status_code)
        return None
    return BeautifulSoup(r.text, "html.parser")


def nav_links(soup, url):
    """Ищет блок со ссылками на все разделы той же игры."""
    me = LOT.search(url).group(1)
    for a in soup.find_all("a", href=True):
        m = LOT.search(a["href"])
        if not (m and m.group(1) == me):
            continue
        node = a.parent
        while node is not None and node.name != "body":
            links = {}
            for b in node.find_all("a", href=True):
                mm = LOT.search(b["href"])
                text = b.get_text(" ", strip=True)
                if mm and text:
                    links.setdefault(mm.group(1), text)
            if len(links) >= 4:
                return [{"id": i, "name": t, "url": f"https://funpay.com/lots/{i}/"}
                        for i, t in links.items()]
            node = node.parent
    return [{"id": me, "name": "start", "url": url}]


def label_for(tag, soup):
    i = tag.get("id")
    if i:
        lab = soup.find("label", attrs={"for": i})
        if lab:
            return lab.get_text(" ", strip=True)
    p = tag.find_parent("label")
    return p.get_text(" ", strip=True) if p else ""


def controls(soup):
    out = []
    for s in soup.find_all("select"):
        prev = s.find_previous("label")
        out.append({"kind": "select", "name": s.get("name"),
                    "label": prev.get_text(" ", strip=True) if prev else "",
                    "options": [[o.get("value"), o.get_text(strip=True)]
                                for o in s.find_all("option")][:60]})
    groups = {}
    for i in soup.find_all("input", attrs={"type": ["radio", "checkbox"]}):
        groups.setdefault((i.get("type"), i.get("name")), []).append(
            [i.get("value"), label_for(i, soup)])
    for (t, n), v in groups.items():
        out.append({"kind": t, "name": n, "options": v[:60]})
    for i in soup.find_all("input", attrs={"type": ["text", "number", "search"]}):
        out.append({"kind": "input", "name": i.get("name"), "placeholder": i.get("placeholder")})
    return out


def filter_blocks(soup):
    res = []
    for el in soup.find_all(class_=re.compile("filter", re.I))[:8]:
        res.append({"class": " ".join(el.get("class", [])),
                    "text": el.get_text(" ", strip=True)[:300]})
    return res


def items_info(soup):
    attrs, servers = {}, {}
    cards = soup.select("a.tc-item")
    for a in cards:
        for k, v in a.attrs.items():
            if k.startswith("data-f-") or k == "data-server":
                attrs.setdefault(k, set()).add(str(v))
        sv = a.select_one(".tc-server")
        if sv and a.get("data-server") is not None:
            servers[str(a.get("data-server"))] = sv.get_text(strip=True)
    return {"lots_on_page": len(cards),
            "data_attrs": {k: sorted(v)[:40] for k, v in attrs.items()},
            "servers": servers}


def main(urls):
    result = []
    for u in urls:
        soup = get(u)
        if not soup:
            continue
        start_id = LOT.search(u).group(1)
        cats = nav_links(soup, u)
        print(u, "разделов:", len(cats))
        game = {"start_url": u,
                "title": soup.title.get_text(strip=True) if soup.title else "",
                "categories": []}
        for c in cats:
            if c["id"] == start_id:
                s = soup
            else:
                time.sleep(random.uniform(2, 4))
                s = get(c["url"])
            if not s:
                continue
            game["categories"].append({
                **c,
                "page_title": s.title.get_text(strip=True) if s.title else "",
                "filter_blocks": filter_blocks(s),
                "controls": controls(s),
                **items_info(s),
            })
            print("   готово:", c["name"])
        result.append(game)
        time.sleep(random.uniform(2, 4))
    with open("funpay_scan.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    print("сохранено: funpay_scan.json")


if __name__ == "__main__":
    main(sys.argv[1:])
