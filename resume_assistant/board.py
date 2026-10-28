"""投递看板：把 applications / jobs / events 汇总成「今天该做什么」。

状态机保证每一步都合法，但它不回答三个真实问题：

1. 这十几个岗位现在分别卡在哪一步
2. 哪个快截止了、哪个截止日期已经过了还在 draft
3. 哪个投出去十天没动静，该催一次了

全部用规则算，不调模型——这些问题都有确定答案，问模型只会得到
「建议您及时跟进」这种没有信息量的回答。
"""

from __future__ import annotations

import html
import re
from datetime import date, datetime, timedelta
from typing import Any

from .config import DATA
from .models import TRANSITIONS, AppStatus, JobKind
from .store import db

# ------------------------------------------------------------------ 文案

STATUS_LABEL: dict[str, str] = {
    "draft": "已建档",
    "matched": "已匹配",
    "composed": "已生成",
    "sent": "已投递",
    "screening": "简历筛",
    "replied": "已回复",
    "meeting": "面谈中",
    "interview": "面试中",
    "offer": "Offer",
    "rejected": "未通过",
    "withdrawn": "已放弃",
}

# 每个状态该做什么。allowed 由状态机算，这里只负责说人话
ACTION: dict[str, str] = {
    "draft": "跑 match 看对口度，再决定要不要投",
    "matched": "跑 gen 出简历",
    "composed": "打开 PDF 过一遍，没问题就投出去",
    "sent": "等回复；超过一周可以礼貌跟进一次",
    "screening": "等简历筛结果",
    "replied": "尽快约面谈时间",
    "meeting": "准备面谈材料：成绩单、项目细节、导师方向",
    "interview": "面试前把 JD 和对应证据再过一遍",
    "offer": "决策：接受 / 谈条件 / 婉拒",
    "rejected": "记录原因，看是硬性不符还是措辞问题",
    "withdrawn": "已放弃，不用再管",
}

# 看板分栏。保研与实习的状态名不同，这里合并同类
COLUMNS: list[tuple[str, str, set[str]]] = [
    ("todo", "待办", {"draft", "matched", "composed"}),
    ("sent", "已投出", {"sent", "screening", "replied"}),
    ("active", "进行中", {"meeting", "interview"}),
    ("closed", "已结束", {"offer", "rejected", "withdrawn"}),
]

FINAL = {"offer", "rejected", "withdrawn"}

# 已经投出去、正在等对方的状态
WAITING = {"sent", "screening"}

# 终端里每栏最多列几条，再多就该去看板页了
MAX_TEXT_PER_COL = 8


# ------------------------------------------------------------- 截止日期解析

_YMD = re.compile(r"(\d{4})\s*[-/.年]\s*(\d{1,2})\s*[-/.月]\s*(\d{1,2})")
_MD = re.compile(r"(?<!\d)(\d{1,2})\s*月\s*(\d{1,2})\s*日?")
_RAW = re.compile(r"(?<!\d)(\d{4})(\d{2})(\d{2})(?!\d)")
_VAGUE = ("长期有效", "长期", "招满即止", "招满为止", "尽快", "常年", "不限")


def parse_deadline(text: str, today: date | None = None) -> date | None:
    """从人写的截止日期里抠出一个 date。抠不出返回 None。

    能处理：2026-11-01 / 2026/11/1 / 2026.11.1 / 2026年11月1日 /
    11月1日（按今年算，已过去一个月以上则按下一年）/ 20261101。
    「长期有效」「招满即止」这类返回 None，由调用方提示「没有明确截止」。
    """
    s = (text or "").strip()
    if not s:
        return None
    today = today or date.today()

    m = _YMD.search(s) or _RAW.search(s)
    if m:
        y, mo, d = (int(x) for x in m.groups())
        try:
            return date(y, mo, d)
        except ValueError:
            return None

    m = _MD.search(s)
    if m:
        mo, d = int(m.group(1)), int(m.group(2))
        for y in (today.year, today.year + 1):
            try:
                cand = date(y, mo, d)
            except ValueError:
                continue  # 2 月 29 日这类，换一年再试
            # 只写月日时按「最近的那个」算：今年已过去 30 天以上就当下一年
            if cand >= today - timedelta(days=30):
                return cand
        return None
    return None


