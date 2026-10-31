"""实习投递开场白（实习线专用）。

保研有套磁信，实习却没有对称的东西——投递时真正要写的是站内信那两句话，
或者邮件正文那两百字。这件事看着比简历简单，其实最容易出问题：

- 站内信有硬长度限制（Boss 直聘那类一屏就截断），写完发现放不下
- 「每周 4 天、可实习 6 个月」这种话，模型最爱顺手编，而这是面试第一句
  就会被追问的内容
- 空话密度最高：「贵司平台广阔，非常期待加入」删掉之后一个字信息都没有

所以一次生成两版（short ≤80 字 / full ≤220 字），并且所有数字都要能溯源。
"""

from __future__ import annotations

import re
from typing import Any

from ..llm import DeepSeek, get_llm
from ..models import Evidence, Job, MatchReport, Pitch, Profile

SYSTEM = """你在帮一个学生写实习投递的开场白，用于站内信和邮件正文。

铁律：
1. 只能使用给定材料里的经历、项目、数字，禁止编造任何项目、奖项、技能、成绩、公司背景。
2. 禁止空话：「贵司平台广阔」「非常期待加入」「希望能有机会学习」这类句子一律不要。
   每句话都要有具体内容，删掉之后信息量下降才算合格。
3. 不要复述 JD 原文，不要罗列技能清单。
4. 输出两个版本：
   - short：站内信 / 打招呼用，不超过 80 字，一句话说清「我是谁 + 我最对口的那个项目（带一个数字）」
   - full：邮件正文用，不超过 220 字，3 段：自我介绍 / 最对口的 1-2 段经历（带数字）/ 可实习时间与联系方式
5. 可实习时间只能来自给定信息。没给就整句不写，绝对不许编「每周 X 天」「可实习 X 个月」。
6. 可以写「简历见附件」，但不要承诺成绩单、推荐信、作品集这类没有提供的材料。
7. 不要 Markdown 符号，不要小标题。只输出 JSON。"""

USER_TMPL = """## 目标岗位
公司：{org}
岗位：{title}
地点：{location}
JD 关键词：{keywords}
JD 要求（摘录）：
{requirements}

JD 原文（仅供理解岗位，不要照抄）：
{raw}

## 我的基本信息
{profile}

可实习时间：{available}

## 我最对口的经历（只能引用这些，括号内是引用编号）
{evidences}

## 输出格式
{{
  "short": "站内信版本，≤80 字，不要换行",
  "full": "邮件正文版本，≤220 字，用 \\n\\n 分段，结尾落款「{name}」",
  "evidence_ids": ["实际引用到的证据 id"]
}}"""

# 编造可实习时间的典型说法
_AVAIL_PAT = re.compile(r"(每周\s*\d+\s*天|可实习\s*\d+\s*个?月|实习\s*\d+\s*个?月|随时到岗|即刻到岗|\d+\s*天/周)")

# 空话：留着只会让人觉得是群发模板
# 「期待您的回复」不算——那是中文邮件的常规收尾，不是吹捧
_EMPTY_WORDS = (
    "贵司平台",
    "平台广阔",
    "非常期待",
    "不胜荣幸",
    "久仰",
    "希望有机会学习",
)

# 数字溯源：排除 V100 / ResNet-50 这类型号里的数字
_NUM_RE = re.compile(r"(?<![A-Za-z0-9])\d+(?:\.\d+)?")


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
    if profile.city:
        lines.append(f"现居：{profile.city}")
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
            + (f"    技能：{'、'.join(e.skill_tags[:8])}\n" if e.skill_tags else "")
        )
    return "".join(out)


def _allowed_numbers(evs: list[Evidence], profile: Profile, job: Job) -> set[str]:
    """允许出现的数字：来自资料、指标、JD 原文。

    判「编造」只看数字在原始材料里有没有出现过——出现过的数字被换了个说法
    写进开场白是正常的，没出现过的才是编的。
    """
    pool: list[str] = [job.raw_text or "", job.title, " ".join(job.mentor_topics)]
    for e in evs:
        pool += [e.title, e.claim, e.raw_excerpt, e.period]
        pool += list(e.raw_numbers)
        pool += [m.value for m in e.metrics]
        pool += [m.rendered() for m in e.metrics]
    for edu in profile.education:
        pool += [edu.period, edu.gpa, edu.gpa_rank, edu.gpa_rank_pct]
        pool += list(edu.courses) + list(edu.honors)
    # 联系方式里的数字（手机号）当然不算编造
    pool += [profile.phone, profile.email, profile.homepage]
    pool += [profile.skills.english, profile.summary, profile.internship_available]

    allowed: set[str] = set()
    for chunk in pool:
        allowed.update(_NUM_RE.findall(str(chunk or "")))
    return allowed


def _contact_line(profile: Profile) -> str:
    bits = [x for x in (profile.phone, profile.email, profile.homepage) if x]
    return "　".join(bits)


