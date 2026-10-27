"""套磁信生成（保研线专用）。

与简历的关键差异：简历只陈述事实，套磁信要「建立连接」——说明为什么是这个导师、
为什么是我。这给了模型更大的发挥空间，也就更容易编造。

三条硬约束：
1. 不得假装读过导师的具体论文。资料里没有论文标题，就只能谈研究方向。
2. 不得编造自己的经历，只能引用给定的证据。
3. 不得用「久仰大名」「不胜荣幸」这类过度客套，导师一眼能看出是模板。
"""

from __future__ import annotations

import re
from typing import Any

from ..llm import DeepSeek, get_llm
from ..models import CoverLetter, Evidence, Job, MatchReport, Profile

SYSTEM = """你是保研套磁邮件的撰写者。为一封发给目标导师的中文邮件写主题和正文。

铁律：
1. 绝对禁止编造导师的论文标题、论文内容、实验室情况、招生名额。
   如果给定信息里没有出现具体论文标题，就只谈研究方向，不要写「拜读了您的论文《XXX》」。
2. 只能使用给定的学生经历，不得编造项目、奖项、技能、成绩。
3. 语气谦逊、具体、克制。禁止「久仰大名」「不胜荣幸」「非常崇拜」这类空话。
4. 正文 350-600 字，分 3-4 段，不要小标题，不要 Markdown 符号。
5. 必须包含：称呼、自我介绍、对该方向的兴趣、与方向相关的自身经历、附件说明、明确的请求。
6. 附件只能提「个人简历」这一项。禁止虚构成绩单、推荐信、课题报告等未实际提供的附件。
7. 提到导师方向时，挑 1-2 个与你经历最相关的点展开，不要整段罗列方向名称。
8. 只输出 JSON。"""

USER_TMPL = """## 导师信息
姓名：{mentor}
单位：{org}
研究方向：{topics}

导师资料原文（这是你唯一可以引用的导师信息，没写到的内容不许编）：
{raw}

## 我的基本信息
{profile}

## 我的相关经历（只能引用这些，括号内是引用编号）
{evidences}

## 输出格式
{{
  "subject": "邮件主题，≤30字，格式如：保研咨询-某某大学-张三-计算机科学与技术",
  "body": "邮件正文，350-600字，以「{salutation}」开头，以「学生：姓名」落款",
  "evidence_ids": ["正文中实际引用到的证据 id"]
}}"""

TITLES = ("老师", "教授", "副教授", "研究员", "副研究员", "院士")


def _salutation(mentor: str) -> str:
    """mentor 可能已经是「张老师」，不要再叠一层。"""
    m = (mentor or "").strip()
    if not m:
        return "尊敬的老师："
    if m.endswith(TITLES):
        return f"尊敬的{m}："
    return f"尊敬的{m}老师："


def _profile_brief(profile: Profile) -> str:
    lines = [f"姓名：{profile.name}"]
    for edu in profile.education:
        lines.append(
            f"{edu.school} {edu.major} {edu.degree}，{edu.period}"
            + (f"，GPA {edu.gpa}" if edu.gpa else "")
            + (f"，排名 {edu.gpa_rank}" if edu.gpa_rank else "")
        )
    skills = "、".join(profile.skills.all_tags()[:10])
    if skills:
        lines.append(f"技能：{skills}")
    return "\n".join(lines)


def _evidence_block(evs: list[Evidence]) -> str:
    if not evs:
        return "（无）"
    out = []
    for e in evs:
        metrics = "，".join(f"{m.name}{m.rendered()}" for m in e.metrics[:3])
        out.append(
            f"[{e.id}] {e.title}（{e.role or '参与'}，{e.period or '时间未填'}）\n"
            f"    {e.claim}\n"
            + (f"    指标：{metrics}\n" if metrics else "")
        )
    return "".join(out)


def _paper_refs(text: str) -> list[str]:
    return re.findall(r"《([^》]{2,80})》", text or "")


def _fix_salutation(text: str) -> str:
    """兜底修正「张老师老师」这类重复称呼。"""
    for bad, good in (
        ("老师老师", "老师"),
        ("教授老师", "教授"),
        ("副教授老师", "副教授"),
        ("研究员老师", "研究员"),
    ):
        text = text.replace(bad, good)
    return text


