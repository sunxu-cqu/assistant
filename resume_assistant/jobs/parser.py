"""JD 结构化解析：把一段粘贴/抓取来的文本拆成可匹配的要求项与关键词。

两类投递共用一套 schema，差异只在 category 分布：
- 实习：skill / experience 占多数
- 保研：domain（研究方向）占多数，mentor_topics 是核心匹配字段
"""

from __future__ import annotations

from typing import Any

from ..llm import DeepSeek, get_llm
from ..models import Job, JobKind, JobRequirement

SYSTEM = """你是岗位需求解析器。把招聘 JD 或导师主页信息拆成结构化要求。

铁律：
1. 只抽取原文明确写出的要求，不补充、不外推。
2. must=true 仅当原文出现「必须/要求/硬性/至少」等强约束表述，否则一律 false。
3. keywords 用于检索匹配，取原文中出现的技术名词、研究方向名词、方法名词，去重后 8~20 个。
4. 只输出 JSON。"""

USER_TMPL = """投递类型：{kind_label}

## 原文
{text}

## 输出格式
{{
  "title": "岗位名或研究方向名",
  "org": "公司名或学校/学院名，未知留空",
  "mentor": "导师姓名，实习类留空",
  "mentor_topics": ["导师研究方向，保研类必填，2-6个"],
  "location": "城市，未知留空",
  "deadline": "截止时间，未知留空",
  "requirements": [
    {{"text": "单条要求，≤30字", "category": "skill|domain|experience|soft|other", "must": false}}
  ],
  "keywords": ["技术词/研究方向词"]
}}"""


def parse_job(job: Job, llm: DeepSeek | None = None) -> Job:
    llm = llm or get_llm()
    kind_label = "保研套磁（联系导师）" if job.kind == JobKind.BAOYAN else "实习投递"
    prompt = USER_TMPL.format(kind_label=kind_label, text=job.raw_text[:12000])

    mock: dict[str, Any] = {
        "title": job.title or "未命名岗位",
        "org": job.org,
        "mentor": job.mentor,
        "mentor_topics": [],
        "location": job.location,
        "deadline": job.deadline,
        "requirements": [],
        "keywords": [],
    }
    data = llm.chat_json(SYSTEM, prompt, mock_payload=mock)

    reqs: list[JobRequirement] = []
    for r in data.get("requirements") or []:
        if not isinstance(r, dict) or not str(r.get("text", "")).strip():
            continue
        cat = r.get("category", "other")
        reqs.append(
            JobRequirement(
                text=str(r["text"]).strip(),
                category=cat if cat in {"skill", "domain", "experience", "soft", "other"} else "other",
                must=bool(r.get("must")),
            )
        )

    job.title = job.title or str(data.get("title", "")).strip()
    job.org = job.org or str(data.get("org", "")).strip()
    job.mentor = job.mentor or str(data.get("mentor", "")).strip()
    job.location = job.location or str(data.get("location", "")).strip()
    job.deadline = job.deadline or str(data.get("deadline", "")).strip()
    job.mentor_topics = [str(t).strip() for t in (data.get("mentor_topics") or []) if str(t).strip()]
    job.requirements = reqs
    seen: list[str] = []
    for k in data.get("keywords") or []:
        k = str(k).strip()
        if k and k not in seen:
            seen.append(k)
    job.keywords = seen
    return job
