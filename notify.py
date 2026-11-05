"""工程表スプシを読み、今日が開始日・終了日のタスクがあればSlackへ通知する。

使い方:
  python notify.py               今日(JST)の分を送信
  python notify.py 2026-11-06    指定日として送信
  python notify.py --dry         送らずに本文を表示するだけ
  python notify.py --force       送信済みの日でももう一度送る

環境変数（.env でも可）:
  GOOGLE_SHEETS_ID           工程表スプシのID
  SLACK_WEBHOOK_ORDERNOTE    #オーダーノート_チャレンジローンチ のWebhook
  SLACK_WEBHOOK_HYPNO        #金運ヒプノ_チャレンジローンチ のWebhook
  SLACK_WEBHOOK_POCKET       #スピスタ_チャレンジローンチ のWebhook（ポケット・スピスタ）
"""
import os
import sys
import unicodedata
from datetime import date, datetime, timedelta, timezone

import gspread
import requests

JST = timezone(timedelta(hours=9))
HERE = os.path.dirname(os.path.abspath(__file__))
SENT_LOG = os.path.join(HERE, "sent_dates.txt")
SEND_FROM_HOUR = 14

# タブ名に含まれる言葉 → 送り先Webhookの環境変数名。
# 「1月オーダーノート」のようにタブを足すだけで自動で対象になる。新しい商品はここに1行足す。
KEYWORD_ROUTES = {
    "オーダーノート": "SLACK_WEBHOOK_ORDERNOTE",
    "金運ヒプノ": "SLACK_WEBHOOK_HYPNO",
    "ポケット": "SLACK_WEBHOOK_POCKET",
    "スピスタ": "SLACK_WEBHOOK_POCKET",
}


def route_for(tab):
    if "テンプレート" in tab:
        return None
    return next((env for kw, env in KEYWORD_ROUTES.items() if kw in tab), None)


def load_env():
    path = os.path.join(HERE, ".env")
    if not os.path.exists(path):
        return
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


def parse_date(s):
    s = s.strip()
    for fmt in ("%Y/%m/%d", "%Y-%m-%d", "%Y年%m月%d日"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None


def load_tabs(sh):
    """通知対象のタブを1回のAPI呼び出しでまとめて読む → [(タブ名, Webhook環境変数名, 行)]"""
    targets = [(ws.title, route_for(ws.title)) for ws in sh.worksheets()]
    targets = [(tab, env) for tab, env in targets if env]
    if not targets:
        return []
    res = sh.values_batch_get(["'" + tab.replace("'", "''") + "'" for tab, _ in targets])
    return [(tab, env, vr.get("values", [])) for (tab, env), vr in zip(targets, res["valueRanges"])]


def read_tasks(rows):
    """ヘッダー行（先頭セルが No）以降をタスクとして読む。"""
    head_idx = next((i for i, r in enumerate(rows) if r and r[0].strip() == "No"), None)
    if head_idx is None:
        return []
    head = [h.strip() for h in rows[head_idx]]
    col = {name: head.index(name) for name in head if name}
    tasks = []
    for r in rows[head_idx + 1:]:
        get = lambda name: r[col[name]].strip() if name in col and col[name] < len(r) else ""
        start, end = parse_date(get("開始日")), parse_date(get("終了日"))
        if not get("No") or not start or not end:
            continue
        tasks.append({
            "no": get("No"), "phase": get("フェーズ"), "name": get("タスク名"),
            "start": start, "end": end, "owner": get("担当者"),
        })
    return tasks


def action_label(t, today):
    if t["start"] == today and t["end"] == today:
        return "開始＆終了日"
    if t["start"] == today:
        return "開始日"
    if t["end"] == today:
        return "終了日"
    return None


def md(d):
    return f"{d.month}/{d.day}"


def width(text):
    """全角を2、半角を1として表示幅を数える（コードブロック内で列を揃えるため）。"""
    return sum(2 if unicodedata.east_asian_width(c) in "WFA" else 1 for c in text)


def pad(text, w):
    return text + " " * max(w - width(text), 1)


TODAY_MARK = {"開始＆終了日": "← 今日！", "開始日": "← 今日開始！", "終了日": "← 今日終了！"}


def build_message(tab, tasks, today):
    no_w = max(width(f"No.{t['no']}") for t in tasks) + 2
    phase_w = max(width(t["phase"]) for t in tasks) + 2
    name_w = max(width(t["name"]) for t in tasks) + 2
    rows = []
    for t in tasks:
        label = action_label(t, today)
        row = ("▶ " if label else "  ") + pad(f"No.{t['no']}", no_w) + pad(t["phase"], phase_w)
        row += "| " + pad(t["name"], name_w) + f"{md(t['start'])}〜{md(t['end'])}"
        if label:
            row += "  " + TODAY_MARK[label]
        rows.append(row)
    actions = []
    for t in tasks:
        label = action_label(t, today)
        if label:
            owner = f"（担当: {t['owner']}）" if t["owner"] else ""
            actions.append(f"・[{label}] No.{t['no']} {t['name']}{owner}")
    return "\n".join([
        f"*【{tab}】本日（{today:%Y/%m/%d}）スケジュール通知*",
        "■ 全体スケジュール",
        "```", *rows, "```",
        "■ 本日のアクション",
        *actions,
    ])


def sent_key(today, tab):
    return f"{today.isoformat()} {tab}"


def already_sent(key):
    if not os.path.exists(SENT_LOG):
        return False
    return key in open(SENT_LOG, encoding="utf-8").read().splitlines()


def mark_sent(key):
    with open(SENT_LOG, "a", encoding="utf-8") as f:
        f.write(key + "\n")


def main():
    load_env()
    args = sys.argv[1:]
    dry, force = "--dry" in args, "--force" in args
    dates = [a for a in args if not a.startswith("--")]
    now = datetime.now(JST)
    today = date.fromisoformat(dates[0]) if dates else now.date()

    # 日付指定なしの自動実行は14時前なら送らない（GitHubのschedule遅延で深夜に翌日分が出るのを防ぐ）
    if not dates and now.hour < SEND_FROM_HOUR and not dry:
        print(f"{now:%H:%M} は{SEND_FROM_HOUR}時前のため送信しない")
        return

    gc =gspread.service_account(filename=os.path.join(HERE, "google-credentials.json"))
    sh = gc.open_by_key(os.environ["GOOGLE_SHEETS_ID"])
    sent_any = False
    for tab, webhook_env, rows in load_tabs(sh):
        tasks = read_tasks(rows)
        if not any(action_label(t, today) for t in tasks):
            continue
        sent_any = True
        key = sent_key(today, tab)
        if already_sent(key) and not force and not dry:
            print(f"{key} は送信済みのためスキップ")
            continue
        text = build_message(tab, tasks, today)
        if dry:
            print(f"[送り先: {webhook_env}]\n{text}\n" + "-" * 40)
            continue
        url = os.environ.get(webhook_env)
        if not url:
            print(f"{tab}: {webhook_env} が未設定のため送信スキップ")
            continue
        res = requests.post(url, json={"text": text}, timeout=30)
        print(f"{tab} → Slack送信: {res.status_code} {res.text}")
        res.raise_for_status()
        mark_sent(key)

    if not sent_any:
        print(f"{today}: 開始・終了のタスクなし（送信なし）")


if __name__ == "__main__":
    main()
