"""硬性资格校验：届别、学历、专业。

为什么单独做：技术匹配做得再好，资格不符也是白投。
这类条件有明确的判定标准（毕业年份、学历层次），适合用规则而非模型判断。
模型只看技术对口度，不会主动发现「2027 届」和「2026 年毕业」的冲突。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..models import Job, Profile

LEVELS = {"专科": 1, "本科": 2, "硕士": 3, "研究生": 3, "博士": 4}

GRAD_YEAR_PATTERNS = [
    re.compile(r"(20\d{2})\s*届"),
    re.compile(r"(20\d{2})\s*年\s*(?:毕业|应届)"),
    re.compile(r"毕业(?:时间)?\s*[:：]?\s*(20\d{2})"),
]

DEGREE_PATTERN = re.compile(r"(专科|本科|硕士|研究生|博士)(?:学历|学位)?\s*(?:及|或)?\s*以上")
DEGREE_EXACT = re.compile(r"(?:要求|限)\s*(专科|本科|硕士|研究生|博士)")

MAJOR_PATTERN = re.compile(r"(计算机|软件工程|数学|统计学|统计|电子|通信|自动化|人工智能|信息安全)")


@dataclass
class EligibilityIssue:
    level: str  # fail / warn / info
    requirement: str
    detail: str
    source: str = ""

    def to_dict(self) -> dict:
        return {"level": self.level, "requirement": self.requirement, "detail": self.detail}


@dataclass
class EligibilityResult:
    issues: list[EligibilityIssue] = field(default_factory=list)

    @property
    def blocked(self) -> bool:
        return any(i.level == "fail" for i in self.issues)

    def to_list(self) -> list[dict]:
        return [i.to_dict() for i in self.issues]


def _grad_year(profile: Profile) -> int | None:
    """从教育经历推断毕业年份（取最晚的一段）。"""
    years: list[int] = []
    for edu in profile.education:
        period = edu.period or ""
        # 「2023.09 ~ 至今」必须先判：否则下面的兜底 findall 只会捞到 2023，
        # 把在读学生算成 2023 届已毕业，届别校验直接误报 fail
        if re.search(r"[~–—-]\s*(至今|现在|在读)|(?:19|20)\d{2}\s*(至今|现在|在读)", period):
            years.append(9999)  # 在读
            continue
        m = re.search(r"(\d{4})\s*[-./]\s*\d{1,2}\s*[~–—-]\s*(\d{4})", period)
        if m:
            years.append(int(m.group(2)))
            continue
        m = re.search(r"(\d{4})\s*[~–—-]\s*(至今|现在)", period)
        if m:
            years.append(9999)  # 在读
            continue
        found = re.findall(r"(20\d{2})", period)
        if found:
            years.append(int(found[-1]))
    return max(years) if years else None


def _highest_degree(profile: Profile) -> int:
    best = 0
    for edu in profile.education:
        best = max(best, LEVELS.get((edu.degree or "").strip(), 0))
    return best


def check(job: Job, profile: Profile) -> EligibilityResult:
    res = EligibilityResult()
    texts = [r.text for r in job.requirements] or [job.raw_text[:4000]]
    haystack = "\n".join(texts)

    # ---- 届别
    grad = _grad_year(profile)
    for pat in GRAD_YEAR_PATTERNS:
        m = pat.search(haystack)
        if not m:
            continue
        required = int(m.group(1))
        if grad is None:
            res.issues.append(
                EligibilityIssue("warn", m.group(0), "无法从教育经历推断毕业年份，请自行核对")
            )
        elif grad >= 9999:
            res.issues.append(EligibilityIssue("info", m.group(0), "你在读，注意确认是否接受在读身份"))
        elif required != grad:
            res.issues.append(
                EligibilityIssue(
                    "fail",
                    m.group(0),
                    f"岗位要求 {required} 届，你的教育经历推算为 {grad} 届毕业，届别不符",
                )
            )
        break

    # ---- 学历
    m = DEGREE_PATTERN.search(haystack) or DEGREE_EXACT.search(haystack)
    if m:
        required = LEVELS.get(m.group(1), 0)
        mine = _highest_degree(profile)
        if mine and required and mine < required:
            res.issues.append(
                EligibilityIssue("fail", m.group(0), f"岗位要求{m.group(1)}及以上，你的最高学历低于该要求")
            )

    # ---- 专业（弱校验）
    majors = {x.group(1) for x in MAJOR_PATTERN.finditer(haystack)}
    if majors:
        my_major = " ".join(e.major or "" for e in profile.education)
        hit = any(k in my_major for k in majors) or any(k in my_major for k in ("计算机", "软件"))
        if not hit and my_major.strip():
            res.issues.append(
                EligibilityIssue(
                    "warn",
                    "专业要求：" + "、".join(sorted(majors)),
                    f"你的专业是「{my_major.strip()}」，请确认是否属于岗位认可的相关专业",
                )
            )

    # ---- 到岗时间与实习时长（无法自动判定，仅提示）
    for r in job.requirements:
        if any(k in r.text for k in ("每周到岗", "实习期", "实习时间", "不少于")):
            res.issues.append(
                EligibilityIssue("info", r.text, "需要你自行确认能否满足出勤与实习时长要求")
            )

    return res
