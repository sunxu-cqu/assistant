"""简历助手 · 核心数据模型。

分层原则：
- Profile   = 静态事实（教育、绩点、排名、技能），人工维护，不参与检索打分
- Evidence  = 原子成就单元，一个项目文件夹可产出 1..N 条，是检索与组稿的最小粒度
- ResumeIR  = 中间表示，渲染器的唯一输入，每条 bullet 必须带 evidence_id
- Application = 投递记录，携带状态机与快照
"""

from __future__ import annotations

import re
from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ---------------------------------------------------------------- Profile

class Education(BaseModel):
    # 允许 YAML 里把 3.82、2022 之类写成数字，统一转成字符串
    model_config = ConfigDict(coerce_numbers_to_str=True)

    school: str
    degree: str = "本科"
    major: str = ""
    period: str = ""
    gpa: str = ""
    gpa_rank: str = ""
    gpa_rank_pct: str = ""
    courses: list[str] = Field(default_factory=list)
    honors: list[str] = Field(default_factory=list)


class Skills(BaseModel):
    languages: list[str] = Field(default_factory=list)
    frameworks: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    english: str = ""
    others: list[str] = Field(default_factory=list)

    def all_tags(self) -> list[str]:
        return [*self.languages, *self.frameworks, *self.tools, *self.others]


class Profile(BaseModel):
    model_config = ConfigDict(coerce_numbers_to_str=True)

    name: str = ""
    gender: str = ""
    birth: str = ""
    phone: str = ""
    email: str = ""
    city: str = ""
    political: str = ""
    homepage: str = ""
    education: list[Education] = Field(default_factory=list)
    skills: Skills = Field(default_factory=Skills)
    summary: str = ""


# --------------------------------------------------------------- Evidence

class Metric(BaseModel):
    """带出处的量化指标。source 是防幻觉校验的唯一锚点。"""

    name: str
    value: str
    unit: str = ""
    source: str = ""

    def rendered(self) -> str:
        return f"{self.value}{self.unit}"


class Star(BaseModel):
    situation: str = ""
    task: str = ""
    action: list[str] = Field(default_factory=list)
    result: str = ""


class Artifact(BaseModel):
    type: Literal["repo", "paper", "demo", "doc", "other"] = "other"
    label: str = ""
    url: str = ""


class Variants(BaseModel):
    short: str = ""
    long: str = ""
    academic: str = ""


class Evidence(BaseModel):
    id: str
    kind: Literal["project", "intern", "research", "course", "award", "activity"] = "project"
    title: str
    subtitle: str = ""
    org: str = ""
    role: str = ""
    period: str = ""
    domain_tags: list[str] = Field(default_factory=list)
    skill_tags: list[str] = Field(default_factory=list)
    claim: str = ""
    star: Star = Field(default_factory=Star)
    metrics: list[Metric] = Field(default_factory=list)
    artifacts: list[Artifact] = Field(default_factory=list)
    variants: Variants = Field(default_factory=Variants)
    weight: float = 0.5
    source_dir: str = ""
    raw_excerpt: str = ""
    # 原始资料中出现过的全部数字，供校验闸门判断「是否编造」
    raw_numbers: list[str] = Field(default_factory=list)

    @field_validator("weight")
    @classmethod
    def _clamp(cls, v: float) -> float:
        return max(0.0, min(1.0, v))

    def search_text(self) -> str:
        parts = [
            self.title,
            self.claim,
            self.role,
            self.org,
            " ".join(self.domain_tags),
            " ".join(self.skill_tags),
            self.star.situation,
            self.star.task,
            " ".join(self.star.action),
            self.star.result,
            self.variants.short,
            self.variants.long,
        ]
        return "\n".join(p for p in parts if p)


# -------------------------------------------------------------------- Job

class JobKind(str, Enum):
    BAOYAN = "baoyan"      # 保研套磁
    INTERNSHIP = "internship"  # 实习


class JobSource(str, Enum):
    MANUAL = "manual"
    CRAWL = "crawl"


class JobRequirement(BaseModel):
    text: str
    category: Literal["skill", "domain", "experience", "soft", "other"] = "other"
    must: bool = False


class Job(BaseModel):
    id: str
    kind: JobKind
    source: JobSource = JobSource.MANUAL
    title: str
    org: str = ""                 # 公司名 / 课题组名
    mentor: str = ""              # 导师姓名（保研）
    mentor_topics: list[str] = Field(default_factory=list)  # 导师研究方向
    url: str = ""
    location: str = ""
    deadline: str = ""
    raw_text: str = ""
    requirements: list[JobRequirement] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    notes: str = ""
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))


# -------------------------------------------------------------------- IR

class Bullet(BaseModel):
    text: str
    evidence_id: str
    metrics: list[str] = Field(default_factory=list)


class IREntry(BaseModel):
    heading: str
    meta: str = ""
    bullets: list[Bullet] = Field(default_factory=list)