def _check(letter: CoverLetter, job: Job, evs: list[Evidence], profile: Profile) -> list[str]:
    warns: list[str] = []
    body = letter.body
    n = letter.word_count()

    if not letter.subject.strip():
        warns.append("[结构] 缺少邮件主题")
    elif len(letter.subject) > 40:
        warns.append(f"[篇幅] 主题过长（{len(letter.subject)} 字），建议 ≤30 字")

    if n < 250:
        warns.append(f"[篇幅] 正文仅 {n} 字，偏短，建议 350-600 字")
    elif n > 700:
        warns.append(f"[篇幅] 正文 {n} 字，偏长，导师可能没耐心读完")

    # 附件虚构：系统只产出简历，提到其他附件就是在承诺不存在的东西
    for word in ("成绩单", "推荐信", "获奖证书", "课题报告", "作品集", "论文全文"):
        if word in body:
            warns.append(f"[编造风险] 正文提到附件「{word}」，但系统只生成简历，请确认该附件真实存在")

    if not re.search(r"(老师|教授)[：:,，]", body[:60]):
        warns.append("[格式] 开头缺少称呼（应为「尊敬的X老师：」）")
    if profile.name and profile.name not in body:
        warns.append("[格式] 结尾缺少落款姓名")
    if not any(k in body for k in ("简历", "附件", "附上")):
        warns.append("[内容] 未提及附件简历")

    # 关键：检查是否编造了导师论文
    raw = (job.raw_text or "") + " " + " ".join(job.mentor_topics)
    for ref in _paper_refs(body):
        if ref not in raw:
            warns.append(f"[编造风险] 正文引用了《{ref}》，但导师资料里没有这个标题，疑似虚构")
    if "论文" in body and "《" not in body and not any(k in raw for k in ("论文", "发表")):
        warns.append("[编造风险] 正文提到「论文」，但导师资料里没有论文信息")

    # 检查引用的证据是否真实存在
    ids = {e.id for e in evs}
    for eid in letter.evidence_ids:
        if eid not in ids:
            warns.append(f"[结构] evidence_ids 里的「{eid}」不在本次候选证据中")

    return warns


def generate(
    job: Job,
    profile: Profile,
    report: MatchReport,
    evidences: list[Evidence],
    llm: DeepSeek | None = None,
    max_retry: int = 1,
) -> CoverLetter:
    llm = llm or get_llm()
    by_id = {e.id: e for e in evidences}
    picked = [by_id[h.evidence_id] for h in report.hits if h.evidence_id in by_id][:3]

    mentor = job.mentor or "老师"
    prompt = USER_TMPL.format(
        mentor=mentor,
        salutation=_salutation(job.mentor),
        org=job.org or "（未填）",
        topics="、".join(job.mentor_topics) or "（未解析出方向）",
        raw=(job.raw_text or "")[:3000],
        profile=_profile_brief(profile),
        evidences=_evidence_block(picked),
    )

    mock = {
        "subject": f"保研咨询-{job.org}-{profile.name}",
        "body": (
            f"尊敬的{mentor}老师：\n\n您好！我是{profile.name}，"
            f"{profile.education[0].school if profile.education else ''}计算机科学与技术专业本科生。\n\n"
            f"我对您的研究方向很感兴趣，希望有机会加入课题组。\n\n"
            f"随信附上我的简历，盼复。\n\n学生：{profile.name}"
        ),
        "evidence_ids": [e.id for e in picked],
    }

    letter: CoverLetter | None = None
    warns: list[str] = []
    for attempt in range(max_retry + 1):
        data: dict[str, Any] = llm.chat_json(SYSTEM, prompt, mock_payload=mock, max_tokens=2000)
        letter = CoverLetter(
            job_id=job.id,
            subject=_fix_salutation(str(data.get("subject", "")).strip()),
            body=_fix_salutation(str(data.get("body", "")).strip()),
            evidence_ids=[str(x).strip() for x in (data.get("evidence_ids") or []) if str(x).strip()],
        )
        warns = _check(letter, job, picked, profile)
        # 只对编造类问题重试，格式问题不重试（重试也改不好）
        if not any("编造风险" in w for w in warns):
            break
        if attempt < max_retry:
            prompt += "\n\n## 上一版的问题（必须修正）\n" + "\n".join(w for w in warns if "编造风险" in w)

    assert letter is not None
    letter.warnings = warns
    return letter


def to_markdown(letter: CoverLetter, job: Job) -> str:
    return (
        f"# 套磁信 · {job.mentor or job.org}\n\n"
        f"> 目标：{job.title} @ {job.org}\n"
        f"> 研究方向：{'、'.join(job.mentor_topics) or '未填'}\n"
        f"> 生成时间：{letter.generated_at}\n"
        f"> 字数：{letter.word_count()}\n\n"
        f"## 主题\n\n{letter.subject}\n\n"
        f"## 正文\n\n{letter.body}\n"
    )
