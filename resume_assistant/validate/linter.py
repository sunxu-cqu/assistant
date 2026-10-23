"""校验闸门（前两道，纯代码，零 API 成本）。

第一道 · 结构：IR 是否完整、evidence_id 是否真实存在
第二道 · 溯源：IR 里出现的每个数字，是否能在证据或档案里找到出处

第二道是防幻觉的核心。数字编造是简历最致命的错误，
而它恰好可以用纯规则拦住，不需要模型。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..models import Evidence, Profile, ResumeIR

YEAR_RE = re.compile(r"^(19|20)\d{2}$")
# 排除紧跟在字母后的数字：V100 / ResNet-50 / CUB-200 里的型号数字不是成果指标
NUM_RE = re.compile(r"(?<![A-Za-z0-9])\d+(?:\.\d+)?")
TECH_RE = re.compile(r"[A-Za-z][A-Za-z0-9+#.\-]{1,20}")

IGNORE_WORDS = {
    "gpa", "cet", "http", "https", "www", "com", "cn", "edu", "org",
    "pdf", "docx", "md", "json", "yaml", "sql", "api", "id",
    # 度量单位与常见缩写，不属于技术栈
    "ms", "qps", "p99", "p95", "p90", "gb", "mb", "tb", "kb",
}


@dataclass
class LintResult:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    stats: dict[str, int] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict:
        return {"ok": self.ok, "errors": self.errors, "warnings": self.warnings, "stats": self.stats}


def _allowed_numbers(evidences: list[Evidence], profile: Profile) -> set[str]:
    allowed: set[str] = set()
    for ev in evidences:
        for m in ev.metrics:
            for n in NUM_RE.findall(m.value):
                allowed.add(n)
            for n in NUM_RE.findall(m.name):
                allowed.add(n)
        for n in NUM_RE.findall(ev.claim + " " + ev.star.result):
            allowed.add(n)
        # 原始资料里出现过的数字，即使没被抽进 metrics，也不算编造
        allowed.update(ev.raw_numbers)
    for edu in profile.education:
        texts = [edu.gpa, edu.gpa_rank, edu.gpa_rank_pct, edu.period, *edu.courses, *edu.honors]
        for text in texts:
            for n in NUM_RE.findall(text or ""):
                allowed.add(n)
    s = profile.skills
    for text in [s.english, *s.all_tags()]:
        for n in NUM_RE.findall(text or ""):
            allowed.add(n)
    return allowed


def _allowed_techs(evidences: list[Evidence], profile: Profile) -> set[str]:
    tags = {t.lower() for t in profile.skills.all_tags()}
    if profile.skills.english:
        tags.add(profile.skills.english.lower())
        for w in TECH_RE.findall(profile.skills.english):
            tags.add(w.lower())
    for ev in evidences:
        tags |= {t.lower() for t in ev.skill_tags}
        tags |= {t.lower() for t in ev.domain_tags}
    return tags


def lint(ir: ResumeIR, evidences: list[Evidence], profile: Profile) -> LintResult:
    res = LintResult()
    by_id = {e.id: e for e in evidences}
    allowed_nums = _allowed_numbers(evidences, profile)
    allowed_tech = _allowed_techs(evidences, profile)

    if not ir.sections:
        res.errors.append("IR 为空：没有任何 section，无法渲染")
        return res

    bullet_count = 0
    for sec in ir.sections:
        if not sec.entries:
            res.warnings.append(f"section「{sec.title}」没有条目，将被跳过")
            continue
        # 研究兴趣是成段叙述，不按 bullet 的篇幅和标点规则约束
        is_para = sec.type == "research_interest"
        for entry in sec.entries:
            for b in entry.bullets:
                bullet_count += 1
                where = f"{sec.title} / {entry.heading or '-'}"

                if b.evidence_id not in by_id and b.evidence_id != "profile":
                    res.errors.append(f"[结构] {where}：引用了不存在的 evidence_id「{b.evidence_id}」")
                    continue

                if not b.text.strip():
                    res.errors.append(f"[结构] {where}：存在空 bullet")
                    continue

                if not is_para and len(b.text) > 60:
                    res.warnings.append(f"[篇幅] {where}：bullet 超过 60 字，建议压缩：「{b.text[:20]}…」")

                if not is_para and b.text.endswith(("。", ".")):
                    res.warnings.append(f"[格式] {where}：bullet 结尾不应有句号")

                # ---- 第二道闸：数字溯源
                for n in NUM_RE.findall(b.text):
                    if YEAR_RE.match(n):
                        continue
                    if n in allowed_nums:
                        continue
                    try:
                        if float(n) < 10:
                            continue
                    except ValueError:
                        pass
                    src = "profile" if b.evidence_id == "profile" else b.evidence_id
                    res.errors.append(
                        f"[溯源] {where}：数字「{n}」在 {src} 的证据中找不到出处，疑似编造"
                    )

                # ---- 技术名词溯源（弱校验，仅提示）
                if is_para:
                    continue
                ev = by_id.get(b.evidence_id)
                excerpt = (ev.raw_excerpt or "").lower() if ev else ""
                for word in TECH_RE.findall(b.text):
                    w = word.lower().strip(".-")
                    if len(w) < 2 or w in IGNORE_WORDS:
                        continue
                    if w in allowed_tech:
                        continue
                    if any(w in t for t in allowed_tech):
                        continue
                    # 词确实出现在原始资料里，说明只是没进 tags，不算编造
                    if excerpt and w in excerpt:
                        continue
                    res.warnings.append(f"[技术词] {where}：「{word}」不在证据的 skill_tags 中，请确认")

    res.stats = {
        "sections": len(ir.sections),
        "bullets": bullet_count,
        "errors": len(res.errors),
        "warnings": len(res.warnings),
    }
    return res