class IRSection(BaseModel):
    type: str
    title: str
    entries: list[IREntry] = Field(default_factory=list)


class ResumeIR(BaseModel):
    job_id: str = ""
    template: str = "single_column"
    generated_at: str = Field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    contact: dict[str, str] = Field(default_factory=dict)
    sections: list[IRSection] = Field(default_factory=list)


# ------------------------------------------------------------ Application

class AppStatus(str, Enum):
    DRAFT = "draft"            # 已建档，未匹配
    MATCHED = "matched"        # 已出匹配报告
    COMPOSED = "composed"      # 简历已生成并通过校验
    SENT = "sent"              # 已投递 / 套磁信已发
    REPLIED = "replied"        # 对方已回复
    MEETING = "meeting"        # 面谈（保研）
    SCREENING = "screening"    # 简历筛（实习）
    INTERVIEW = "interview"    # 面试
    OFFER = "offer"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"


TRANSITIONS: dict[JobKind, dict[AppStatus, set[AppStatus]]] = {
    JobKind.BAOYAN: {
        AppStatus.DRAFT: {AppStatus.MATCHED, AppStatus.WITHDRAWN},
        AppStatus.MATCHED: {AppStatus.COMPOSED, AppStatus.DRAFT, AppStatus.WITHDRAWN},
        AppStatus.COMPOSED: {AppStatus.SENT, AppStatus.MATCHED, AppStatus.WITHDRAWN},
        AppStatus.SENT: {AppStatus.REPLIED, AppStatus.REJECTED, AppStatus.WITHDRAWN},
        AppStatus.REPLIED: {AppStatus.MEETING, AppStatus.REJECTED, AppStatus.WITHDRAWN},
        AppStatus.MEETING: {AppStatus.OFFER, AppStatus.REJECTED, AppStatus.WITHDRAWN},
        AppStatus.OFFER: set(),
        AppStatus.REJECTED: set(),
        AppStatus.WITHDRAWN: set(),
    },
    JobKind.INTERNSHIP: {
        AppStatus.DRAFT: {AppStatus.MATCHED, AppStatus.WITHDRAWN},
        AppStatus.MATCHED: {AppStatus.COMPOSED, AppStatus.DRAFT, AppStatus.WITHDRAWN},
        AppStatus.COMPOSED: {AppStatus.SENT, AppStatus.MATCHED, AppStatus.WITHDRAWN},
        AppStatus.SENT: {AppStatus.SCREENING, AppStatus.REJECTED, AppStatus.WITHDRAWN},
        AppStatus.SCREENING: {AppStatus.INTERVIEW, AppStatus.REJECTED, AppStatus.WITHDRAWN},
        AppStatus.INTERVIEW: {AppStatus.OFFER, AppStatus.REJECTED, AppStatus.WITHDRAWN},
        AppStatus.OFFER: set(),
        AppStatus.REJECTED: set(),
        AppStatus.WITHDRAWN: set(),
    },
}


class StatusEvent(BaseModel):
    status: AppStatus
    at: str = Field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    note: str = ""


class Snapshot(BaseModel):
    jd_path: str = ""
    ir_path: str = ""
    pdf_path: str = ""


class Application(BaseModel):
    id: str
    job_id: str
    kind: JobKind
    status: AppStatus = AppStatus.DRAFT
    history: list[StatusEvent] = Field(default_factory=list)
    snapshot: Snapshot = Field(default_factory=Snapshot)
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))


def can_transition(kind: JobKind, src: AppStatus, dst: AppStatus) -> bool:
    return dst in TRANSITIONS.get(kind, {}).get(src, set())


# -------------------------------------------------------------- Match 报告

class MatchHit(BaseModel):
    evidence_id: str
    title: str
    score: float
    reason: str = ""
    matched_keywords: list[str] = Field(default_factory=list)
    recall_rank: int = 0


class MatchReport(BaseModel):
    job_id: str
    hits: list[MatchHit] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    # 硬性资格问题（届别/学历/专业），level=fail 表示不符合投递条件
    eligibility: list[dict[str, Any]] = Field(default_factory=list)
    blocked: bool = False
    generated_at: str = Field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))


# ------------------------------------------------------------- 工具函数

_NUM_RE = re.compile(r"\d+(?:\.\d+)?%?")


def extract_numbers(text: str) -> list[str]:
    return _NUM_RE.findall(text)


def slugify(text: str, maxlen: int = 40) -> str:
    """中文转拼音太麻烦，直接保留中文，只替换不安全字符。"""
    s = re.sub(r"[\\/:*?\"<>|\s]+", "-", text.strip())
    s = re.sub(r"-+", "-", s).strip("-")
    return s[:maxlen] or "untitled"


def to_yaml_dict(model: BaseModel) -> dict[str, Any]:
    return model.model_dump(mode="json", exclude_none=True)
