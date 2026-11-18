"""カーセンサーの検索結果ページから車両情報を取得する。"""

from __future__ import annotations

import re
import time
from urllib.parse import urlencode, urljoin

import requests
from bs4 import BeautifulSoup

from . import config

BASE = "https://www.carsensor.net"


class ScrapeError(Exception):
    pass


def build_search_url() -> str:
    params = {
        "CARC": "*".join(config.MODELS),
        "AR": "*".join(config.PREFS),
        "YMIN": str(config.YEAR_MIN),
        "SMAX": str(config.SEARCH_MILEAGE_MAX_KM),
        # REP0 = 修復歴なし, MLT1 = 全周囲カメラ
        "OPTCD": "REP0*MLT1",
    }
    return f"{BASE}/usedcar/index.html?" + urlencode(params, safe="*")


def _num(text: str | None) -> float | None:
    if not text:
        return None
    m = re.search(r"[\d.,]+", text)
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", ""))
    except ValueError:
        return None


def _price(box) -> float | None:
    """'199' + '.9' のように分かれている価格を 199.9 にする。"""
    if box is None:
        return None
    main = box.select_one('[class$="mainPriceNum"]')
    sub = box.select_one('[class$="subPriceNum"]')
    if main is None:
        return None
    return _num(main.get_text(strip=True) + (sub.get_text(strip=True) if sub else ""))


def _spec(card) -> dict[str, str]:
    out = {}
    for box in card.select(".specList__detailBox"):
        dt, dd = box.find("dt"), box.find("dd")
        if dt and dd:
            out[dt.get_text(strip=True)] = dd.get_text(" ", strip=True)
    return out


def is_diesel(title: str, displacement: str) -> bool:
    if re.search(r"(?<![A-Za-z])XD(?![A-Za-z])", title) or "ディーゼル" in title:
        return True
    # MAZDA3 / CX-30 のディーゼルは 1.8L のみ
    return displacement.replace(" ", "").upper().startswith("1800")


def parse_cards(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    cars = []
    for card in soup.select("div.cassette.js_listTableCassette"):
        cid = (card.get("id") or "").removesuffix("_cas")
        title_a = card.select_one("h3.cassetteMain__title a")
        if not cid or title_a is None:
            continue
        full_title = title_a.get_text(" ", strip=True).replace("\xa0", " ")
        parts = [p.strip() for p in title_a.get_text().split("\xa0")]
        model = parts[0] if parts else ""
        grade = parts[1] if len(parts) > 1 else ""
        catch = " ".join(parts[2:]) if len(parts) > 2 else ""

        spec = _spec(card)
        img = card.select_one(".cassetteMain__mainImg img")
        img_src = img.get("src") or img.get("data-original") if img else None
        if img_src and img_src.startswith("//"):
            img_src = "https:" + img_src

        area = [p.get_text(strip=True) for p in card.select(".cassetteSub__area p")]
        shop = card.select_one(".cassetteSub__shop a")
        body_items = [li.get_text(strip=True) for li in card.select(".carBodyInfoList__item")]

        cars.append(
            {
                "id": cid,
                "model": model,
                "grade": grade,
                "catch": catch,
                "title": full_title,
                "total_price": _price(card.select_one(".totalPrice")),
                "base_price": _price(card.select_one(".basePrice")),
                "year": int(_num(spec.get("年式")) or 0) or None,
                "mileage": _num(spec.get("走行距離")),
                "mileage_text": spec.get("走行距離", ""),
                "repair": spec.get("修復歴", ""),
                "inspection": spec.get("車検", ""),
                "warranty": spec.get("保証", ""),
                "displacement": spec.get("排気量", ""),
                "mission": spec.get("ミッション", ""),
                "body": body_items[0] if body_items else "",
                "color": body_items[1] if len(body_items) > 1 else "",
                "pref": area[0] if area else "",
                "city": area[1] if len(area) > 1 else "",
                "shop": shop.get_text(strip=True) if shop else "",
                "image": img_src,
                "url": f"{BASE}/usedcar/detail/{cid}/index.html",
            }
        )
    return cars


def _mileage_km(car: dict) -> float | None:
    """'3.8万km' → 3.8、'9000km' → 0.9 のように万km単位にそろえる。"""
    text = car.get("mileage_text", "")
    if car.get("mileage") is None:
        return None
    if "万" in text:
        return car["mileage"]
    return car["mileage"] / 10000


def parse_total_count(html: str) -> int | None:
    m = re.search(r'id="sbmt"[^>]*>\s*<span>([\d,]+)</span>台', html)
    return int(m.group(1).replace(",", "")) if m else None


def _next_page_url(html: str, current: str) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    for a in soup.select(".pager a, a.pager__btn__next, button.pager__btn__next"):
        if "次" in a.get_text() and a.get("href"):
            return urljoin(current, a["href"])
    # 予備: indexN.html 形式のリンクから次のページ番号を探す
    cur = re.search(r"index(\d*)\.html", current)
    cur_no = int(cur.group(1) or 1) if cur else 1
    for href in re.findall(r'href="([^"]*index(\d+)\.html\?[^"]*)"', html):
        if int(href[1]) == cur_no + 1:
            return urljoin(current, href[0].replace("&amp;", "&"))
    return None


def _get(session: requests.Session, url: str) -> requests.Response:
    """一時的な通信エラーに備えて、間隔をあけて最大3回まで再試行する。"""
    last = None
    for attempt in range(3):
        try:
            resp = session.get(url, timeout=30)
            if resp.status_code < 500:
                return resp
            last = ScrapeError(f"HTTP {resp.status_code}: {url}")
        except requests.RequestException as e:
            last = e
        time.sleep(15 * (attempt + 1))
    raise ScrapeError(f"取得に失敗しました: {url} ({last})")


def fetch_all() -> dict:
    session = requests.Session()
    session.headers.update({"User-Agent": config.USER_AGENT, "Accept-Language": "ja"})
    url = build_search_url()
    cars: dict[str, dict] = {}
    total = None
    for page in range(config.MAX_PAGES):
        resp = _get(session, url)
        if resp.status_code != 200:
            raise ScrapeError(f"HTTP {resp.status_code}: {url}")
        resp.encoding = "utf-8"
        html = resp.text
        if page == 0:
            total = parse_total_count(html)
            if total is None:
                raise ScrapeError("検索結果の件数を読み取れませんでした（ページ構造が変わった可能性）")
        for car in parse_cards(html):
            car["mileage"] = _mileage_km(car)
            cars[car["id"]] = car
        if len(cars) >= (total or 0):
            break
        nxt = _next_page_url(html, url)
        if not nxt or nxt == url:
            break
        url = nxt
        time.sleep(config.REQUEST_INTERVAL_SEC)

    if total and len(cars) < total * 0.8:
        raise ScrapeError(f"取得件数が少なすぎます（{len(cars)}/{total}台）。ページ構造が変わった可能性")
    return {"total": total, "cars": list(cars.values()), "search_url": build_search_url()}


def check_listing_alive(car_id: str) -> bool:
    """詳細ページを確認して、掲載が続いているかを返す。"""
    try:
        resp = requests.get(
            f"{BASE}/usedcar/detail/{car_id}/index.html",
            headers={"User-Agent": config.USER_AGENT},
            timeout=30,
        )
    except requests.RequestException:
        return True  # 判定できないときは掲載中とみなす
    if resp.status_code in (404, 410):
        return False
    resp.encoding = "utf-8"
    text = resp.text
    if "掲載が終了" in text or "掲載終了" in text or "お探しの物件は" in text:
        return False
    return car_id in text
