"""Gmail（SMTP + アプリパスワード）でメールを送る。

必要な環境変数（GitHub Actions の Secrets に登録）:
  GMAIL_ADDRESS       送信に使うGmailアドレス
  GMAIL_APP_PASSWORD  Googleアカウントで発行したアプリパスワード（16文字）
  MAIL_TO             送信先（省略時は GMAIL_ADDRESS）
  APP_URL             画面のURL（メール内のリンクに使う。省略可）
環境変数がない場合はメールを送らず、内容を標準出力に表示する（ローカル確認用）。
"""

from __future__ import annotations

import html
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from . import config


def _send(subject: str, html_body: str, text_body: str) -> None:
    addr = os.environ.get("GMAIL_ADDRESS")
    password = os.environ.get("GMAIL_APP_PASSWORD")
    to = os.environ.get("MAIL_TO") or addr
    if not addr or not password:
        print("[メール未送信: GMAIL_ADDRESS / GMAIL_APP_PASSWORD が未設定]")
        print("件名:", subject)
        print(text_body)
        return
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = addr
    msg["To"] = to
    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=60) as s:
        s.login(addr, password.replace(" ", ""))
        s.send_message(msg)


def _price(v) -> str:
    return f"{v:.1f}万円" if v is not None else "記載なし"


def _km(v) -> str:
    return f"{v * 10000:,.0f}km" if v is not None else "-"


def _car_line(c: dict) -> str:
    return (
        f"{c['model']} {c['grade']} / 支払総額 {_price(c.get('total_price'))} / "
        f"{c.get('year')}年 / {_km(c.get('mileage'))} / {c.get('pref')}{c.get('city')}\n  {c['url']}"
    )


def _car_row(c: dict, extra: str = "") -> str:
    e = html.escape
    img = (
        f'<img src="{e(c["image"])}" width="120" style="border-radius:6px;display:block">'
        if c.get("image")
        else ""
    )
    reasons = c.get("near_reasons") or []
    reason_html = (
        f'<div style="color:#b45309;font-size:12px">惜しい点: {e(" / ".join(reasons))}</div>' if reasons else ""
    )
    return f"""
<tr>
 <td style="padding:8px 8px 8px 0;vertical-align:top;width:120px">{img}</td>
 <td style="padding:8px 0;vertical-align:top;font-size:14px;line-height:1.5">
  <a href="{e(c['url'])}" style="color:#1d4ed8;font-weight:bold;text-decoration:none">{e(c['model'])} {e(c['grade'])}</a><br>
  <span style="font-size:16px;font-weight:bold">支払総額 {e(_price(c.get('total_price')))}</span>
  <span style="color:#555">（本体 {e(_price(c.get('base_price')))}）</span>{extra}<br>
  {e(str(c.get('year')))}年 / {e(_km(c.get('mileage')))} / {e(c.get('color', ''))} / {e(c.get('pref', ''))}{e(c.get('city', ''))}<br>
  <span style="color:#555;font-size:12px">{e(c.get('shop', ''))}</span>
  {reason_html}
 </td>
</tr>"""


def _section(title: str, rows: list[str], color: str) -> str:
    if not rows:
        return ""
    return f"""
<h2 style="font-size:17px;border-left:5px solid {color};padding-left:8px;margin:24px 0 4px">{html.escape(title)}（{len(rows)}台）</h2>
<table style="border-collapse:collapse;width:100%">{''.join(rows)}</table>"""


