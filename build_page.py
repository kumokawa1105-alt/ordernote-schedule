"""工程表スプシから「通知予定ページ」(schedule.html)を作る。

使い方:
  python build_page.py          schedule.html を作ってブラウザで開く
  python build_page.py --no-open  作るだけ
"""
import html
import os
import subprocess
import sys
from datetime import date, datetime

import gspread

import notify

OUT = os.path.join(notify.HERE, "schedule.html")

# Webhook → Slackチャンネル名（表示用）
CHANNELS = {
    "SLACK_WEBHOOK_ORDERNOTE": "#オーダーノート_チャレンジローンチ",
    "SLACK_WEBHOOK_HYPNO": "#金運ヒプノ_チャレンジローンチ",
    "SLACK_WEBHOOK_POCKET": "#スピスタ_チャレンジローンチ",
}
WEEK = "月火水木金土日"


def jp(d):
    return f"{d.month}/{d.day}（{WEEK[d.weekday()]}）"


def sent_keys():
    if not os.path.exists(notify.SENT_LOG):
        return set()
    return set(open(notify.SENT_LOG, encoding="utf-8").read().splitlines())


def collect():
    notify.load_env()
    gc = gspread.service_account(filename=os.path.join(notify.HERE, "google-credentials.json"))
    sh = gc.open_by_key(os.environ["GOOGLE_SHEETS_ID"])
    titles = {ws.title: ws for ws in sh.worksheets()}
    launches = []
    for tab, webhook_env in notify.TAB_ROUTES.items():
        if tab not in titles:
            continue
        tasks = notify.read_tasks(titles[tab])
        if tasks:
            launches.append({"tab": tab, "channel": CHANNELS.get(webhook_env, webhook_env), "tasks": tasks})
    return launches


def notices(launch):
    """そのタブで通知が出る日 → その日のアクション一覧"""
    days = {}
    for t in launch["tasks"]:
        for d in {t["start"], t["end"]}:
            days.setdefault(d, []).append(t)
    return dict(sorted(days.items()))


def render_upcoming(launches, today, sent):
    items = []
    for ln in launches:
        for d, tasks in notices(ln).items():
            if d >= today:
                items.append((d, ln, tasks))
    items.sort(key=lambda x: (x[0], x[1]["tab"]))
    if not items:
        return '<p class="empty">今後の通知はありません</p>'
    rows = []
    for d, ln, tasks in items[:12]:
        diff = (d - today).days
        when = "今日" if diff == 0 else "明日" if diff == 1 else f"{diff}日後"
        done = f"{d.isoformat()} {ln['tab']}" in sent
        status = '<span class="tag done">送信済み</span>' if done else f'<span class="tag">{when} 14:00</span>'
        acts = "".join(
            f'<li><b>{notify.action_label(t, d)}</b> No.{html.escape(t["no"])} {html.escape(t["name"])}</li>'
            for t in tasks
        )
        rows.append(f"""
        <div class="up{' today' if diff == 0 else ''}">
          <div class="up-date"><span class="big">{jp(d)}</span>{status}</div>
          <div class="up-body">
            <div class="up-tab">{html.escape(ln['tab'])}<span class="ch">→ {html.escape(ln['channel'])}</span></div>
            <ul>{acts}</ul>
          </div>
        </div>""")
    return "".join(rows)


def render_launch(ln, today, sent):
    tasks = ln["tasks"]
    lo = min(t["start"] for t in tasks)
    hi = max(t["end"] for t in tasks)
    span = (hi - lo).days + 1
    pct = lambda d: (d - lo).days / span * 100
    future = [d for d in notices(ln) if d >= today]
    nxt = f"次の通知：<b>{jp(future[0])}</b>" if future else "通知はすべて終了"
    today_line = ""
    if lo <= today <= hi:
        today_line = f'<div class="todayline" style="left:{pct(today) + 50 / span:.2f}%"></div>'
    rows = []
    for t in tasks:
        state = "past" if t["end"] < today else "now" if t["start"] <= today else "future"
        left, width = pct(t["start"]), ((t["end"] - t["start"]).days + 1) / span * 100
        period = jp(t["start"]) if t["start"] == t["end"] else f"{jp(t['start'])} 〜 {jp(t['end'])}"
        rows.append(f"""
        <tr class="{state}">
          <td class="no">{html.escape(t['no'])}</td>
          <td class="ph">{html.escape(t['phase'])}</td>
          <td class="nm">{html.escape(t['name'])}<div class="sub">{period}{' ・ ' + html.escape(t['owner']) if t['owner'] else ''}</div></td>
          <td class="bar"><div class="track">{today_line}<div class="fill" style="left:{left:.2f}%;width:{width:.2f}%"></div></div></td>
        </tr>""")
    done = sum(1 for d in notices(ln) if f"{d.isoformat()} {ln['tab']}" in sent)
    return f"""
    <section class="card">
      <div class="card-head">
        <div><h2>{html.escape(ln['tab'])}</h2><div class="ch">{html.escape(ln['channel'])}</div></div>
        <div class="meta"><div>{nxt}</div><div class="muted">{jp(lo)} 〜 {jp(hi)} ・ 通知{len(notices(ln))}回（送信済み{done}）</div></div>
      </div>
      <table>{''.join(rows)}</table>
    </section>"""


