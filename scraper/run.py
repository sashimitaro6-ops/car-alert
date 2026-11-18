"""毎日1回実行するメイン処理。

1. カーセンサーから候補を取得
2. 本命 / 惜しい候補 / その他 に分類
3. 前回のデータと比べて「新着」「値下げ」「掲載終了」を判定
4. docs/data/ 以下のJSONを更新（画面はこれを表示する）
5. Gmailで1日のまとめを送信
"""

from __future__ import annotations

import json
import statistics
import sys
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import carsensor, config, mailer

JST = timezone(timedelta(hours=9))
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "docs" / "data"
LISTINGS_PATH = DATA_DIR / "listings.json"
STARS_PATH = DATA_DIR / "stars.json"

# 掲載終了した車（星なし）をデータに残す日数
KEEP_ENDED_DAYS = 30


def load_json(path: Path, default):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return default


def save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def classify(car: dict) -> str:
    """'main'（本命） / 'near'（惜しい候補） / 'other'（相場参考） を返す。"""
    if (car.get("year") or 0) < config.YEAR_MIN or car.get("repair") not in ("なし", ""):
        return "other"
    price, km = car.get("total_price"), car.get("mileage")
    if km is None:
        return "other"
    if price is None:
        # 支払総額の記載がない車は判断できないので、本体価格が範囲内なら惜しい候補として見せる
        base = car.get("base_price")
        if base is not None and base <= config.NEAR_TOTAL_PRICE_MAX and km <= config.NEAR_MILEAGE_MAX:
            return "near"
        return "other"
    if price <= config.MAIN_TOTAL_PRICE_MAX and km <= config.MAIN_MILEAGE_MAX:
        return "main"
    if price <= config.NEAR_TOTAL_PRICE_MAX and km <= config.NEAR_MILEAGE_MAX:
        return "near"
    return "other"


def near_reasons(car: dict) -> list[str]:
    reasons = []
    if car.get("total_price") is None:
        reasons.append("支払総額の記載なし")
    elif car["total_price"] > config.MAIN_TOTAL_PRICE_MAX:
        reasons.append(f"支払総額 +{car['total_price'] - config.MAIN_TOTAL_PRICE_MAX:.1f}万円")
    if car.get("mileage") is not None and car["mileage"] > config.MAIN_MILEAGE_MAX:
        reasons.append(f"走行 +{(car['mileage'] - config.MAIN_MILEAGE_MAX) * 10000:,.0f}km")
    return reasons


def model_group(car: dict) -> str:
    return "CX-30" if car.get("model", "").startswith("CX-30") else "MAZDA3"


def market_summary(cars: list[dict]) -> dict:
    """走行5万km以内のガソリン車の支払総額の中央値など。"""
    out = {}
    for group in ("MAZDA3", "CX-30"):
        prices = sorted(
            c["total_price"]
            for c in cars
            if model_group(c) == group
            and c.get("total_price")
            and c.get("mileage") is not None
            and c["mileage"] <= config.MAIN_MILEAGE_MAX
        )
        out[group] = {
            "count": len(prices),
            "median": round(statistics.median(prices), 1) if prices else None,
            "min": prices[0] if prices else None,
            "main": sum(1 for c in cars if model_group(c) == group and c["tier"] == "main"),
            "near": sum(1 for c in cars if model_group(c) == group and c["tier"] == "near"),
        }
    return out


