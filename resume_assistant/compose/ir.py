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
6. 每条 bullet 必须覆盖不同的动作或结果。禁止用不同措辞复述同一件事，
   例如「为 X 补充测试用例，覆盖率 78% 升至 84%」和「向 X 提交测试，覆盖率 78%→84%」
   是重复的，只能保留一条。素材不够就少写，宁可 1 条也不要凑数。
7. 只输出 JSON。"""

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


def _nums(text: str) -> set[str]:
    import re

    return set(re.findall(r"\d+(?:\.\d+)?", text))


def _similar(a: str, b: str) -> float:
    from difflib import SequenceMatcher

    return SequenceMatcher(None, a, b).ratio()


def _info_score(b: Bullet) -> float:
    """信息量：数字个数权重高，长度次之。"""
    return len(_nums(b.text)) * 2.0 + len(b.text) / 50.0


def _dedupe_bullets(bullets: list[Bullet]) -> list[Bullet]:
    """去掉「换种说法复述同一件事」的 bullet，重复时保留信息量更大的那条。

    LLM 在要求输出 N 条时，信息不足就会把同一件事拆成多条。
    这是简历的硬伤，必须在代码层拦，不能只靠 prompt。
    """
    kept: list[Bullet] = []
    for b in bullets:
        text = b.text.strip()
        if not text:
            continue
        replaced = False
        for i, k in enumerate(kept):
            is_dup = _similar(text, k.text) >= 0.5
            if not is_dup:
                # 数字高度重叠 + 文本有一定相似度，也判为重复
                nb, nk = _nums(text), _nums(k.text)
                is_dup = bool(nb and nk) and len(nb & nk) / max(len(nb), len(nk)) >= 0.75 and _similar(text, k.text) >= 0.3
            if is_dup:
                if _info_score(b) > _info_score(k):
                    kept[i] = b
                replaced = True
                break
        if not replaced:
            kept.append(b)
    return kept


def _dedupe_evidence(evs: list[Evidence]) -> list[Evidence]:
    """同一个项目被抽出两条时只留一条。

    真实资料里「XX 备份」「XX (旧)」这类文件夹很常见，两个文件夹会抽出两条
    同标题的证据，措辞略有不同（LLM 两次抽取不会一字不差）。简历上同一个项目
    出现两遍，比少一个项目难解释得多——而 review 只会提醒，挡不住已经生成的版本。

    判重不看 id：标题归一化后相同，且「时间段相同」或「claim 相似度 ≥ 0.5」即算同一条。
    """
    import re as _re

    def norm(s: str) -> str:
        return _re.sub(r"\s+", "", s or "")

    def info(e: Evidence) -> float:
        return len(e.metrics) * 2 + len(e.claim or "") / 50.0

    out: list[Evidence] = []
    for e in evs:
        dup_at = None
        for i, k in enumerate(out):
            if norm(e.title) != norm(k.title):
                continue
            same_period = bool((e.period or "").strip()) and norm(e.period) == norm(k.period)
            if same_period or _similar(e.claim or "", k.claim or "") >= 0.5:
                dup_at = i
                break
        if dup_at is None:
            out.append(e)
        elif info(e) > info(out[dup_at]):
            out[dup_at] = e
    return out


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


INTEREST_SYSTEM = """你是保研套磁简历的研究兴趣撰写者。你的任务是写一段 2 句话的研究兴趣陈述。

铁律：
1. 必须围绕给定的导师研究方向来写，不能跑题到学生自己的其他方向。
2. 只能引用给定的经历，不得编造任何项目、技术或成果。
3. 不用「致力于」「热衷于」「浓厚的兴趣」这类空话，写具体的点。
4. 总长不超过 110 字，写成完整的两句话，结尾加句号。
5. 只输出 JSON。"""

INTEREST_TMPL = """## 导师研究方向
{topics}

## 该生与该方向相关的经历
{evs}

## 该生的自我描述（仅作背景参考，不要照抄）
{summary}

## 输出格式
{{"text": "两句话的研究兴趣陈述，≤110字，结尾加句号"}}"""


def _interest_section(
    profile: Profile,
    job: Job,
    spec_title: str,
    top_evs: list[Evidence],
    llm: DeepSeek,
) -> IRSection | None:
    topics = job.mentor_topics or job.keywords[:4]
    if not topics:
        return None

    ev_lines = "\n".join(f"- {e.title}：{e.claim}" for e in top_evs[:3]) or "（无直接相关经历）"
    prompt = INTEREST_TMPL.format(
        topics="、".join(topics[:5]),
        evs=ev_lines,
        summary=profile.summary.strip() or "（无）",
    )
    mock = {"text": f"研究兴趣集中于{'、'.join(topics[:4])}，希望结合已有工程经验继续深入"}
    try:
        data = llm.chat_json(INTEREST_SYSTEM, prompt, mock_payload=mock, max_tokens=400)
    except Exception:  # noqa: BLE001
        data = mock

    text = str(data.get("text", "")).strip()
    if len(text) > 130:
        text = text[:130].rstrip("，、。") + "。"
    elif text and not text.endswith(("。", ".", "！", "？")):
        text += "。"
    if not text:
        return None
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
            top_evs = [by_id[h.evidence_id] for h in report.hits if h.evidence_id in by_id][:3]
            sec = _interest_section(profile, job, spec.title, top_evs, llm)
        elif spec.type in {"projects", "experience"}:
            sec = None
            pool = [
                by_id[h.evidence_id]
                for h in report.hits
                if h.evidence_id in by_id and h.evidence_id not in used_ids
            ]
            # 备份文件夹抽出来的孪生证据先并掉，否则「项目经历」里同一个项目占两格
            pool = _dedupe_evidence(pool)
            if spec.include_kinds is not None:
                filtered = [e for e in pool if e.kind in spec.include_kinds]
                # experience 语义严格：没有对应类型的经历就整段不出现，
                # 否则会出现「科研与实习经历」里全是课程项目这种虚假陈述
                chosen = filtered if spec.type == "experience" else (filtered or pool)
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
                    bl = _dedupe_bullets(bl)
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
