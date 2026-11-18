# マツダ中古車ウォッチ

MAZDA3 / CX-30 の中古車をカーセンサーから毎日1回検索し、条件に合う車を Gmail で知らせ、パソコン用の画面で一覧・星保存ができるツールです。

## 仕組み

```
GitHub Actions（毎日 20:45 JST ごろ起動）
  └ scraper/run.py
      ├ カーセンサーの検索結果を取得（約10ページ、4秒間隔）
      ├ 本命 / 惜しい候補 / その他 に分類、ディーゼル除外
      ├ 前日と比較して 新着・値下げ・掲載終了 を判定
      ├ 本命・惜しい候補・星つきの車は詳細ページから状態・保証・装備・全写真を取得（1台1回、値段が変わったら再取得）
      ├ docs/data/listings.json を更新してコミット
      └ Gmail でまとめを送信
GitHub Pages（docs/）
  └ index.html … 一覧画面。星は docs/data/stars.json に GitHub API で保存
```

## 条件を変えたいとき

`scraper/config.py` を編集してコミットするだけです（価格・走行距離・地域・年式など）。

## 初期設定

### 1. Gmail のアプリパスワードを発行
1. Google アカウントで 2段階認証をオンにする
2. https://myaccount.google.com/apppasswords でアプリパスワード（16文字）を作成

### 2. リポジトリの Secrets を登録
GitHub のリポジトリ → Settings → Secrets and variables → Actions → New repository secret

| 名前 | 値 |
|---|---|
| `GMAIL_ADDRESS` | 送信に使う Gmail アドレス |
| `GMAIL_APP_PASSWORD` | 手順1のアプリパスワード |
| `MAIL_TO` | 送信先（省略すると `GMAIL_ADDRESS` 宛て） |

### 3. GitHub Pages を有効化
Settings → Pages → Build and deployment → Source: **Deploy from a branch**、Branch: **main** / フォルダ **/docs** → Save

数分後 `https://<ユーザー名>.github.io/<リポジトリ名>/` で画面が開きます。

### 4. 星を保存するためのトークンを作成
1. https://github.com/settings/personal-access-tokens/new
2. Repository access: **Only select repositories** → このリポジトリだけを選ぶ
3. Permissions → Repository permissions → **Contents: Read and write**
4. 作成したトークンを、画面右上「⚙ 設定」に貼り付けて保存

### 5. 動作確認
Actions タブ → daily-check → **Run workflow** で手動実行できます。

## 注意
- 情報元はカーセンサーの公開検索ページです。負荷をかけないよう1日1回・間隔をあけて取得しています。
- サイトの構造が変わると取得に失敗し、「取得エラー」メールが届きます（その日のデータは更新されません）。
- GitHub の定時実行は混雑時に遅れることがあります。