def update_state(prev: dict, scraped: list[dict], stars: dict, today: str, check_alive=carsensor.check_listing_alive):
    """前回の状態と今回の取得結果から、新しい状態と通知イベントを作る。"""
    prev_cars: dict[str, dict] = prev.get("cars", {})
    cars: dict[str, dict] = {}
    events = {"new": [], "price_drop": [], "ended_starred": []}

    seen = set()
    for car in scraped:
        cid = car["id"]
        seen.add(cid)
        car["tier"] = classify(car)
        car["near_reasons"] = near_reasons(car) if car["tier"] == "near" else []
        old = prev_cars.get(cid)
        rec = dict(car)
        rec["status"] = "active"
        rec["last_seen"] = today
        if old is None:
            rec["first_seen"] = today
            rec["price_history"] = [{"date": today, "total": car["total_price"], "base": car["base_price"]}]
        else:
            rec["first_seen"] = old.get("first_seen", today)
            hist = list(old.get("price_history", []))
            last_total = hist[-1]["total"] if hist else None
            last_base = hist[-1].get("base") if hist else None
            if (car["total_price"], car["base_price"]) != (last_total, last_base):
                hist.append({"date": today, "total": car["total_price"], "base": car["base_price"]})
            rec["price_history"] = hist
            # 値下げ（支払総額、なければ本体価格で比較）
            before = last_total if car["total_price"] is not None else last_base
            now = car["total_price"] if car["total_price"] is not None else car["base_price"]
            if before is not None and now is not None and now < before:
                if rec["tier"] in ("main", "near") or cid in stars:
                    events["price_drop"].append({**rec, "price_before": before, "price_now": now})

        old_tier = old.get("tier") if old and old.get("status") == "active" else None
        if rec["tier"] in ("main", "near") and old_tier not in ("main", "near"):
            rec["new_reason"] = "新着" if old is None else "条件入り（値下げ等）"
            events["new"].append(rec)
        elif rec["tier"] == "main" and old_tier == "near":
            rec["new_reason"] = "本命に昇格"
            events["new"].append(rec)
        cars[cid] = rec

    # 今回見つからなかった車 → 掲載終了の判定
    for cid, old in prev_cars.items():
        if cid in seen:
            continue
        rec = dict(old)
        if old.get("status") == "active":
            # 検索条件から外れただけ（例：値上げで走行距離条件外）の可能性があるので、詳細ページで確認
            alive = check_alive(cid) if (cid in stars or old.get("tier") in ("main", "near")) else False
            if alive:
                rec["status"] = "out_of_search"
                rec["last_seen"] = today
            else:
                rec["status"] = "ended"
                rec["ended_on"] = today
                if cid in stars:
                    events["ended_starred"].append(rec)
        elif old.get("status") == "out_of_search":
            if cid in stars and not check_alive(cid):
                rec["status"] = "ended"
                rec["ended_on"] = today
                events["ended_starred"].append(rec)
        # 古い掲載終了データは削除（星つきは残す）
        if rec["status"] == "ended" and cid not in stars:
            ended = datetime.fromisoformat(rec.get("ended_on", today))
            if datetime.fromisoformat(today) - ended > timedelta(days=KEEP_ENDED_DAYS):
                continue
        cars[cid] = rec

    return cars, events


def main() -> int:
    now = datetime.now(JST)
    today = now.date().isoformat()
    prev = load_json(LISTINGS_PATH, {"cars": {}})
    stars = load_json(STARS_PATH, {})
    first_run = not prev.get("cars")

    try:
        result = carsensor.fetch_all()
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        mailer.send_error(f"{type(e).__name__}: {e}")
        return 1

    scraped = [c for c in result["cars"] if not carsensor.is_diesel(c["title"], c["displacement"])]
    cars, events = update_state(prev, scraped, stars, today)
    active = [c for c in cars.values() if c["status"] == "active"]
    market = market_summary(active)

    data = {
        "updated_at": now.isoformat(timespec="minutes"),
        "search_url": result["search_url"],
        "conditions": {
            "main_total_price_max": config.MAIN_TOTAL_PRICE_MAX,
            "main_mileage_max": config.MAIN_MILEAGE_MAX,
            "near_total_price_max": config.NEAR_TOTAL_PRICE_MAX,
            "near_mileage_max": config.NEAR_MILEAGE_MAX,
            "year_min": config.YEAR_MIN,
            "prefs": list(config.PREFS.values()),
        },
        "market": market,
        "cars": cars,
    }
    save_json(LISTINGS_PATH, data)
    if not STARS_PATH.exists():
        save_json(STARS_PATH, {})

    mailer.send_daily(events, market, active, first_run=first_run)
    print(
        f"取得 {len(result['cars'])}台 / ガソリン {len(scraped)}台 / "
        f"新着 {len(events['new'])} / 値下げ {len(events['price_drop'])} / 星の掲載終了 {len(events['ended_starred'])}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
