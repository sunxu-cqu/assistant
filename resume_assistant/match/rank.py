"""精排层：LLM 对召回结果逐条打分，并输出匹配理由与缺口。

为什么必须出理由：投递是高风险决策，用户需要判断「为什么这条对口」，
而不是接受一个黑盒分数。reason 同时是组稿阶段的选材依据。
"""

from __future__ import annotations

import json

from ..llm import DeepSeek, get_llm
from ..models import Evidence, Job, MatchHit, MatchReport

SYSTEM = """你是岗位匹配评估器。对每个候选成就，判断它与目标岗位/导师方向的对口程度。

铁律：
1. 打分只依据给出的成就信息与岗位要求，禁止脑补成就里没有的经历。
2. 分数含义：85+ 直接对口可作主打；70-84 相关可作支撑；50-69 弱相关；<50 不建议写进简历。
3. reason 必须引用具体技术点或研究方向，不许写「经历匹配」这类空话。
4. gaps 只写岗位明确要求、但候选池里确实没有对应证据的能力。
5. 只输出 JSON。"""

USER_TMPL = """## 目标岗位
类型：{kind_label}
名称：{title}
单位/导师：{org}
研究方向：{topics}

要求清单：
{requirements}

关键词：{keywords}

## 候选成就池
{candidates}

## 输出格式
{{
  "hits": [
    {{"evidence_id": "ev_xxx", "score": 0, "reason": "≤40字，说明对口在哪", "matched_keywords": ["命中的关键词"]}}
  ],
  "gaps": ["岗位要求但我没有的能力，每条≤20字"]
}}"""


def _candidate_block(evs: list[Evidence]) -> str:
    lines = []
    for e in evs:
        metrics = "、".join(f"{m.name}{m.rendered()}" for m in e.metrics[:4]) or "无"
        lines.append(
            json.dumps(
                {
                    "id": e.id,
                    "title": e.title,
                    "role": e.role,
                    "claim": e.claim,
                    "domain_tags": e.domain_tags,
                    "skill_tags": e.skill_tags,
                    "metrics": metrics,
                },
                ensure_ascii=False,
            )
        )
    return "\n".join(lines)


def rank(job: Job, candidates: list[Evidence], llm: DeepSeek | None = None, min_score: float = 45.0) -> MatchReport:
    llm = llm or get_llm()
    if not candidates:
        return MatchReport(job_id=job.id, hits=[], gaps=[r.text for r in job.requirements])

    reqs = "\n".join(f"- [{r.category}{'/必须' if r.must else ''}] {r.text}" for r in job.requirements) or "（未解析）"
    prompt = USER_TMPL.format(
        kind_label="保研套磁" if job.kind.value == "baoyan" else "实习",
        title=job.title,
        org=job.org or job.mentor or "未填",
        topics="、".join(job.mentor_topics) or "无",
        requirements=reqs,
        keywords="、".join(job.keywords) or "无",
        candidates=_candidate_block(candidates),
    )

    mock = {
        "hits": [
            {"evidence_id": c.id, "score": 70, "reason": "mock 模式占位理由", "matched_keywords": c.skill_tags[:2]}
            for c in candidates[:5]
        ],
        "gaps": [],
    }
    data = llm.chat_json(SYSTEM, prompt, mock_payload=mock, max_tokens=3000)

    by_id = {c.id: c for c in candidates}
    hits: list[MatchHit] = []
    for i, h in enumerate(data.get("hits") or []):
        if not isinstance(h, dict):
            continue
        eid = str(h.get("evidence_id", "")).strip()
        ev = by_id.get(eid)
        if ev is None:
            continue
        try:
            score = float(h.get("score", 0))
        except (TypeError, ValueError):
            score = 0.0
        if score < min_score:
            continue
        hits.append(
            MatchHit(
                evidence_id=eid,
                title=ev.title,
                score=round(score, 1),
                reason=str(h.get("reason", "")).strip(),
                matched_keywords=[str(k).strip() for k in (h.get("matched_keywords") or []) if str(k).strip()],
                recall_rank=i + 1,
            )
        )

    hits.sort(key=lambda x: x.score, reverse=True)
    gaps = [str(g).strip() for g in (data.get("gaps") or []) if str(g).strip()]
    return MatchReport(job_id=job.id, hits=hits, gaps=gaps)


def match_job(job: Job, evidences: list[Evidence], llm: DeepSeek | None = None) -> MatchReport:
    from .recall import recall

    candidates = [e for e, _, _ in recall(job, evidences, top_k=30)]
    return rank(job, candidates, llm=llm)
