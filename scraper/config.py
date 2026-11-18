"""検索条件の設定。条件を変えたいときはこのファイルだけを編集してください。"""

# 対象車種（カーセンサーの車種コード）
MODELS = {
    "MA_S096": "MAZDA3セダン",
    "MA_S097": "MAZDA3ファストバック",
    "MA_S101": "CX-30",
}

# 対象地域（カーセンサーの都道府県コード）
PREFS = {
    "53": "大阪府",
    "54": "兵庫県",
    "52": "京都府",
    "51": "滋賀県",
    "55": "奈良県",
    "56": "和歌山県",
    "50": "三重県",
    "43": "福井県",
}

YEAR_MIN = 2019

# 本命の条件
MAIN_TOTAL_PRICE_MAX = 190.0  # 支払総額（万円）
MAIN_MILEAGE_MAX = 5.0  # 走行距離（万km）

# 惜しい候補の条件（年式・修復歴・全方位カメラ・ディーゼル除外は本命と同じ）
NEAR_TOTAL_PRICE_MAX = 220.0
NEAR_MILEAGE_MAX = 6.0

# 相場把握のため、検索自体は価格上限なし・走行距離はこの値以下で取得する
SEARCH_MILEAGE_MAX_KM = 60000

# ページ取得の間隔（秒）。サイトに負荷をかけないため短くしないこと
REQUEST_INTERVAL_SEC = 4
MAX_PAGES = 20

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)
