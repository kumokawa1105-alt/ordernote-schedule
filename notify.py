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

# 通知するタブ → 送り先Webhookの環境変数名。新しい月のタブはここに1行足す。
TAB_ROUTES = {
    "11月オーダーノート": "SLACK_WEBHOOK_ORDERNOTE",
    "12月オーダーノート": "SLACK_WEBHOOK_ORDERNOTE",
    "11月金運ヒプノ": "SLACK_WEBHOOK_HYPNO",
}


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


def read_tasks(ws):
    """ヘッダー行（先頭セルが No）以降をタスクとして読む。"""
    rows = ws.get_all_values()
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
    today = date.fromisoformat(dates[0]) if dates else datetime.now(JST).date()

    gc = gspread.service_account(filename=os.path.join(HERE, "google-credentials.json"))
    sh = gc.open_by_key(os.environ["GOOGLE_SHEETS_ID"])
    titles = {ws.title: ws for ws in sh.worksheets()}

    sent_any = False
    for tab, webhook_env in TAB_ROUTES.items():
        if tab not in titles:
            print(f"タブが見つかりません: {tab}")
            continue
        tasks = read_tasks(titles[tab])
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
