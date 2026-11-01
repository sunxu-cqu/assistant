"""保研套磁 PPT 的组装：把证据装成「一页讲一件事」的分页结构。

和简历组稿（`compose/ir.py`）的区别：

- 简历是**压缩**：3 个项目挤一页纸，每条 bullet ≤45 字，靠 LLM 改写措辞
- PPT 是**展开**：一个项目一页，讲背景 / 做了什么 / 结果，靠证据的 star 结构直接拼

所以这里**不调 LLM**。理由有两个：

1. PPT 一页能放 300 字，压缩不再是问题，LLM 唯一的价值（把长素材压短）用不上；
2. 展开比压缩更容易编——把「做了 A」写成「做了 A 并显著提升 B」只需要一句顺滑的过渡。
   直接拼 star 和 metrics，数字天然过了反幻觉闸门。

代价是文字不如 LLM 润色得漂亮。要润色的话，应该加在「讲稿备注」上，
而不是动正文的事实。
"""

from __future__ import annotations

from pathlib import Path

import yaml

from ..config import DECK_TEMPLATE_DIR
from ..models import DeckIR, DeckSlide, Evidence, Job, MatchReport, Profile
from .ir import _collapse_same_project, _dedupe_evidence, _fmt_period


# ------------------------------------------------------------------ 模板

class DeckSlideSpec:
    def __init__(self, raw: dict) -> None:
        self.type: str = raw.get("type", "")
        self.title: str = raw.get("title", "")
        self.subtitle: str = raw.get("subtitle", "")
        self.max_entries: int = int(raw.get("max_entries", 3))
        self.bullets_per_entry: int = int(raw.get("bullets_per_entry", 3))
        self.metrics_per_entry: int = int(raw.get("metrics_per_entry", 3))
        self.max_items: int = int(raw.get("max_items", 6))
        self.max_points: int = int(raw.get("max_points", 3))
        self.optional: bool = bool(raw.get("optional", False))


class DeckTemplate:
    def __init__(self, key: str, raw: dict) -> None:
        self.key = key
        self.name: str = raw.get("name", key)
        self.description: str = raw.get("description", "")
        self.page: dict = raw.get("page", {})
        self.slides: list[DeckSlideSpec] = [DeckSlideSpec(s) for s in raw.get("slides", [])]


def load_deck_template(key: str = "baoyan_academic") -> DeckTemplate:
    path: Path = DECK_TEMPLATE_DIR / f"{key}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"PPT 模板不存在：{path}")
    return DeckTemplate(key, yaml.safe_load(path.read_text(encoding="utf-8")) or {})


def list_deck_templates() -> list[dict[str, str]]:
    out = []
    for p in sorted(DECK_TEMPLATE_DIR.glob("*.yaml")):
        try:
            raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        except Exception:  # noqa: BLE001
            continue
        out.append({
            "key": p.stem,
            "name": raw.get("name", p.stem),
            "description": raw.get("description", ""),
        })
    return out


# ------------------------------------------------------------------ 各页组装

def _cover(profile: Profile, job: Job, spec: DeckSlideSpec) -> DeckSlide:
    edu = profile.education[0] if profile.education else None
    school_line = " · ".join(x for x in (
        edu.school if edu else "", edu.major if edu else "", edu.degree if edu else "",
    ) if x)

    # 「申请方向」优先用导师方向，其次岗位名。都没有就不写，不编一个出来
    topic = (job.mentor_topics[0] if job.mentor_topics else "") or job.title
    target = ""
    if topic:
        who = job.mentor or job.org
        target = f"申请方向：{topic}" + (f"（{who}）" if who else "")

    return DeckSlide(
        type="cover",
        title=profile.name or "未填写姓名",
        subtitle=spec.subtitle or school_line,
        meta=target,
        footer=profile.email or profile.phone,
    )


def _profile_slide(profile: Profile, spec: DeckSlideSpec) -> DeckSlide | None:
    items: list[str] = []
    edu = profile.education[0] if profile.education else None
    if edu:
        line = " · ".join(x for x in (edu.school, edu.major, edu.degree) if x)
        if edu.period:
            line += f"（{_fmt_period(edu.period)}）"
        if line:
            items.append(line)
        # 绩点和排名分开写：排名百分比是加分项，但没写就是没写，不换算
        score = []
        if edu.gpa:
            score.append(f"GPA {edu.gpa}")
        if edu.gpa_rank:
            score.append(f"专业排名 {edu.gpa_rank}")
        if edu.gpa_rank_pct:
            score.append(f"前 {edu.gpa_rank_pct}")
        if score:
            items.append(" · ".join(score))

    sk = profile.skills
    if sk.english:
        items.append(f"英语：{sk.english}")
    langs = "、".join(sk.languages)
    frames = "、".join(sk.frameworks)
    tools = "、".join(sk.tools)
    parts = []
    if langs:
        parts.append(f"语言 {langs}")
    if frames:
        parts.append(f"框架 {frames}")
    if tools:
        parts.append(f"工具 {tools}")
    if parts:
        items.append(" · ".join(parts))
    if sk.others:
        items.append("、".join(sk.others[:4]))

    if edu and edu.honors:
        items.append("荣誉：" + "、".join(edu.honors[:3]))

    if not items:
        return None
    return DeckSlide(
        type="profile",
        title=spec.title or "个人概况",
        bullets=items[: spec.max_items],
    )