def _vague_deadline(text: str) -> bool:
    return any(w in (text or "") for w in _VAGUE)


# ------------------------------------------------------------------ 数据汇总

def _days_since(iso: str, now: datetime | None = None) -> int:
    """ISO 时间戳到现在过了几天。解析失败返回 0（宁可少报，不误报）。"""
    if not iso:
        return 0
    now = now or datetime.now()
    try:
        t = datetime.fromisoformat(iso.replace("Z", ""))
    except ValueError:
        return 0
    return max(0, (now - t).days)


def _allowed(kind: JobKind, status: AppStatus) -> list[str]:
    return sorted(s.value for s in TRANSITIONS.get(kind, {}).get(status, set()))


def collect(today: date | None = None, now: datetime | None = None) -> dict[str, Any]:
    """汇总所有投递记录。纯读，不改任何状态。

    today / now 只为测试注入，正常调用不传。
    """
    today = today or date.today()
    now = now or datetime.now()
    items: list[dict[str, Any]] = []

    for app in db.list_applications():
        job = db.get_job(app.job_id)
        status = app.status.value
        last_at = app.history[-1].at if app.history else app.created_at
        days_in = _days_since(last_at, now)

        deadline_raw = (job.deadline if job else "") or ""
        dl = parse_deadline(deadline_raw, today)
        days_left = (dl - today).days if dl else None

        alerts: list[dict[str, str]] = []

        def add(level: str, text: str) -> None:
            alerts.append({"level": level, "text": text})

        # ---- 截止日期
        if dl and days_left is not None:
            if days_left < 0 and status not in FINAL | {"sent", "screening", "replied", "meeting", "interview"}:
                add("error", f"截止日期 {dl.isoformat()} 已过 {-days_left} 天，还停在「{STATUS_LABEL.get(status, status)}」")
            elif 0 <= days_left <= 3 and status in {"draft", "matched", "composed"}:
                add("error", f"还有 {days_left} 天截止，简历还没投出去")
            elif 0 <= days_left <= 7 and status in {"draft", "matched"}:
                add("warn", f"还有 {days_left} 天截止，简历还没开始做")

        # ---- 状态停留
        if status in WAITING and days_in >= 7:
            add("warn", f"投出去 {days_in} 天没动静，可以跟进一次")
        elif status == "composed" and days_in >= 3:
            add("warn", f"简历 {days_in} 天前就生成好了，还没投")
        elif status == "replied" and days_in >= 2:
            add("warn", f"对方回复 {days_in} 天了，尽快约时间")
        elif status == "draft" and _days_since(app.created_at, now) >= 3:
            add("info", f"建档 {_days_since(app.created_at, now)} 天还没匹配，要不要投可以先跑一下 match")
        elif status == "interview":
            add("info", "面试前把 JD 和对应证据再过一遍")

        # ---- 生成物缺失：说已生成，但 PDF 路径是空的（Typst 报错时会出现）
        # 只在「已生成」阶段提醒。已经投出去了再提这个没有意义，事情已经发生
        if status == "composed" and not app.snapshot.pdf_path:
            add("warn", "状态是已生成，但没有 PDF 路径，重跑一次 gen 确认")

        items.append(
            {
                "app_id": app.id,
                "job_id": app.job_id,
                "kind": app.kind.value,
                "title": (job.title if job else "") or app.job_id,
                "org": (job.org or job.mentor) if job else "",
                "mentor": job.mentor if job else "",
                "location": job.location if job else "",
                "url": job.url if job else "",
                "status": status,
                "status_label": STATUS_LABEL.get(status, status),
                "days_in_status": days_in,
                "age_days": _days_since(app.created_at, now),
                "deadline": deadline_raw,
                "deadline_date": dl.isoformat() if dl else "",
                "days_left": days_left,
                "deadline_vague": bool(deadline_raw) and dl is None and _vague_deadline(deadline_raw),
                "next": ACTION.get(status, ""),
                "allowed": _allowed(app.kind, app.status),
                "pdf": app.snapshot.pdf_path,
                "alerts": alerts,
            }
        )

    # 有事的排前面：error > warn > info > 无事，同级按停留时间长的在前
    _rank = {"error": 0, "warn": 1, "info": 2}

    def worst(it: dict[str, Any]) -> int:
        return min([_rank[a["level"]] for a in it["alerts"]] or [3])

    items.sort(key=lambda it: (worst(it), -it["days_in_status"], it["title"]))

    columns = []
    for key, title, statuses in COLUMNS:
        bucket = [it for it in items if it["status"] in statuses]
        columns.append({"key": key, "title": title, "items": bucket, "count": len(bucket)})

    return {
        "ok": True,
        "today": today.isoformat(),
        "total": len(items),
        "alerts": [it for it in items if it["alerts"]],
        "funnel": _funnel(items),
        "columns": columns,
        "items": items,
    }