def build(launches, today):
    sent = sent_keys()
    updated = datetime.now(notify.JST)
    cards = "".join(render_launch(ln, today, sent) for ln in launches)
    return f"""<!doctype html>
<html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ローンチ通知予定</title>
<style>
:root {{
  --bg:#f6f5f2; --card:#fff; --ink:#1f2328; --muted:#6b7280; --line:#e6e3dd;
  --accent:#4a154b; --accent-soft:#f3e8f3; --now:#2f7d5b; --now-soft:#e5f3ec; --past:#c9c6c0;
}}
@media (prefers-color-scheme: dark) {{
  :root {{ --bg:#16171a; --card:#1f2125; --ink:#e8e6e3; --muted:#9aa0a6; --line:#2e3136;
          --accent:#d7a6d8; --accent-soft:#3a2a3b; --now:#6fcf9f; --now-soft:#1f3a2e; --past:#4a4d52; }}
}}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--bg); color:var(--ink);
  font-family:-apple-system,"Hiragino Sans","Noto Sans JP",sans-serif; line-height:1.5; }}
main {{ max-width:1040px; margin:0 auto; padding:32px 16px 64px; }}
header h1 {{ margin:0; font-size:24px; }}
header p {{ margin:4px 0 0; color:var(--muted); font-size:13px; }}
h3 {{ font-size:15px; margin:32px 0 12px; color:var(--muted); font-weight:600; }}
.up {{ display:flex; gap:16px; background:var(--card); border:1px solid var(--line); border-radius:12px;
  padding:14px 16px; margin-bottom:8px; }}
.up.today {{ border-color:var(--accent); box-shadow:0 0 0 1px var(--accent); }}
.up-date {{ width:130px; flex:none; display:flex; flex-direction:column; gap:6px; align-items:flex-start; }}
.big {{ font-size:17px; font-weight:700; }}
.tag {{ font-size:12px; background:var(--accent-soft); color:var(--accent); padding:2px 8px; border-radius:999px; }}
.tag.done {{ background:var(--now-soft); color:var(--now); }}
.up-body {{ min-width:0; flex:1; }}
.up-tab {{ font-weight:700; }}
.ch {{ color:var(--muted); font-size:12px; font-weight:400; margin-left:8px; }}
.card-head .ch {{ margin-left:0; }}
.up ul {{ margin:4px 0 0; padding-left:18px; font-size:14px; }}
.up li b {{ color:var(--accent); font-weight:600; margin-right:4px; }}
.card {{ background:var(--card); border:1px solid var(--line); border-radius:12px; padding:18px; margin-bottom:16px; }}
.card-head {{ display:flex; justify-content:space-between; gap:16px; flex-wrap:wrap; margin-bottom:10px; }}
.card-head h2 {{ margin:0; font-size:18px; }}
.meta {{ text-align:right; font-size:14px; }}
.muted {{ color:var(--muted); font-size:12px; }}
table {{ width:100%; border-collapse:collapse; table-layout:fixed; }}
td {{ padding:8px 6px; border-top:1px solid var(--line); vertical-align:middle; font-size:14px; }}
td.no {{ width:36px; color:var(--muted); }}
td.ph {{ width:70px; color:var(--muted); font-size:12px; }}
td.nm {{ width:42%; }}
.sub {{ color:var(--muted); font-size:12px; }}
.track {{ position:relative; height:10px; background:var(--bg); border-radius:999px; }}
.fill {{ position:absolute; top:0; bottom:0; border-radius:999px; background:var(--accent); min-width:6px; }}
tr.past td {{ color:var(--muted); }}
tr.past .fill {{ background:var(--past); }}
tr.now .fill {{ background:var(--now); }}
tr.now td.nm {{ font-weight:700; }}
.todayline {{ position:absolute; top:-6px; bottom:-6px; width:2px; background:#e0523f; z-index:1; }}
.legend {{ display:flex; gap:14px; font-size:12px; color:var(--muted); margin:8px 0 12px; flex-wrap:wrap; }}
.legend i {{ display:inline-block; width:10px; height:10px; border-radius:3px; margin-right:4px; vertical-align:-1px; }}
.empty {{ color:var(--muted); }}
@media (max-width:640px) {{
  .up {{ flex-direction:column; gap:6px; }}
  .up-date {{ width:auto; flex-direction:row; align-items:center; }}
  .meta {{ text-align:left; }}
  td.ph {{ display:none; }}
  td.nm {{ width:58%; }}
  .up-tab .ch {{ display:block; margin-left:0; }}
  .card {{ padding:14px 12px; }}
}}
</style></head>
<body><main>
<header>
  <h1>ローンチ通知予定</h1>
  <p>工程表スプシの開始日・終了日に、毎日14:00にSlackへ通知します ・ 更新 {updated:%Y/%m/%d %H:%M}</p>
</header>
<h3>これからの通知</h3>
{render_upcoming(launches, today, sent)}
<h3>ローンチ別の日程</h3>
<div class="legend"><span><i style="background:var(--now)"></i>進行中</span><span><i style="background:var(--accent)"></i>これから</span><span><i style="background:var(--past)"></i>終了</span><span><i style="background:#e0523f;width:2px"></i>今日</span></div>
{cards}
</main></body></html>"""


def main():
    today = datetime.now(notify.JST).date()
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(build(collect(), today))
    print(f"作成: {OUT}")
    if "--no-open" not in sys.argv:
        subprocess.run(["open", OUT])


if __name__ == "__main__":
    main()
