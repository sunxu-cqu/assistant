"""组稿：把选中的证据装配成 Resume IR。

原则：LLM 只做「措辞」，不做「选材」，不做「造事实」。
- 选材由 match 阶段决定（hits 顺序即优先级）
- 结构（section / entry 的标题、角色、时间）由代码确定性生成
- LLM 仅负责把 star + metrics 压成简历 bullet，且必须复用给定数字
"""

from __future__ import annotations

from typing import Any

from ..llm import DeepSeek, get_llm
from ..models import Bullet, Education, Evidence, IREntry, IRSection, Job, MatchReport, Profile, ResumeIR
from .templates import Template

SYSTEM = """你是中文简历文案压缩器。把给定的经历素材改写成简历 bullet。

铁律：
1. 每条 bullet 必须以动词开头（主导/设计/实现/优化/搭建/复现/验证…），不写「负责了」「参与了」这类虚词。
2. 数字只能来自给定的 metrics 列表，禁止新造、禁止四舍五入后变形。
3. 技术名词只能来自给定的 skill_tags / domain_tags / metrics，禁止替换成没出现过的技术。
4. 每条 ≤45 字，单行可读，不写句号。
5. 优先把与目标岗位关键词相关的那一面写在前面。
6. 只输出 JSON。"""

USER_TMPL = """## 目标岗位关键词
{keywords}

## 需要改写的经历
{items}

## 上一轮校验未通过的问题（必须逐条修正，若无则为空）
{feedback}

## 输出格式
{{
  "entries": [
    {{
      "evidence_id": "ev_xxx",
      "bullets": [
        {{"text": "动词开头的短句，≤45字", "metrics": ["用到的数字，如 3200", "0.85"]}}
      ]
    }}
  ]
}}

说明：每个 evidence_id 输出 {bullets_per_entry} 条 bullet，不足则少输出。"""


def _fmt_period(period: str) -> str:
    """2022-09 ~ 2026-06 → 2022.09 – 2026.06"""
    import re

    p = period.replace(" ~ ", " – ").replace("~", "–").strip()
    p = re.sub(r"(\d{4})[-/](\d{1,2})", r"\1.\2", p)
    return p


def _edu_section(profile: Profile, spec_title: str) -> IRSection | None:
    if not profile.education:
        return None
    entries: list[IREntry] = []
    for edu in profile.education:
        meta_parts = [_fmt_period(edu.period)] if edu.period else []
        if edu.gpa:
            meta_parts.append(f"GPA {edu.gpa}")
        if edu.gpa_rank:
            meta_parts.append(f"排名 {edu.gpa_rank}")
        heading = " | ".join(x for x in [edu.school, edu.major, edu.degree] if x)
        bullets: list[Bullet] = []
        if edu.courses:
            bullets.append(Bullet(text="核心课程：" + "、".join(edu.courses[:8]), evidence_id="profile", metrics=[]))
        if edu.honors:
            bullets.append(Bullet(text="在校荣誉：" + "、".join(edu.honors[:4]), evidence_id="profile", metrics=[]))
        entries.append(IREntry(heading=heading, meta=" | ".join(meta_parts), bullets=bullets))
    return IRSection(type="education", title=spec_title, entries=entries)


def _skills_section(profile: Profile, spec_title: str) -> IRSection | None:
    s = profile.skills
    rows: list[str] = []
    if s.languages:
        rows.append("编程语言：" + "、".join(s.languages))
    if s.frameworks:
        rows.append("框架/库：" + "、".join(s.frameworks))
    if s.tools:
        rows.append("工具/环境：" + "、".join(s.tools))
    if s.others:
        rows.append("其他：" + "、".join(s.others))
    if s.english:
        rows.append("英语：" + s.english)
    if not rows:
        return None
    bullets = [Bullet(text=r, evidence_id="profile", metrics=[]) for r in rows]
    return IRSection(type="skills", title=spec_title, entries=[IREntry(heading="", meta="", bullets=bullets)])


def _honors_section(profile: Profile, spec_title: str) -> IRSection | None:
    honors: list[str] = []
    for edu in profile.education:
        honors.extend(edu.honors)
    if not honors:
        return None
    return IRSection(
        type="honors",
        title=spec_title,
        entries=[IREntry(heading="", meta="", bullets=[Bullet(text=h, evidence_id="profile", metrics=[]) for h in honors])],
    )