def _funnel(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """分线漏斗：建档 → 投出 → 回复 → 面试 → Offer。

    分母用「已投出」，因为还没投的记录不该算进回复率——
    否则一堆草稿会把回复率稀释成 0，看着像投了都没人理。
    """
    out: dict[str, dict[str, Any]] = {}
    for kind, label in (("internship", "实习"), ("baoyan", "保研")):
        rows = [it for it in items if it["kind"] == kind]
        if not rows:
            continue
        total = len(rows)
        sent = sum(1 for it in rows if it["status"] not in {"draft", "matched", "composed"})
        replied = sum(1 for it in rows if it["status"] in {"replied", "meeting", "interview", "offer"})
        interviewed = sum(1 for it in rows if it["status"] in {"meeting", "interview", "offer"})
        offers = sum(1 for it in rows if it["status"] == "offer")
        rejected = sum(1 for it in rows if it["status"] == "rejected")

        def pct(a: int, b: int) -> str:
            return f"{a / b:.0%}" if b else "—"

        out[kind] = {
            "label": label,
            "total": total,
            "sent": sent,
            "replied": replied,
            "interviewed": interviewed,
            "offers": offers,
            "rejected": rejected,
            "draft": sum(1 for it in rows if it["status"] in {"draft", "matched", "composed"}),
            "reply_rate": pct(replied, sent),
            "interview_rate": pct(interviewed, sent),
            "offer_rate": pct(offers, sent),
        }
    return out


# ------------------------------------------------------------------ 文本输出

def to_text(data: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append(f"投递看板 · {data['today']} · 共 {data['total']} 条")

    if data["funnel"]:
        lines.append("")
        for kind, f in data["funnel"].items():
            lines.append(
                f"  {f['label']}：建档 {f['total']} → 投出 {f['sent']} → 回复 {f['replied']}"
                f" → 面试 {f['interviewed']} → Offer {f['offers']}"
                f"　（回复率 {f['reply_rate']}｜面试率 {f['interview_rate']}）"
            )

    if data["alerts"]:
        lines.append("\n需要处理：")
        for it in data["alerts"]:
            for a in it["alerts"]:
                mark = "!" if a["level"] == "error" else ("*" if a["level"] == "warn" else "·")
                lines.append(f"  {mark} {it['title']}　{a['text']}")

    for col in data["columns"]:
        if not col["items"]:
            continue
        lines.append(f"\n【{col['title']}】{col['count']} 个")
        for it in col["items"][:MAX_TEXT_PER_COL]:
            tail = f"　已停留 {it['days_in_status']} 天" if it["days_in_status"] else ""
            lines.append(f"  {it['title']}　{it['org']}　{it['status_label']}{tail}")
            if it["next"]:
                lines.append(f"      → {it['next']}")
        if col["count"] > MAX_TEXT_PER_COL:
            lines.append(f"  … 还有 {col['count'] - MAX_TEXT_PER_COL} 个，看板页或 --json 看全部")

    if not data["total"]:
        lines.append("\n还没有投递记录。先 job add 加岗位，再 gen 生成简历。")
    return "\n".join(lines)


# ------------------------------------------------------------------ HTML

_CSS = """
*{box-sizing:border-box}
body{margin:0;padding:26px 20px 60px;background:#f5f6f8;color:#1a1a1a;
  font:14px/1.6 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif}
.wrap{max-width:1280px;margin:0 auto}
h1{font-size:21px;margin:0 0 6px}
.sub{color:#666;font-size:13px;margin-bottom:18px}
.bar{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:8px}
.stat{background:#fff;border:1px solid #e3e6ea;border-radius:8px;padding:10px 16px;min-width:88px}
.stat b{display:block;font-size:20px;line-height:1.2}
.stat span{color:#777;font-size:12px}
.stat.hot b{color:#d9534f}
.panel{background:#fff;border:1px solid #e3e6ea;border-left:4px solid #d9534f;border-radius:8px;
  padding:12px 18px;margin:16px 0}
.panel.warn{border-left-color:#e0a800}
.panel h2{font-size:14px;margin:0 0 8px;color:#444}
.todo{font-size:13px;padding:3px 0}
.todo .lv-error{color:#d9534f;font-weight:600}
.todo .lv-warn{color:#96591a;font-weight:600}
.todo .lv-info{color:#6b7280}
.board{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;align-items:start;margin-top:20px}
@media(max-width:1000px){.board{grid-template-columns:repeat(2,1fr)}}
.col{background:#eef0f3;border-radius:10px;padding:10px}
.col h3{font-size:13px;margin:2px 4px 10px;color:#3b4a5a;display:flex;justify-content:space-between}
.col h3 em{font-style:normal;color:#8a9099;font-weight:400}
.card{background:#fff;border:1px solid #e3e6ea;border-left:3px solid #cfd4da;border-radius:8px;
  padding:11px 13px;margin-bottom:9px}
.card.error{border-left-color:#d9534f}
.card.warn{border-left-color:#e0a800}
.card.ok{border-left-color:#3f9c5a}
.ttl{font-weight:600;font-size:14px;line-height:1.4}
.org{color:#666;font-size:12.5px;margin-top:2px}
.tags{display:flex;gap:5px;flex-wrap:wrap;margin-top:7px}
.tag{font-size:11.5px;padding:1px 7px;border-radius:999px;background:#eef2f6;color:#3b4a5a}
.tag.k-baoyan{background:#e8f1fb;color:#1f4e79}
.tag.k-intern{background:#eaf6ee;color:#2f7a4a}
.tag.due{background:#fdecec;color:#a33}
.tag.due-soon{background:#fdf3e3;color:#8a5a12}
.nx{font-size:12.5px;color:#4a5560;margin-top:7px;padding-top:7px;border-top:1px dashed #e3e6ea}
.alert{font-size:12.5px;margin-top:6px;padding:3px 0}
.alert.error{color:#d9534f}
.alert.warn{color:#96591a}
.alert.info{color:#6b7280}
.alert::before{content:"•";margin-right:5px}
.empty{color:#9aa0a6;font-size:12.5px;padding:8px 4px}
.foot{margin-top:28px;color:#8a9099;font-size:12px;line-height:1.8}
"""


def _esc(s: str) -> str:
    return html.escape(s or "", quote=True)


def _card(it: dict[str, Any]) -> str:
    lv = "ok"
    if any(a["level"] == "error" for a in it["alerts"]):
        lv = "error"
    elif any(a["level"] == "warn" for a in it["alerts"]):
        lv = "warn"

    kind_cls = "k-baoyan" if it["kind"] == "baoyan" else "k-intern"
    kind_label = "保研" if it["kind"] == "baoyan" else "实习"

    tags = [f"<span class='tag {kind_cls}'>{kind_label}</span>"]
    if it["days_in_status"]:
        tags.append(f"<span class='tag'>停留 {it['days_in_status']} 天</span>")
    if it["deadline"]:
        # 已经投出去的岗位不再标红「已过 N 天」——那时已经在流程里了，
        # 截止日期过了不影响什么，标红只会制造焦虑
        urgent = it["status"] in {"draft", "matched", "composed"}
        if it["days_left"] is not None and urgent:
            if it["days_left"] < 0:
                tags.append(f"<span class='tag due'>已过 {-it['days_left']} 天</span>")
            elif it["days_left"] <= 3:
                tags.append(f"<span class='tag due'>还剩 {it['days_left']} 天</span>")
            elif it["days_left"] <= 7:
                tags.append(f"<span class='tag due-soon'>还剩 {it['days_left']} 天</span>")
            else:
                tags.append(f"<span class='tag'>截止 {it['deadline_date']}</span>")
        else:
            tags.append(f"<span class='tag'>截止 {it['deadline_date'] or _esc(it['deadline'])}</span>")

    alerts = "".join(f"<div class='alert {a['level']}'>{_esc(a['text'])}</div>" for a in it["alerts"])
    org = " · ".join(x for x in (it["org"], it["mentor"], it["location"]) if x)
    return f"""
<div class="card {lv}">
  <div class="ttl">{_esc(it['title'])}</div>
  {f'<div class="org">{_esc(org)}</div>' if org else ''}
  <div class="tags">{''.join(tags)}</div>
  {f'<div class="nx">→ {_esc(it["next"])}</div>' if it['next'] else ''}
  {alerts}
</div>"""


def render_html(data: dict[str, Any], subtitle: str = "") -> str:
    stats = [f"<div class='stat'><b>{data['total']}</b><span>投递记录</span></div>"]
    for f in data["funnel"].values():
        # 一条都没投出去的线不显示比率——「0 / —」除了占位置没有信息量
        if not f["sent"]:
            stats.append(f"<div class='stat'><b>{f['total']}</b><span>{f['label']}建档</span></div>")
            continue
        stats.append(f"<div class='stat'><b>{f['sent']}</b><span>{f['label']}已投出</span></div>")
        stats.append(f"<div class='stat'><b>{f['reply_rate']}</b><span>{f['label']}回复率</span></div>")
        if f["offers"]:
            stats.append(f"<div class='stat'><b>{f['offers']}</b><span>{f['label']}Offer</span></div>")

    n_err = sum(1 for it in data["alerts"] for a in it["alerts"] if a["level"] == "error")
    n_warn = sum(1 for it in data["alerts"] for a in it["alerts"] if a["level"] == "warn")
    if n_err:
        stats.append(f"<div class='stat hot'><b>{n_err}</b><span>必须处理</span></div>")
    if n_warn:
        stats.append(f"<div class='stat'><b>{n_warn}</b><span>建议处理</span></div>")

    todo_rows = []
    for it in data["alerts"]:
        for a in it["alerts"]:
            todo_rows.append(
                f"<div class='todo'><span class='lv-{a['level']}'>[{it['status_label']}] {_esc(it['title'])}</span>"
                f"　{_esc(a['text'])}</div>"
            )
    panel = (
        "<div class='panel'>" + "<h2>需要处理</h2>" + "".join(todo_rows) + "</div>"
        if todo_rows
        else "<div class='panel' style='border-left-color:#3f9c5a'><h2>需要处理</h2>"
        "<div class='todo'>没有需要立刻处理的事。</div></div>"
    )

    cols = []
    for col in data["columns"]:
        cards = "".join(_card(it) for it in col["items"]) or "<div class='empty'>空</div>"
        cols.append(
            f"<div class='col'><h3>{_esc(col['title'])}<em>{col['count']}</em></h3>{cards}</div>"
        )

    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>投递看板 · {data['total']} 条</title>
<style>{_CSS}</style></head>
<body><div class="wrap">
<h1>投递看板</h1>
<div class="sub">{_esc(subtitle) or f"截至 {data['today']}。红色是必须处理（快截止 / 已过期 / 投出去太久没动静），黄色是建议处理。"}</div>
<div class="bar">{''.join(stats)}</div>
{panel}
<div class="board">{''.join(cols)}</div>
<div class="foot">
  状态推进：<code>app advance &lt;app_id&gt; &lt;状态&gt;</code>，非法转移会被状态机拒绝。<br>
  这里只读不改——看板是根据 applications 表和 events 表现算出来的，不会动你的数据。
</div>
</div></body></html>"""


def build(html: bool = True, today: date | None = None, now: datetime | None = None) -> dict[str, Any]:
    data = collect(today=today, now=now)
    if html:
        path = DATA / "board.html"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render_html(data), encoding="utf-8")
        data["html_path"] = str(path)
    return data