def send_daily(events: dict, market: dict, active: list[dict], first_run: bool = False) -> None:
    new_main = sorted((c for c in events["new"] if c["tier"] == "main"), key=lambda c: c.get("total_price") or 999)
    new_near = sorted((c for c in events["new"] if c["tier"] == "near"), key=lambda c: c.get("total_price") or 999)
    new_ids = {c["id"] for c in new_main + new_near}
    drops = sorted(
        (c for c in events["price_drop"] if c["id"] not in new_ids),
        key=lambda c: c["price_now"] - c["price_before"],
    )
    ended = events["ended_starred"]
    main_total = sum(1 for c in active if c["tier"] == "main")
    app_url = os.environ.get("APP_URL", "")

    parts = []
    if new_main:
        parts.append(f"本命{len(new_main)}")
    if drops:
        parts.append(f"値下げ{len(drops)}")
    if new_near:
        parts.append(f"惜しい{len(new_near)}")
    if ended:
        parts.append(f"星の掲載終了{len(ended)}")
    head = "【初回】" if first_run else ""
    subject = f"{head}マツダ中古車アラート：" + ("・".join(parts) if parts else "新しい動きなし") + f"（掲載中の本命 {main_total}台）"

    # --- HTML ---
    drop_rows = [
        _car_row(
            c,
            f' <span style="color:#dc2626;font-weight:bold">▼{c["price_before"] - c["price_now"]:.1f}万円</span>'
            f' <span style="color:#555">（{c["price_before"]:.1f}→{c["price_now"]:.1f}）</span>',
        )
        for c in drops
    ]
    ended_rows = [_car_row(c, ' <span style="color:#6b7280;font-weight:bold">掲載終了</span>') for c in ended]

    m_rows = "".join(
        f"<tr><td style='padding:2px 12px 2px 0'><b>{g}</b></td>"
        f"<td style='padding:2px 12px 2px 0'>本命 {m['main']}台 / 惜しい {m['near']}台</td>"
        f"<td style='padding:2px 0'>5万km以内の支払総額 中央値 {_price(m['median'])}・最安 {_price(m['min'])}（{m['count']}台）</td></tr>"
        for g, m in market.items()
    )
    app_link = (
        f'<p><a href="{html.escape(app_url)}" style="display:inline-block;background:#1d4ed8;color:#fff;'
        f'padding:8px 14px;border-radius:6px;text-decoration:none">アプリで一覧を見る</a></p>'
        if app_url
        else ""
    )
    nothing = (
        "<p style='color:#555'>今日は新着・値下げはありませんでした。</p>"
        if not (new_main or new_near or drops or ended)
        else ""
    )
    body = f"""<div style="font-family:sans-serif;max-width:680px;color:#111">
<p style="margin:0 0 8px">条件：{config.YEAR_MIN}年以降・修復歴なし・全方位カメラ・ガソリン車・支払総額{config.MAIN_TOTAL_PRICE_MAX:.0f}万円以内・{config.MAIN_MILEAGE_MAX:.0f}万km以内</p>
{nothing}
{_section("本命の新着", [_car_row(c, f' <span style="color:#555">[{c.get("new_reason", "")}]</span>') for c in new_main], "#16a34a")}
{_section("値下げ", drop_rows, "#dc2626")}
{_section("星をつけた車の掲載終了", ended_rows, "#6b7280")}
{_section("惜しい候補の新着", [_car_row(c) for c in new_near], "#d97706")}
<h2 style="font-size:17px;border-left:5px solid #1d4ed8;padding-left:8px;margin:24px 0 4px">相場（関西・福井・三重）</h2>
<table style="border-collapse:collapse;font-size:14px">{m_rows}</table>
{app_link}
<p style="color:#888;font-size:12px">情報元：カーセンサー（全周囲カメラ・修復歴なしで絞り込み）。掲載内容は必ず販売店で確認してください。</p>
</div>"""

    # --- テキスト版 ---
    lines = [subject, ""]
    for title, items in (("■本命の新着", new_main), ("■惜しい候補の新着", new_near), ("■星の掲載終了", ended)):
        if items:
            lines.append(title)
            lines += [_car_line(c) for c in items]
            lines.append("")
    if drops:
        lines.append("■値下げ")
        lines += [f"▼{c['price_before'] - c['price_now']:.1f}万円 " + _car_line(c) for c in drops]
        lines.append("")
    lines.append("■相場")
    for g, m in market.items():
        lines.append(f"{g}: 本命{m['main']} 惜しい{m['near']} 中央値{_price(m['median'])} 最安{_price(m['min'])}")
    if app_url:
        lines += ["", app_url]

    _send(subject, body, "\n".join(lines))


def send_error(message: str) -> None:
    subject = "マツダ中古車アラート：取得エラー"
    text = (
        "今日の検索でエラーが発生しました。データは更新していません。\n"
        "サイトの一時的な不調であれば翌日には直ります。数日続く場合はページ構造が変わった可能性があります。\n\n"
        + message
    )
    _send(subject, f"<pre style='white-space:pre-wrap'>{html.escape(text)}</pre>", text)
