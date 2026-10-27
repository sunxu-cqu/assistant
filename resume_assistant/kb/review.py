"""知识库校对：抽取完成后的「人工复核清单」。

抽取是 LLM 做的，出错方式其实高度集中，而且都能用规则查出来——不需要再花
一次 token 问模型「你抽得对吗」：

- 把课程大作业标成 research，于是它出现在保研简历的「科研经历」里
- 时间写成「2024」这种裸年份，或者干脆空着，简历上就显示「时间未填」
- 指标没写来源，将来数字溯源闸门没法判断它是不是编的
- 同一个项目被两个文件夹重复抽出来，简历上出现两条几乎一样的经历

`audit()` 只做检测不做修改：改哪个字段、改成什么，只有你自己知道。
`render_html()` 把这些证据排成卡片页，方便一眼扫完几十条。
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any

from ..models import Evidence

# kind 的「原文佐证词」：抽出来的 kind 如果和原文对不上，多半是模型标错了
KIND_EVIDENCE = {
    "research": ("研究", "课题", "论文", "实验", "组会", "导师", "实验室", "复现", "benchmark", "综述"),
    "intern": ("实习", "公司", "在职", "入职", "部门", "团队", "汇报", "上线", "转正", "leader"),
    "course": ("课程", "作业", "课设", "实验课", "期末", "大作业", "学分"),
    "award": ("奖", "竞赛", "大赛", "杯", "名次", "排名第", "金牌", "银牌", "铜牌"),
    "project": (),  # 项目最宽泛，不设佐证词，否则天天误报
    "other": (),
}

KIND_LABEL = {
    "research": "科研",
    "intern": "实习",
    "project": "项目",
    "course": "课程",
    "award": "竞赛/荣誉",
    "other": "其他",
}


@dataclass
class Finding:
    evidence_id: str
    title: str
    level: str  # warn | info
    field: str
    message: str
    suggestion: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "title": self.title,
            "level": self.level,
            "field": self.field,
            "message": self.message,
            "suggestion": self.suggestion,
        }


@dataclass
class ReviewResult:
    total: int
    flagged: int
    findings: list[Finding] = field(default_factory=list)
    by_evidence: dict[str, list[Finding]] = field(default_factory=dict)

    @property
    def warnings(self) -> int:
        return sum(1 for f in self.findings if f.level == "warn")

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "flagged": self.flagged,
            "warnings": self.warnings,
            "findings": [f.to_dict() for f in self.findings],
        }


_YEAR_RE = re.compile(r"(19|20)\d{2}")


def _text_of(ev: Evidence) -> str:
    """原文 + 标题 + 副标题，作为 kind 佐证的检索范围。"""
    return " ".join(
        [
            ev.raw_excerpt or "",
            ev.title or "",
            ev.subtitle or "",
            ev.claim or "",
            ev.org or "",
            ev.role or "",
        ]
    ).lower()


def _sim(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def audit(evs: list[Evidence]) -> ReviewResult:
    findings: list[Finding] = []

    def add(ev: Evidence, level: str, fld: str, msg: str, sug: str = "") -> None:
        findings.append(Finding(ev.id, ev.title, level, fld, msg, sug))

    for ev in evs:
        # ---- 时间：空着或没有年份，简历上就会露馅
        if not (ev.period or "").strip():
            add(
                ev,
                "warn",
                "period",
                "时间为空，简历上这一段不会显示时间",
                "补成 2024.03 ~ 2024.06；确实没记录的话可以留空，简历会只显示角色",
            )
        elif not _YEAR_RE.search(ev.period):
            add(ev, "warn", "period", f"时间「{ev.period}」里没有年份", "补成 2024.03 ~ 2024.06 这种格式")

        # ---- kind：和原文对不上，多半是模型标错，会直接影响进哪一段
        words = KIND_EVIDENCE.get(str(ev.kind), ())
        body = _text_of(ev)
        if words and not any(w in body for w in words):
            add(
                ev,
                "warn",
                "kind",
                f"标成了「{KIND_LABEL.get(str(ev.kind), ev.kind)}」，但资料里找不到相关字眼",
                f"确认它到底是不是{KIND_LABEL.get(str(ev.kind), ev.kind)}，不是就改成 project / course",
            )

        # ---- 指标：没有指标简历就没数字；有指标但没出处，将来溯源会误杀
        if not ev.metrics:
            add(ev, "info", "metrics", "没有任何量化指标，简历里这条会显得空", "能补数字就补，补不了可接受")
        for m in ev.metrics:
            if (m.source or "").strip() in ("", "未标注"):
                add(
                    ev,
                    "warn",
                    "metrics",
                    f"指标「{m.name}」没有出处",
                    "在 evidence yaml 里补 source，否则数字溯源闸门无法判断真伪",
                )

        # ---- claim 信息量
        n = len(ev.claim or "")
        if n < 12:
            add(ev, "warn", "claim", f"一句话描述只有 {n} 字，信息量太低", "补清楚「做了什么、怎么做的、结果如何」")
        elif n > 120:
            add(ev, "info", "claim", f"一句话描述 {n} 字，组稿时会被压缩", "可手动拆成两条更细的证据")

        # ---- 标签
        if not ev.skill_tags:
            add(ev, "info", "skill_tags", "没有技能标签，检索时只能靠 BM25 命中", "补上技术栈，召回会准很多")

        # ---- 溯源
        if not (ev.raw_excerpt or "").strip():
            add(ev, "warn", "raw_excerpt", "没有原文摘录，无法核对这条是不是编的", "重新抽取该文件夹，或手动补一段原文")

    # ---- 跨证据：疑似重复项目（同一个项目被两个文件夹抽了两遍）
    for i, a in enumerate(evs):
        for b in evs[i + 1 :]:
            ta, tb = (a.title or "").strip(), (b.title or "").strip()
            if not ta or not tb:
                continue
            ratio = _sim(ta, tb)
            contained = (ta in tb or tb in ta) and min(len(ta), len(tb)) >= 4
            if ratio >= 0.62 or contained:
                findings.append(
                    Finding(
                        a.id,
                        a.title,
                        "warn",
                        "duplicate",
                        f"和「{b.title}」高度相似（相似度 {ratio:.2f}），可能是同一个项目被重复抽取",
                        "确认是否重复；重复的话删掉一条，或在 source_dir 里合并",
                    )
                )

    by_ev: dict[str, list[Finding]] = {}
    for f in findings:
        by_ev.setdefault(f.evidence_id, []).append(f)

    # warn 排在前面，方便从上往下处理
    findings.sort(key=lambda f: (0 if f.level == "warn" else 1, f.evidence_id))
    return ReviewResult(total=len(evs), flagged=len(by_ev), findings=findings, by_evidence=by_ev)


def to_text(res: ReviewResult, limit: int = 200) -> str:
    lines: list[str] = []
    lines.append(f"共 {res.total} 条证据，{res.flagged} 条有疑点（{res.warnings} 个警告）")
    if not res.findings:
        lines.append("没有发现问题。")
        return "\n".join(lines)

    shown = 0
    for f in res.findings:
        if shown >= limit:
            lines.append(f"... 还有 {len(res.findings) - shown} 条未显示")
            break
        icon = "!" if f.level == "warn" else "i"
        lines.append(f"  {icon} [{f.evidence_id}] {f.title}")
        lines.append(f"      {f.field}: {f.message}")
        if f.suggestion:
            lines.append(f"      → {f.suggestion}")
        shown += 1
    return "\n".join(lines)


# ------------------------------------------------------------------ HTML

_CSS = """
*{box-sizing:border-box}
body{margin:0;padding:28px 20px 60px;background:#f5f6f8;color:#1a1a1a;
  font:14px/1.65 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif}
