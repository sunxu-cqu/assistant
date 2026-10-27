"""编排层：把知识库、匹配、组稿、校验、渲染串成一条流水线。

MCP server 与 CLI 都只调这一层，保证两种入口行为完全一致。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from .compose.ir import compose
from .compose.templates import load_template, list_templates
from .config import APPS_DIR, JOBS_DIR, OUTPUT_DIR, PROFILE_PATH, ensure_dirs
from .kb.builder import build_all, load_all_evidence
from .llm import get_llm
from .match.rank import match_job
from .models import (
    AppStatus,
    Job,
    JobKind,
    JobSource,
    MatchReport,
    Profile,
    ResumeIR,
    Snapshot,
)
from .render.typst import render_pdf
from .store import db
from .validate.linter import lint


def load_profile() -> Profile:
    if PROFILE_PATH.exists():
        raw = yaml.safe_load(PROFILE_PATH.read_text(encoding="utf-8")) or {}
        return Profile(**raw)
    return Profile()


# ------------------------------------------------------------------- 知识库

def build_knowledge_base(force: bool = False) -> dict[str, Any]:
    return build_all(force=force)


def knowledge_stats() -> dict[str, Any]:
    evs = load_all_evidence()
    kinds: dict[str, int] = {}
    for e in evs:
        kinds[e.kind] = kinds.get(e.kind, 0) + 1
    return {
        "evidence_count": len(evs),
        "by_kind": kinds,
        "total_metrics": sum(len(e.metrics) for e in evs),
        "missing_source_metrics": sum(1 for e in evs for m in e.metrics if m.source in ("", "未标注")),
    }


# --------------------------------------------------------------------- 岗位

def add_job(
    *,
    kind: str,
    title: str = "",
    raw_text: str = "",
    url: str = "",
    org: str = "",
    mentor: str = "",
    location: str = "",
    deadline: str = "",
    parse: bool = True,
) -> dict[str, Any]:
    from .jobs.fetcher import FetchBlocked, fetch_jd_text, make_job
    from .jobs.parser import parse_job

    kind_enum = JobKind(kind)
    source = JobSource.MANUAL
    text = raw_text.strip()

    if url and not text:
        try:
            text = fetch_jd_text(url)
            source = JobSource.CRAWL
        except FetchBlocked as exc:
            return {
                "ok": False,
                "stage": "fetch",
                "error": str(exc),
                "hint": "请打开该链接，复制正文后用 raw_text 参数重新提交（source=manual）",
            }
    if not text:
        return {"ok": False, "stage": "input", "error": "raw_text 与 url 至少提供一个"}

    job = make_job(
        kind=kind_enum,
        title=title.strip(),
        raw_text=text,
        org=org,
        mentor=mentor,
        url=url,
        location=location,
        deadline=deadline,
        source=source,
    )
    if parse:
        job = parse_job(job, llm=get_llm())

    db.save_job(job)
    ensure_dirs()
    (JOBS_DIR / f"{job.id}.yaml").write_text(
        yaml.safe_dump(job.model_dump(mode="json"), allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    return {"ok": True, "job": job.model_dump(mode="json"), "source": source.value}


def list_jobs(kind: str | None = None) -> list[dict[str, Any]]:
    jobs = db.list_jobs(JobKind(kind) if kind else None)
    return [
        {
            "id": j.id,
            "kind": j.kind.value,
            "title": j.title,
            "org": j.org or j.mentor,
            "mentor_topics": j.mentor_topics,
            "deadline": j.deadline,
            "requirements": len(j.requirements),
        }
        for j in jobs
    ]


# --------------------------------------------------------------------- 匹配

def run_match(job_id: str, refresh: bool = False) -> dict[str, Any]:
    job = db.get_job(job_id)
    if job is None:
        return {"ok": False, "error": f"岗位不存在：{job_id}"}

    cached = db.get_match(job_id)
    if cached and not refresh:
        return {"ok": True, "cached": True, "report": cached.model_dump(mode="json")}

    evs = load_all_evidence()
    if not evs:
        return {"ok": False, "error": "知识库为空，请先运行 build_knowledge_base"}

    from .validate.eligibility import check as check_eligibility

    elig = check_eligibility(job, load_profile())
    report = match_job(job, evs, llm=get_llm())
    report.eligibility = elig.to_list()
    report.blocked = elig.blocked
    db.save_match(report)
    return {
        "ok": True,
        "cached": False,
        "blocked": report.blocked,
        "report": report.model_dump(mode="json"),
    }


# --------------------------------------------------------------- 生成简历

def _write_ir(ir: ResumeIR, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(ir.model_dump(mode="json"), allow_unicode=True, sort_keys=False), encoding="utf-8")


def generate_resume(
    job_id: str,
    template_key: str = "single_column",
    max_retry: int = 1,
) -> dict[str, Any]:
    job = db.get_job(job_id)
    if job is None:
        return {"ok": False, "stage": "load", "error": f"岗位不存在：{job_id}"}

    evs = load_all_evidence()
    if not evs:
        return {"ok": False, "stage": "load", "error": "知识库为空，请先构建知识库"}

    profile = load_profile()
    if not profile.name:
        return {
            "ok": False,
            "stage": "profile",
            "error": f"请先填写 {PROFILE_PATH}（可从 data/profile.example.yaml 复制后修改）",
        }

    report: MatchReport | None = db.get_match(job_id)
    if report is None:
        report = match_job(job, evs, llm=get_llm())
        db.save_match(report)

    template = load_template(template_key)
    llm = get_llm()

    ir: ResumeIR | None = None
    result = None
    feedback = ""
    for attempt in range(max_retry + 1):
        ir = compose(profile, job, report, evs, template, llm=llm, feedback=feedback)
        result = lint(ir, evs, profile)
        if result.ok:
            break
        # 把校验错误回灌给模型重写
        feedback = "\n".join(result.errors[:10])

    assert ir is not None and result is not None

    app = db.find_application_by_job(job_id) or db.create_application(job)
    out_dir = OUTPUT_DIR / app.id
    _write_ir(ir, out_dir / "ir.yaml")
    typ_path, pdf_path = render_pdf(ir, template, out_dir)

    db.set_snapshot(
        app.id,
        Snapshot(
            jd_path=str(JOBS_DIR / f"{job.id}.yaml"),
            ir_path=str(out_dir / "ir.yaml"),
            pdf_path=str(pdf_path) if pdf_path else "",
        ),
    )
    if result.ok:
        if app.status == AppStatus.DRAFT:
            app = db.transition(app.id, AppStatus.MATCHED, note="匹配完成")
        if app.status == AppStatus.MATCHED:
            app = db.transition(app.id, AppStatus.COMPOSED, note="简历已生成并通过校验")

    return {
        "ok": result.ok,
        "application_id": app.id,
        "status": app.status.value,
        "lint": result.to_dict(),
        "ir_path": str(out_dir / "ir.yaml"),
        "typ_path": str(typ_path),
        "pdf_path": str(pdf_path) if pdf_path else None,
        "match_summary": {
            "hits": [{"id": h.evidence_id, "title": h.title, "score": h.score, "reason": h.reason} for h in report.hits[:8]],
            "gaps": report.gaps,
            "eligibility": report.eligibility,
            "blocked": report.blocked,
        },
        "hint": None if pdf_path else "PDF 编译失败，请查看 render_error.txt，.typ 文件仍可直接编辑后手动编译",
    }


def preview_ir(job_id: str, template_key: str = "single_column") -> dict[str, Any]:
    """只生成 IR 不渲染，用于快速看结构。"""
    job = db.get_job(job_id)
    if job is None:
        return {"ok": False, "error": f"岗位不存在：{job_id}"}
    evs = load_all_evidence()
    report = db.get_match(job_id) or match_job(job, evs, llm=get_llm())
    ir = compose(load_profile(), job, report, evs, load_template(template_key), llm=get_llm())
    return {"ok": True, "ir": ir.model_dump(mode="json"), "lint": lint(ir, evs, load_profile()).to_dict()}


# ---------------------------------------------------------------- 套磁信

def generate_letter(job_id: str, max_retry: int = 1) -> dict[str, Any]:
    """为保研岗位生成套磁邮件（主题 + 正文）。"""
    from .compose.letter import generate as compose_letter
    from .compose.letter import to_markdown

    job = db.get_job(job_id)
    if job is None:
        return {"ok": False, "stage": "load", "error": f"岗位不存在：{job_id}"}
    if job.kind != JobKind.BAOYAN:
        return {
            "ok": False,
            "stage": "kind",
            "error": f"套磁信只适用于保研类岗位（kind=baoyan），当前是 {job.kind.value}",
        }

    profile = load_profile()
    if not profile.name:
        return {"ok": False, "stage": "profile", "error": f"请先填写 {PROFILE_PATH}"}

    evs = load_all_evidence()
    if not evs:
        return {"ok": False, "stage": "load", "error": "知识库为空，请先构建知识库"}

    report = db.get_match(job_id)
    if report is None:
        report = match_job(job, evs, llm=get_llm())
        db.save_match(report)

    letter = compose_letter(job, profile, report, evs, llm=get_llm(), max_retry=max_retry)

    app = db.find_application_by_job(job_id) or db.create_application(job)
    out_dir = OUTPUT_DIR / app.id
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path = out_dir / "cover_letter.md"
    md_path.write_text(to_markdown(letter, job), encoding="utf-8")
    letter.path = str(md_path)

    return {
        "ok": True,
        "application_id": app.id,
        "letter": letter.model_dump(mode="json"),
        "word_count": letter.word_count(),
        "warnings": letter.warnings,
        "path": str(md_path),
        "hint": None if not letter.warnings else "存在提示项，建议人工复核后再发送",
    }


# ------------------------------------------------------------------ 状态机

def advance(app_id: str, to_status: str, note: str = "") -> dict[str, Any]:
    try:
        app = db.transition(app_id, AppStatus(to_status), note)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "application": app.model_dump(mode="json"), "timeline": db.app_timeline(app_id)}


def list_applications(status: str | None = None) -> list[dict[str, Any]]:
    apps = db.list_applications(AppStatus(status) if status else None)
    out = []
    for a in apps:
        job = db.get_job(a.job_id)
        out.append(
            {
                "id": a.id,
                "job_id": a.job_id,
                "kind": a.kind.value,
                "title": job.title if job else "",
                "org": (job.org or job.mentor) if job else "",
                "status": a.status.value,
                "pdf": a.snapshot.pdf_path,
                "updated": a.history[-1].at if a.history else "",
            }
        )
    return out


def get_application(app_id: str) -> dict[str, Any]:
    app = db.get_application(app_id)
    if app is None:
        return {"ok": False, "error": f"投递记录不存在：{app_id}"}
    return {
        "ok": True,
        "application": app.model_dump(mode="json"),
        "timeline": db.app_timeline(app_id),
        "job": (db.get_job(app.job_id) or Job(id="", kind=JobKind.INTERNSHIP, title="")).model_dump(mode="json"),
    }


def all_templates() -> list[dict[str, str]]:
    return list_templates()