def _interest_section(profile: Profile, job: Job, spec_title: str) -> IRSection | None:
    topics = job.mentor_topics or job.keywords[:4]
    if not topics:
        return None
    body = profile.summary.strip() or "对上述方向有持续的学习与工程实践积累"
    text = f"研究兴趣集中于{'、'.join(topics[:4])}。{body}"
    return IRSection(
        type="research_interest",
        title=spec_title,
        entries=[IREntry(heading="", meta="", bullets=[Bullet(text=text, evidence_id="profile", metrics=[])])],
    )


def _llm_bullets(
    job: Job,
    selected: list[Evidence],
    bullets_per_entry: int,
    llm: DeepSeek,
    feedback: str = "",
) -> dict[str, list[dict[str, Any]]]:
    if not selected:
        return {}
    items = []
    for ev in selected:
        items.append(
            {
                "evidence_id": ev.id,
                "title": ev.title,
                "role": ev.role,
                "claim": ev.claim,
                "star": {
                    "situation": ev.star.situation,
                    "task": ev.star.task,
                    "action": ev.star.action,
                    "result": ev.star.result,
                },
                "metrics": [{"name": m.name, "value": m.value, "unit": m.unit} for m in ev.metrics],
                "skill_tags": ev.skill_tags,
                "domain_tags": ev.domain_tags,
                "variants": {"short": ev.variants.short, "long": ev.variants.long, "academic": ev.variants.academic},
            }
        )
    import json

    prompt = USER_TMPL.format(
        keywords="、".join(job.keywords) or job.title,
        items=json.dumps(items, ensure_ascii=False, indent=1),
        bullets_per_entry=bullets_per_entry,
        feedback=feedback or "（无）",
    )
    mock = {
        "entries": [
            {
                "evidence_id": ev.id,
                "bullets": [{"text": (ev.claim or ev.title)[:45], "metrics": []}],
            }
            for ev in selected
        ]
    }
    data = llm.chat_json(SYSTEM, prompt, mock_payload=mock, max_tokens=3000)
    out: dict[str, list[dict[str, Any]]] = {}
    for e in data.get("entries") or []:
        if isinstance(e, dict) and e.get("evidence_id"):
            out[str(e["evidence_id"])] = [b for b in (e.get("bullets") or []) if isinstance(b, dict) and b.get("text")]
    return out


def compose(
    profile: Profile,
    job: Job,
    report: MatchReport,
    evidences: list[Evidence],
    template: Template,
    llm: DeepSeek | None = None,
    feedback: str = "",
) -> ResumeIR:
    llm = llm or get_llm()
    by_id = {e.id: e for e in evidences}

    sections: list[IRSection] = []
    used_ids: set[str] = set()
    for spec in template.sections:
        if spec.type == "education":
            sec = _edu_section(profile, spec.title)
        elif spec.type == "skills":
            sec = _skills_section(profile, spec.title)
        elif spec.type == "honors":
            sec = _honors_section(profile, spec.title)
        elif spec.type == "research_interest":
            sec = _interest_section(profile, job, spec.title)
        elif spec.type in {"projects", "experience"}:
            sec = None
            pool = [
                by_id[h.evidence_id]
                for h in report.hits
                if h.evidence_id in by_id and h.evidence_id not in used_ids
            ]
            if spec.include_kinds is not None:
                filtered = [e for e in pool if e.kind in spec.include_kinds]
                # 过滤后为空则退回全池，避免整段消失
                chosen = filtered or pool
            else:
                chosen = pool
            chosen = chosen[: spec.max_entries]
            if chosen:
                used_ids |= {e.id for e in chosen}
                bullets_map = _llm_bullets(job, chosen, spec.bullets_per_entry, llm, feedback=feedback)
                entries: list[IREntry] = []
                for ev in chosen:
                    bl = []
                    for b in bullets_map.get(ev.id, [])[: spec.bullets_per_entry]:
                        bl.append(
                            Bullet(
                                text=str(b.get("text", "")).strip(),
                                evidence_id=ev.id,
                                metrics=[str(m).strip() for m in (b.get("metrics") or []) if str(m).strip()],
                            )
                        )
                    heading = ev.title
                    meta = " | ".join(x for x in [ev.role, _fmt_period(ev.period), ev.org] if x)
                    entries.append(IREntry(heading=heading, meta=meta, bullets=bl))
                sec = IRSection(type=spec.type, title=spec.title, entries=entries)
        else:
            sec = None

        if sec and sec.entries:
            sections.append(sec)

    contact = {
        "name": profile.name,
        "phone": profile.phone,
        "email": profile.email,
        "city": profile.city,
        "homepage": profile.homepage,
        "gender": profile.gender,
        "birth": profile.birth,
        "political": profile.political,
    }
    return ResumeIR(job_id=job.id, template=template.key, contact={k: v for k, v in contact.items() if v}, sections=sections)