.wrap{max-width:1000px;margin:0 auto}
h1{font-size:21px;margin:0 0 6px}
.sub{color:#666;font-size:13px;margin-bottom:18px}
.bar{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:20px}
.stat{background:#fff;border:1px solid #e3e6ea;border-radius:8px;padding:10px 16px;min-width:96px}
.stat b{display:block;font-size:20px;line-height:1.2}
.stat span{color:#777;font-size:12px}
.filters{margin-bottom:16px;display:flex;gap:8px}
.filters button{border:1px solid #d7dbe0;background:#fff;border-radius:999px;padding:5px 14px;
  font-size:13px;cursor:pointer;color:#333}
.filters button.on{background:#1f4e79;border-color:#1f4e79;color:#fff}
.card{background:#fff;border:1px solid #e3e6ea;border-left:4px solid #cfd4da;border-radius:8px;
  padding:14px 18px;margin-bottom:12px}
.card.warn{border-left-color:#d9534f}
.card.info{border-left-color:#e0a800}
.card.ok{border-left-color:#3f9c5a}
.hd{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap}
.ttl{font-weight:600;font-size:15px}
.id{font:12px ui-monospace,Consolas,monospace;color:#8a9099}
.badge{font-size:12px;padding:1px 8px;border-radius:999px;background:#eef2f6;color:#3b4a5a}
.badge.research{background:#e8f1fb;color:#1f4e79}
.badge.intern{background:#eaf6ee;color:#2f7a4a}
.badge.award{background:#fdf1e3;color:#96591a}
.badge.course{background:#f0eefb;color:#55418f}
.meta{color:#666;font-size:13px;margin-top:4px}
.claim{margin-top:8px}
.metrics{margin:8px 0 0;padding-left:0;list-style:none;font-size:13px}
.metrics li{padding:2px 0;color:#333}
.metrics .src{color:#8a9099}
.tags{margin-top:8px;display:flex;gap:6px;flex-wrap:wrap}
.tag{font-size:12px;background:#f1f3f5;border-radius:4px;padding:1px 7px;color:#4a5560}
.issues{margin-top:10px;border-top:1px dashed #e3e6ea;padding-top:8px}
.issue{font-size:13px;padding:3px 0}
.issue.warn::before{content:"!";display:inline-block;width:16px;color:#d9534f;font-weight:700}
.issue.info::before{content:"i";display:inline-block;width:16px;color:#e0a800;font-weight:700}
.sug{color:#777;font-size:12px;margin-left:16px}
.foot{margin-top:26px;color:#8a9099;font-size:12px;line-height:1.8}
"""

_JS = """
const btns=document.querySelectorAll('.filters button');
btns.forEach(b=>b.onclick=()=>{
  btns.forEach(x=>x.classList.remove('on'));b.classList.add('on');
  const k=b.dataset.k;
  document.querySelectorAll('.card').forEach(c=>{
    c.style.display=(k==='all'||c.classList.contains(k))?'':'none';
  });
});
"""


def _esc(s: str) -> str:
    return html.escape(s or "", quote=True)


def _card(ev: Evidence, fs: list[Finding]) -> str:
    level = "warn" if any(f.level == "warn" for f in fs) else ("info" if fs else "ok")
    kind = str(ev.kind)
    meta_bits = [x for x in (ev.org, ev.role, ev.period) if x]
    metrics = "".join(
        f"<li>{_esc(m.name)}：<b>{_esc(m.rendered())}</b>"
        + (f" <span class='src'>（出处：{_esc(m.source)}）</span>" if m.source else "")
        + "</li>"
        for m in ev.metrics
    )
    tags = "".join(f"<span class='tag'>{_esc(t)}</span>" for t in (ev.skill_tags or [])[:10])
    issues = "".join(
        f"<div class='issue {f.level}'>{_esc(f.field)}：{_esc(f.message)}"
        + (f"<div class='sug'>→ {_esc(f.suggestion)}</div>" if f.suggestion else "")
        + "</div>"
        for f in fs
    )
    return f"""
<div class="card {level}">
  <div class="hd">
    <span class="ttl">{_esc(ev.title)}</span>
    <span class="badge {kind}">{_esc(KIND_LABEL.get(kind, kind))}</span>
    <span class="id">{_esc(ev.id)}</span>
  </div>
  <div class="meta">{_esc(" · ".join(meta_bits)) or "（单位 / 角色 / 时间都为空）"}</div>
  <div class="claim">{_esc(ev.claim)}</div>
  {f'<ul class="metrics">{metrics}</ul>' if metrics else ''}
  {f'<div class="tags">{tags}</div>' if tags else ''}
  {f'<div class="issues">{issues}</div>' if issues else ''}
</div>"""


def render_html(res: ReviewResult, evs: list[Evidence], subtitle: str = "") -> str:
    order = {"warn": 0, "info": 1, "ok": 2}

    def rank(ev: Evidence) -> tuple[int, str]:
        fs = res.by_evidence.get(ev.id, [])
        lv = "warn" if any(f.level == "warn" for f in fs) else ("info" if fs else "ok")
        return (order[lv], ev.title or "")

    cards = "".join(_card(ev, res.by_evidence.get(ev.id, [])) for ev in sorted(evs, key=rank))
    n_ok = res.total - res.flagged
    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>知识库校对 · {res.total} 条证据</title>
<style>{_CSS}</style></head>
<body><div class="wrap">
<h1>知识库校对</h1>
<div class="sub">{_esc(subtitle) or "抽取结果逐条核对；红色是需要你确认的疑点，黄色是可选的优化建议。"}</div>
<div class="bar">
  <div class="stat"><b>{res.total}</b><span>证据总数</span></div>
  <div class="stat"><b>{res.warnings}</b><span>警告</span></div>
  <div class="stat"><b>{res.flagged}</b><span>涉及证据</span></div>
  <div class="stat"><b>{n_ok}</b><span>没有问题</span></div>
</div>
<div class="filters">
  <button class="on" data-k="all">全部</button>
  <button data-k="warn">只看警告</button>
  <button data-k="info">只看建议</button>
  <button data-k="ok">只看正常</button>
</div>
{cards}
<div class="foot">
  改完直接编辑 <code>data/evidence/*.yaml</code>，或跑 <code>kb build --force</code> 重抽。<br>
  这里只做检测不做修改——改哪个字段、改成什么，只有你自己知道。
</div>
</div><script>{_JS}</script></body></html>"""
