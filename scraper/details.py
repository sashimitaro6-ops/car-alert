"""カーセンサーの車両詳細ページから、状態・保証・装備・全写真を取得する。

本命・惜しい候補・星つきの車だけを対象にし、1台につき1回だけ取得する
（価格が変わったときは取り直す）。初回以降は1日に数ページ程度のアクセスになる。
"""

from __future__ import annotations

import re
import time
import unicodedata

import requests
from bs4 import BeautifulSoup

from . import config

BASE = "https://www.carsensor.net"

# 詳細ページの表から取り出す項目（表示名 → ページ上の見出し）
SPEC_KEYS = [
    "ワンオーナー", "禁煙車", "定期点検記録簿", "修復歴", "リサイクル料", "車検", "法定整備", "保証",
    "色", "駆動方式", "ハンドル", "乗車定員", "ドア数", "排気量", "ミッション", "エンジン種別",
    "使用燃料", "WLTC燃費", "JC08燃費", "車体寸法", "車両重量", "最小回転半径", "発売年月", "年式(初度登録年)",
]


def _clean(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    return re.sub(r"\s+", " ", text).strip()


def parse_detail(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")

    # --- 表（状態・スペック）。同じ見出しが2回出るもの（駆動方式など）は最初を採用
    table: dict[str, str] = {}
    for th in soup.find_all("th"):
        td = th.find_next_sibling("td")
        if not td:
            continue
        key = _clean(th.get_text(" ", strip=True))
        if key and key not in table:
            table[key] = _clean(td.get_text(" ", strip=True))
    spec = {k: table[k] for k in SPEC_KEYS if k in table and table[k] not in ("", "-", "－")}

    fees = None
    for k, v in table.items():
        if k.startswith("※内:諸費用") or k.startswith("※内：諸費用"):
            m = re.search(r"([\d.]+)\s*万円", v.replace(" ", ""))
            fees = float(m.group(1)) if m else None
            break

    # 保証は「保証付:販売店保証 保証期間:3ヵ月 保証距離:3,000km」までを要約として使う
    if "保証" in spec:
        spec["保証"] = re.split(r"保証費用|詳細については", spec["保証"])[0].strip()
    if "車検" in spec:
        spec["車検"] = re.split(r"車検の取得にあたって", spec["車検"])[0].strip()
    if "法定整備" in spec:
        spec["法定整備"] = re.split(r"※", spec["法定整備"])[0].strip()
    if "WLTC燃費" in spec:
        m = re.match(r"([\d.]+)", spec["WLTC燃費"])
        spec["WLTC燃費"] = f"{m.group(1)}km/L" if m else spec["WLTC燃費"]

    # --- 装備（付いているものだけ）
    equipment: dict[str, list[str]] = {}
    for cat in soup.select(".equipmentList__category"):
        title = cat.select_one(".equipmentList__title")
        if not title:
            continue
        items = []
        for li in cat.select("li.equipmentList__item--active"):
            for a in li.find_all("a"):
                a.decompose()
            name = _clean(li.get_text("", strip=True))
            name = re.sub(r"\(Apple CarPlay、Android Autoなど\)", "", name)
            if name:
                items.append(name)
        equipment[_clean(title.get_text())] = items

    # --- 写真（大きいサイズ、カテゴリと説明つき）
    photos, seen = [], set()
    for a in soup.select("a.js-photo"):
        url = a.get("data-photo") or ""
        if "/CSphoto/" not in url or url in seen:
            continue
        if url.startswith("//"):
            url = "https:" + url
        seen.add(url)
        photos.append(
            {
                "url": url,
                "category": _clean(a.get("data-categoryname") or ""),
                "caption": _clean(a.get("data-caption") or ""),
            }
        )

    return {"spec": spec, "fees": fees, "equipment": equipment, "photos": photos}


def fetch_detail(session: requests.Session, car_id: str) -> dict | None:
    url = f"{BASE}/usedcar/detail/{car_id}/index.html"
    for attempt in range(2):
        try:
            resp = session.get(url, timeout=30)
            if resp.status_code == 200:
                resp.encoding = "utf-8"
                d = parse_detail(resp.text)
                return d if (d["spec"] or d["photos"]) else None
            if resp.status_code in (404, 410):
                return None
        except requests.RequestException:
            pass
        time.sleep(10)
    return None


def enrich(cars: dict[str, dict], stars: dict, today: str, max_pages: int = 150) -> int:
    """対象の車に detail を付ける。取得したページ数を返す。"""
    targets = []
    for cid, c in cars.items():
        if c.get("status") != "active":
            continue
        if c.get("tier") not in ("main", "near") and cid not in stars:
            continue
        d = c.get("detail")
        last_price_date = (c.get("price_history") or [{}])[-1].get("date", "")
        if d and d.get("fetched_on", "") >= last_price_date:
            continue
        targets.append(cid)
    # 本命 → 星つき → 惜しい の順に優先
    targets.sort(key=lambda i: (cars[i].get("tier") != "main", i not in stars))
    session = requests.Session()
    session.headers.update({"User-Agent": config.USER_AGENT, "Accept-Language": "ja"})
    n = 0
    for cid in targets[:max_pages]:
        d = fetch_detail(session, cid)
        n += 1
        if d:
            d["fetched_on"] = today
            cars[cid]["detail"] = d
        time.sleep(config.REQUEST_INTERVAL_SEC)
    return n