def _project_slide(ev: Evidence, spec: DeckSlideSpec) -> DeckSlide:
    """一个项目一页：背景 → 做了什么 → 结果。

    顺序是刻意的。面试官听项目时脑子里问的是「为什么做 / 你怎么做的 / 做成了什么」，
    把结果放最前面反而像在吹。
    """
    bullets: list[str] = []
    if ev.star.situation:
        bullets.append(f"背景：{ev.star.situation}")

    # 给「结果」留一格，别让动作把版面占满
    room = spec.bullets_per_entry - len(bullets) - (1 if ev.star.result else 0)
    for act in ev.star.action[: max(0, room)]:
        bullets.append(act)
    if ev.star.result:
        bullets.append(f"结果：{ev.star.result}")

    if not bullets:
        # star 没抽全时退回 claim，总比空白页强
        bullets = [ev.claim] if ev.claim else []

    metrics = [f"{m.rendered()}|{m.name}" for m in ev.metrics[: spec.metrics_per_entry]]

    sub = " · ".join(x for x in (ev.role, ev.org, _fmt_period(ev.period)) if x)
    return DeckSlide(
        type="project",
        title=ev.title,
        subtitle=sub,
        bullets=bullets,
        metrics=metrics,
        notes=ev.claim,
        evidence_id=ev.id,
    )


def _why_slide(profile: Profile, job: Job, pool: list[Evidence], spec: DeckSlideSpec) -> DeckSlide | None:
    """为什么选这个方向。

    这页最容易写成空话（「贵组方向与我的兴趣高度契合」），所以只放三种**可核对**的事实：
    导师写在 JD 里的方向、我做过哪些相关的项目、两边重合的关键词。
    一个都凑不出来就整页不出——空着比编一句强。
    """
    points: list[str] = []

    if job.mentor_topics:
        points.append("导师方向：" + "、".join(job.mentor_topics[:4]))

    if pool:
        titles = "、".join(e.title for e in pool[:3])
        points.append(f"我做过的相关工作：{titles}")

    if job.mentor_topics and pool:
        topics = {t.lower() for t in job.mentor_topics}
        mine: set[str] = set()
        for ev in pool:
            mine |= {t.lower() for t in (*ev.domain_tags, *ev.skill_tags)}
        overlap = sorted(topics & mine)
        if overlap:
            points.append("重合点：" + "、".join(overlap[:5]))

    if not points:
        return None
    # 只有一条「我做过的相关工作」撑不起「为什么选这个方向」这个标题——
    # 一页只放一行字比不出这页更难看。导师方向没填时就是这样，宁可不生成。
    if len(points) < 2:
        return None
    return DeckSlide(
        type="why",
        title=spec.title or "为什么选择这个方向",
        bullets=points[: spec.max_points],
    )


def _plan_slide(profile: Profile, spec: DeckSlideSpec) -> DeckSlide | None:
    """研究设想。资料里没有就整页不出。

    profile.summary 是本人写过的自我陈述，拆成句子正好当要点——
    这是「已有事实的重新排版」，不是生成。
    """
    text = (profile.summary or "").strip()
    if not text:
        return None
    import re

    parts = [s.strip() for s in re.split(r"[。；;]\s*", text) if s.strip()]
    if not parts:
        return None
    return DeckSlide(
        type="plan",
        title=spec.title or "研究设想",
        bullets=parts[: spec.max_points],
    )


def _end(profile: Profile, job: Job, spec: DeckSlideSpec) -> DeckSlide:
    contact = " · ".join(x for x in (profile.email, profile.phone) if x)
    return DeckSlide(
        type="end",
        title=spec.title or "谢谢",
        meta=contact,
        subtitle="期待有机会进一步交流",
    )


# ------------------------------------------------------------------ 主入口

def build_deck(
    profile: Profile,
    job: Job,
    report: MatchReport,
    evidences: list[Evidence],
    template: DeckTemplate,
) -> DeckIR:
    by_id = {e.id: e for e in evidences}
    pool = [by_id[h.evidence_id] for h in report.hits if h.evidence_id in by_id]
    # 和简历同一套去重：备份文件夹的孪生证据、同一项目的总述+分述
    pool = _collapse_same_project(_dedupe_evidence(pool))

    slides: list[DeckSlide] = []
    cursor = 0  # 多个 projects section 时不重复挑同一批项目
    for spec in template.slides:
        if spec.type == "cover":
            slides.append(_cover(profile, job, spec))
        elif spec.type == "profile":
            s = _profile_slide(profile, spec)
            if s:
                slides.append(s)
        elif spec.type == "projects":
            for ev in pool[cursor : cursor + spec.max_entries]:
                slides.append(_project_slide(ev, spec))
            cursor += spec.max_entries
        elif spec.type == "why":
            s = _why_slide(profile, job, pool, spec)
            if s:
                slides.append(s)
        elif spec.type == "plan":
            s = _plan_slide(profile, spec)
            if s:
                slides.append(s)
            elif not spec.optional:
                # 非可选却没内容，不硬凑一页空壳
                continue
        elif spec.type == "end":
            slides.append(_end(profile, job, spec))

    return DeckIR(
        job_id=job.id,
        template=template.key,
        contact={
            "name": profile.name,
            "email": profile.email,
            "phone": profile.phone,
        },
        slides=slides,
    )
