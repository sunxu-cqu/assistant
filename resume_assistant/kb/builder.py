"""知识库构建：项目资料 → 原子成就单元（Evidence）。

关键约束：
1. 只允许抽取资料中明确存在的事实与数字，metrics 必须带 source
2. 一个项目文件夹可产出多条 Evidence（不同侧面可分别投递不同岗位）
3. 用 fingerprint 做增量，资料没变就不重复烧 token
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml

from ..config import EVIDENCE_DIR, PROFILE_PATH, ensure_dirs
from ..llm import DeepSeek, get_llm
from ..models import Artifact, Evidence, Metric, Profile, Star, Variants, slugify
from .scanner import ProjectFolder, scan_all

INDEX_PATH = EVIDENCE_DIR / "_index.json"

SYSTEM = """你是一名严谨的简历证据抽取器。你的唯一任务是把项目资料转写成结构化的「成就单元」。

铁律：
1. 只能使用资料中明确写出的事实、技术名、数字。资料没写的，一律留空，禁止推测、禁止补全。
2. 每个数字必须能指出它在哪个文件里出现，写入 metrics[].source。
3. 不写形容词堆砌，写「做了什么 + 用什么方法 + 得到什么可验证结果」。
4. 只输出 JSON，不要任何解释文字。"""

USER_TMPL = """请从下面的项目资料中抽取成就单元。

## 项目文件夹名
{folder}

## 我的技能池（仅用于 tag 归一化，若资料中出现同义词请对齐到这里）
{skills}

## 项目资料
{content}

## 输出格式
{{
  "items": [
    {{
      "kind": "project|intern|research|course|award|activity",
      "title": "一句话项目名（≤20字）",
      "subtitle": "来源说明，如 课程项目 / 暑期实习 / 实验室课题",
      "org": "学校或公司名",
      "role": "我的角色，如 负责人 / 后端开发 / 独立完成",
      "period": "YYYY-MM ~ YYYY-MM，只能填资料明确写出的时间；只有起始时间则写 YYYY-MM ~ 至今；无法确定就留空",
      "domain_tags": ["领域标签，2-4个，如 推荐系统/图神经网络/计算机视觉"],
      "skill_tags": ["技术标签：把资料中出现过的语言、框架、中间件、数据库、库、工具全部列出，最多12个，宁多勿漏"],
      "claim": "一句话主干结论，含可验证结果，≤50字",
      "star": {{
        "situation": "背景与约束，≤60字",
        "task": "我要解决的问题，≤40字",
        "action": ["我做的关键动作，每条≤40字，2-4条"],
        "result": "结果，优先用量化指标，≤50字"
      }},
      "metrics": [
        {{"name": "指标名", "value": "数值（纯数字或百分数）", "unit": "单位", "source": "文件名或段落位置"}}
      ],
      "artifacts": [{{"type": "repo|paper|demo|doc|other", "label": "说明", "url": "链接"}}],
      "variants": {{
        "short": "≤25字的极简版，适合一页简历单行",
        "long": "≤70字的展开版",
        "academic": "≤70字的学术表述版，强调方法与贡献"
      }},
      "weight": 0.0
    }}
  ]
}}