def _check(
    pitch: Pitch,
    job: Job,
    evs: list[Evidence],
    profile: Profile,
) -> list[str]:
    warns: list[str] = []
    cc = pitch.char_count()

    if not pitch.short.strip():
        warns.append("[结构] 站内信版本为空")
    elif cc["short"] > 90:
        warns.append(f"[篇幅] 站内信 {cc['short']} 字，站内信一屏就截断，压到 80 字内")

    if not pitch.full.strip():
        warns.append("[结构] 邮件正文为空")
    elif cc["full"] < 80:
        warns.append(f"[篇幅] 邮件正文仅 {cc['full']} 字，偏短，建议 150-220 字")
    elif cc["full"] > 260:
        warns.append(f"[篇幅] 邮件正文 {cc['full']} 字，偏长，HR 不会读完，压到 220 字内")

    both = f"{pitch.short}\n{pitch.full}"

    # 「简历见附件」是实话，不拦；拦的是承诺没提供的材料
    for word in ("成绩单", "推荐信", "获奖证书", "作品集"):
        if word in both:
            warns.append(f"[编造风险] 提到「{word}」，但系统只产出简历，你未必会真的发这份材料")

    hits = [w for w in _EMPTY_WORDS if w in both]
    if hits:
        warns.append(f"[空话] 出现「{'」「'.join(hits)}」，删掉这些句子信息量不变")

    # 可实习时间：profile 没填就不许出现
    if not (profile.internship_available or "").strip():
        m = _AVAIL_PAT.search(both)
        if m:
            warns.append(
                f"[编造风险] 提到「{m.group(0)}」，但 profile.yaml 的 internship_available 是空的，"
                "请先填真实的可实习时间"
            )
        else:
            # 不编是对的，但整句不写也有代价：实习 HR 第一句就问「能来几天、几个月」。
            # 这条提示只在没填的时候出现一次，提醒的是档案缺项，不是文案问题
            warns.append(
                "[档案] profile.yaml 的 internship_available 是空的，正文里写不出可实习时间；"
                "这是实习 HR 最先问的一句，建议先填（如「2026.01 起，每周 4 天，可实习 6 个月」）"
            )

    # 数字溯源
    allowed = _allowed_numbers(evs, profile, job)
    seen: list[str] = []
    for n in _NUM_RE.findall(both):
        if n not in allowed and n not in seen:
            seen.append(n)
    if seen:
        warns.append(f"[编造风险] 数字 {('、'.join(seen[:6]))} 在资料里找不到出处")

    ids = {e.id for e in evs}
    for eid in pitch.evidence_ids:
        if eid not in ids:
            warns.append(f"[结构] evidence_ids 里的「{eid}」不在本次候选证据中")

    if profile.name and profile.name not in pitch.full:
        warns.append("[格式] 邮件正文结尾缺少落款姓名")
    if not _contact_line(profile):
        warns.append("[格式] profile.yaml 里没有手机号 / 邮箱，HR 无法联系你")

    return warns


def generate(
    job: Job,
    profile: Profile,
    report: MatchReport,
    evidences: list[Evidence],
    llm: DeepSeek | None = None,
    max_retry: int = 1,
) -> Pitch:
    llm = llm or get_llm()
    by_id = {e.id: e for e in evidences}
    picked = [by_id[h.evidence_id] for h in report.hits if h.evidence_id in by_id][:3]

    reqs = "\n".join(f"- {r.text}" for r in job.requirements[:8]) or "（未解析出结构化要求）"
    available = (profile.internship_available or "").strip() or "（未填写，整句不要写）"

    prompt = USER_TMPL.format(
        org=job.org or "（未填）",
        title=job.title,
        location=job.location or "（未填）",
        keywords="、".join(job.keywords[:12]) or "（未解析）",
        requirements=reqs,
        raw=(job.raw_text or "")[:2500],
        profile=_profile_brief(profile),
        available=available,
        evidences=_evidence_block(picked),
        name=profile.name,
    )

    top = picked[0] if picked else None
    mock = {
        "short": (
            f"{profile.name}，{profile.education[0].school if profile.education else ''}"
            f"{profile.education[0].major if profile.education else ''}，"
            + (f"做过{top.title}。" if top else "有相关项目经验。")
        ),
        "full": (
            f"您好，我是{profile.name}，"
            f"{profile.education[0].school if profile.education else ''}"
            f"{profile.education[0].major if profile.education else ''}专业"
            f"{profile.education[0].degree if profile.education else ''}。\n\n"
            + (f"我做过{top.title}：{top.claim}\n\n" if top else "")
            + f"{profile.name}　{_contact_line(profile)}"
        ),
        "evidence_ids": [e.id for e in picked],
    }

    pitch: Pitch | None = None
    warns: list[str] = []
    for attempt in range(max_retry + 1):
        data: dict[str, Any] = llm.chat_json(SYSTEM, prompt, mock_payload=mock, max_tokens=1200)
        pitch = Pitch(
            job_id=job.id,
            short=str(data.get("short", "")).strip(),
            full=str(data.get("full", "")).strip(),
            evidence_ids=[str(x).strip() for x in (data.get("evidence_ids") or []) if str(x).strip()],
        )
        warns = _check(pitch, job, picked, profile)
        # 编造和超长都值得重写一次；空话与格式问题重试也改不好
        retryable = [w for w in warns if "编造风险" in w or "篇幅" in w]
        if not retryable:
            break
        if attempt < max_retry:
            prompt += "\n\n## 上一版的问题（必须修正）\n" + "\n".join(retryable)

    assert pitch is not None
    pitch.warnings = warns
    return pitch


def to_markdown(pitch: Pitch, job: Job) -> str:
    cc = pitch.char_count()
    body = (
        f"# 投递开场白 · {job.title} @ {job.org}\n\n"
        f"> 岗位：{job.title}（{job.kind.value}）\n"
        f"> 生成时间：{pitch.generated_at}\n"
        f"> 字数：站内信 {cc['short']} / 邮件 {cc['full']}\n\n"
        f"## 站内信版本（≤80 字）\n\n{pitch.short}\n\n"
        f"## 邮件正文版本（≤220 字）\n\n{pitch.full}\n"
    )
    if pitch.warnings:
        body += "\n## 需要确认\n\n" + "\n".join(f"- {w}" for w in pitch.warnings) + "\n"
    return body
