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
    # 科研型模板会把它写进教育段：保研简历上「哪个实验室、跟谁」比课程分更有信息量
    lab: str = ""
    advisor: str = ""


class FamilyMember(BaseModel):
    """家庭成员。国企/事业单位的应聘登记表常要求，普通实习简历用不上。"""

    model_config = ConfigDict(coerce_numbers_to_str=True)

    name: str = ""
    relation: str = ""
    workplace: str = ""
    political: str = ""


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
    # 以下四项是体制内/国企应聘登记表的固定栏目。普通实习简历不填也不影响，
    # 因为只有 gov_soe 模板会读它们（basic_info 段）。
    native_place: str = ""   # 籍贯
    height: str = ""
    weight: str = ""
    health: str = ""
    # 证件照路径。只在模板声明了 page.photo 且文件真的存在时才画——
    # 画一个空框比不画更糟，那是「你没准备好」的视觉证据。
    photo: str = ""
    family: list[FamilyMember] = Field(default_factory=list)
    education: list[Education] = Field(default_factory=list)
    skills: Skills = Field(default_factory=Skills)
    summary: str = ""
    # 可实习时间（如「2026.01 起，每周 4 天，可实习 6 个月」）。
    # 空着就是空着——开场白里不许编，编了面试第一句就被问穿。
    internship_available: str = ""


# --------------------------------------------------------------- Evidence

class Metric(BaseModel):
    """带出处的量化指标。source 是防幻觉校验的唯一锚点。"""

    name: str
    value: str
    unit: str = ""
    source: str = ""

    def rendered(self) -> str:
        """「值 + 单位」，但值里已经带单位就不重复加。

        抽取器经常把「18%→9%」写进 value 又填 unit="%"，直接拼出来是「9%%」，
        这句话会原样进 prompt，模型很可能照着抄进简历。
        """
        v = (self.value or "").strip()
        u = (self.unit or "").strip()
        if not u:
            return v
        if v.endswith(u):
            return v
        if u in ("%", "％") and ("%" in v or "％" in v):
            return v
        return f"{v}{u}"


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
    # paper / patent 单列出来，是为了让科研型模板能把「论文与专利」单独成段。
    # 塞进 project 里的话，简历上「基于 XXX 的改进」和「论文《XXX》」会混在一起，
    # 而这两类东西在导师眼里的分量完全不同。
    kind: Literal["project", "intern", "research", "course", "award", "activity", "paper", "patent"] = "project"
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


# ------------------------------------------------------------ Deck（保研 PPT）

class DeckSlide(BaseModel):
    """一页 PPT。

    和简历 IR 的区别：简历是「压缩到一页纸」，PPT 是「一个项目展开讲一页」。
    所以这里保留了 star 的结构（背景 / 动作 / 结果），而不是压成一条 bullet。
    """

    type: Literal["cover", "profile", "project", "why", "plan", "end"] = "project"
    title: str = ""
    subtitle: str = ""       # 封面：学校 · 专业；项目页：角色 · 组织 · 时间
    meta: str = ""           # 封面：申请方向；结尾：联系方式
    footer: str = ""         # 页脚（日期等）
    bullets: list[str] = Field(default_factory=list)
    # 指标条：每项是 "0.85|F1" 这种「值|标签」，渲染时画成卡片
    metrics: list[str] = Field(default_factory=list)
    notes: str = ""          # 演讲备注，放在 pptx 的 notes 里
    evidence_id: str = ""    # 溯源：这页来自哪条证据


class DeckIR(BaseModel):
    job_id: str = ""
    template: str = "baoyan_academic"
    generated_at: str = Field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    contact: dict[str, str] = Field(default_factory=dict)
    slides: list[DeckSlide] = Field(default_factory=list)


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


# ------------------------------------------------------------ 套磁信

class CoverLetter(BaseModel):
    job_id: str
    subject: str = ""
    body: str = ""
    evidence_ids: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    path: str = ""
    generated_at: str = Field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    def word_count(self) -> int:
        return len([c for c in self.body if not c.isspace()])


class Pitch(BaseModel):
    """实习投递开场白。

    两个长度是真实场景逼出来的：站内信（Boss 直聘那类）只能塞下一句话，
    邮件正文可以写到两百字。一次生成两版，省得投的时候现删。
    """

    job_id: str
    short: str = ""      # 站内信 / 打招呼
    full: str = ""       # 邮件正文
    evidence_ids: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    path: str = ""
    generated_at: str = Field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    @staticmethod
    def _count(text: str) -> int:
        return len([c for c in (text or "") if not c.isspace()])

    def char_count(self) -> dict[str, int]:
        return {"short": self._count(self.short), "full": self._count(self.full)}


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