说明：
- 若资料只够支撑 1 条，就返回 1 条；不要为了凑数拆分。
- weight 表示这个成就对「保研/实习」两类投递的综合重要度，0~1。
- metrics 若资料中没有数字，返回空数组 []。"""


def _load_profile() -> Profile:
    if PROFILE_PATH.exists():
        try:
            return Profile(**yaml.safe_load(PROFILE_PATH.read_text(encoding="utf-8")) or {})
        except Exception:  # noqa: BLE001
            return Profile()
    return Profile()


def _load_index() -> dict[str, Any]:
    if INDEX_PATH.exists():
        try:
            return json.loads(INDEX_PATH.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return {}
    return {}


def _save_index(index: dict[str, Any]) -> None:
    INDEX_PATH.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")


def _normalize_period(raw: str) -> str:
    """规范化时间区间：统一分隔符、合并重复波浪号、补齐悬空的「至今」。"""
    p = (raw or "").strip()
    if not p:
        return ""
    p = p.replace("/", "-")
    # 先把「至今」类表述统一，避免下面的「至→~」误伤
    p = re.sub(r"至今|现在|目前", "至今", p)
    if p == "至今":
        return "至今"
    for sep in ("到", "—", "－", "–"):
        p = p.replace(sep, "~")
    p = re.sub(r"至(?!今)", "~", p)
    p = re.sub(r"\s+-\s+", "~", p)
    p = re.sub(r"\s*~+\s*", " ~ ", p).strip()
    if p.endswith("~"):
        p = p.rstrip("~ ").strip() + " ~ 至今"
    elif p.endswith("至今") and " ~ " not in p:
        head = p[: -len("至今")].rstrip("~ ").strip()
        p = f"{head} ~ 至今" if head else "至今"
    if p.startswith("~"):
        p = "至今"
    return p


def _collect_numbers(text: str) -> list[str]:
    """收集原始资料里出现过的数字，作为「非编造」的证据池。

    排除紧跟在字母后的数字（V100 / ResNet-50 / CUB-200 里的型号数字），
    这些不是可量化的成果指标。
    """
    nums = re.findall(r"(?<![A-Za-z0-9])\d+(?:\.\d+)?", text or "")
    return sorted(set(nums), key=lambda x: (len(x), x))


def _to_evidence(raw: dict[str, Any], folder: ProjectFolder, idx: int) -> Evidence:
    metrics = []
    for m in raw.get("metrics") or []:
        if not isinstance(m, dict) or not str(m.get("value", "")).strip():
            continue
        metrics.append(
            Metric(
                name=str(m.get("name", "")).strip(),
                value=str(m.get("value", "")).strip(),
                unit=str(m.get("unit", "")).strip(),
                source=str(m.get("source", "")).strip() or "未标注",
            )
        )
    star_raw = raw.get("star") or {}
    variants_raw = raw.get("variants") or {}
    artifacts = []
    for a in raw.get("artifacts") or []:
        if isinstance(a, dict) and (a.get("url") or a.get("label")):
            artifacts.append(
                Artifact(
                    type=a.get("type", "other") if a.get("type") in {"repo", "paper", "demo", "doc", "other"} else "other",
                    label=str(a.get("label", "")).strip(),
                    url=str(a.get("url", "")).strip(),
                )
            )
    title = str(raw.get("title", "")).strip() or folder.name
    eid = f"ev_{slugify(folder.name, 24)}_{idx:02d}"
    return Evidence(
        id=eid,
        kind=raw.get("kind", "project"),
        title=title,
        subtitle=str(raw.get("subtitle", "")).strip(),
        org=str(raw.get("org", "")).strip(),
        role=str(raw.get("role", "")).strip(),
        period=_normalize_period(str(raw.get("period", ""))),
        domain_tags=[str(t).strip() for t in (raw.get("domain_tags") or []) if str(t).strip()],
        skill_tags=[str(t).strip() for t in (raw.get("skill_tags") or []) if str(t).strip()],
        claim=str(raw.get("claim", "")).strip(),
        star=Star(
            situation=str(star_raw.get("situation", "")).strip(),
            task=str(star_raw.get("task", "")).strip(),
            action=[str(a).strip() for a in (star_raw.get("action") or []) if str(a).strip()],
            result=str(star_raw.get("result", "")).strip(),
        ),
        metrics=metrics,
        artifacts=artifacts,
        variants=Variants(
            short=str(variants_raw.get("short", "")).strip(),
            long=str(variants_raw.get("long", "")).strip(),
            academic=str(variants_raw.get("academic", "")).strip(),
        ),
        weight=float(raw.get("weight") or 0.5),
        source_dir=str(folder.path),
        raw_excerpt=folder.text[:1200],
        raw_numbers=_collect_numbers(folder.text),
    )


def build_one(folder: ProjectFolder, llm: DeepSeek | None = None, force: bool = False) -> tuple[list[Evidence], bool]:
    """返回 (证据列表, 是否命中增量跳过)。"""
    ensure_dirs()
    llm = llm or get_llm()
    index = _load_index()
    key = folder.name
    fp = folder.fingerprint

    if not force and key in index and index[key].get("fingerprint") == fp:
        ids = set(index[key].get("evidence_ids", []))
        cached: list[Evidence] = []
        fname = index[key].get("file")
        path = EVIDENCE_DIR / fname if fname else None
        if path and path.exists():
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            for item in data.get("items", []):
                if item.get("id") in ids:
                    cached.append(Evidence(**item))
        if cached:
            return cached, True

    if not folder.text.strip():
        return [], False

    profile = _load_profile()
    prompt = USER_TMPL.format(
        folder=folder.name,
        skills=", ".join(profile.skills.all_tags()) or "（未提供）",
        content=folder.text,
    )
    mock = {
        "items": [
            {
                "kind": "project",
                "title": folder.name,
                "subtitle": "离线样例",
                "org": "",
                "role": "",
                "period": "",
                "domain_tags": ["样例"],
                "skill_tags": [],
                "claim": "mock 模式生成的占位成就",
                "star": {"situation": "", "task": "", "action": [], "result": ""},
                "metrics": [],
                "artifacts": [],
                "variants": {"short": "占位", "long": "占位", "academic": "占位"},
                "weight": 0.5,
            }
        ]
    }
    result = llm.chat_json(SYSTEM, prompt, mock_payload=mock)
    items = [_to_evidence(r, folder, i) for i, r in enumerate(result.get("items") or []) if isinstance(r, dict)]

    slug = slugify(folder.name, 24)
    old_path = EVIDENCE_DIR / f"{slug}.yaml"
    if old_path.exists():
        old_path.unlink()
    if items:
        payload = {
            "_source": {"folder": folder.name, "path": str(folder.path), "files": folder.files, "skipped": folder.skipped},
            "items": [e.model_dump(mode="json") for e in items],
        }
        (EVIDENCE_DIR / f"{slug}.yaml").write_text(
            yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8"
        )

    index[key] = {"fingerprint": fp, "evidence_ids": [e.id for e in items], "file": f"{slug}.yaml"}
    _save_index(index)
    return items, False


def build_all(force: bool = False, llm: DeepSeek | None = None) -> dict[str, Any]:
    ensure_dirs()
    folders = scan_all()
    report: dict[str, Any] = {"folders": len(folders), "created": [], "skipped": [], "empty": [], "errors": []}
    for folder in folders:
        try:
            items, cached = build_one(folder, llm=llm, force=force)
            if not items:
                report["empty"].append(folder.name)
            elif cached:
                report["skipped"].append(folder.name)
            else:
                report["created"].append({"folder": folder.name, "count": len(items), "ids": [i.id for i in items]})
        except Exception as exc:  # noqa: BLE001
            report["errors"].append({"folder": folder.name, "error": str(exc)})
    return report


def load_all_evidence() -> list[Evidence]:
    out: list[Evidence] = []
    for path in sorted(EVIDENCE_DIR.glob("*.yaml")):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception:  # noqa: BLE001
            continue
        for item in data.get("items", []):
            try:
                out.append(Evidence(**item))
            except Exception:  # noqa: BLE001
                continue
    return out


def update_evidence(evidence_id: str, **fields: Any) -> dict[str, Any]:
    """就地修改一条证据的字段（人工校对后用）。

    只改传进来的字段，其余原样保留。改完用 Evidence 重新校验一遍，
    避免手抖写出非法 kind 或越界 weight——宁可在这一步报错，
    也不要等到生成简历时才发现。
    """
    clean = {k: v for k, v in fields.items() if v is not None and v != ""}
    if not clean:
        return {"ok": False, "error": "没有要修改的字段"}

    for path in sorted(EVIDENCE_DIR.glob("*.yaml")):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception:  # noqa: BLE001
            continue
        items = data.get("items") or []
        for i, item in enumerate(items):
            if item.get("id") != evidence_id:
                continue
            try:
                ev = Evidence(**{**item, **clean})
            except Exception as exc:  # noqa: BLE001
                return {"ok": False, "error": f"字段非法：{exc}"}
            items[i] = ev.model_dump(mode="json")
            data["items"] = items
            path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
            return {"ok": True, "path": str(path), "changed": sorted(clean), "evidence": items[i]}

    return {"ok": False, "error": f"找不到证据：{evidence_id}"}


def save_evidence(ev: Evidence) -> Path:
    """单条覆盖保存（人工编辑后用）。"""
    slug = slugify(ev.source_dir.rsplit("\\", 1)[-1].rsplit("/", 1)[-1], 24) or "misc"
    path = EVIDENCE_DIR / f"{slug}.yaml"
    data = {"items": [ev.model_dump(mode="json")]}
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path
