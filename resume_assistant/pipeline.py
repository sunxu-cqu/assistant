"""编排层：把知识库、匹配、组稿、校验、渲染串成一条流水线。

MCP server 与 CLI 都只调这一层，保证两种入口行为完全一致。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from .compose.ir import compose
from .compose.templates import load_template, list_templates
from .config import APPS_DIR, DATA, JOBS_DIR, OUTPUT_DIR, PROFILE_PATH, ConfigError, ensure_dirs
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
from .render.typst import pdf_pages, render_pdf
from .store import db
from .validate.linter import lint


def load_profile() -> Profile:
    if not PROFILE_PATH.exists():
        return Profile()
    raw_text = PROFILE_PATH.read_text(encoding="utf-8")
    try:
        raw = yaml.safe_load(raw_text) or {}
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        where = f"第 {mark.line + 1} 行" if mark is not None else ""
        problem = getattr(exc, "problem", None) or str(exc).splitlines()[0]
        raise ConfigError(f"{PROFILE_PATH} 格式有误{('（' + where + '）') if where else ''}：{problem}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"{PROFILE_PATH} 应该是一个键值对（YAML 字典），现在读到的是 {type(raw).__name__}")
    try:
        return Profile(**raw)
    except ValidationError as exc:
        first = exc.errors()[0]
        field = ".".join(str(x) for x in first.get("loc", ())) or "（整体）"
        raise ConfigError(
            f"{PROFILE_PATH} 字段「{field}」类型不对：{first.get('msg', '')}。"
            "数字和日期请加引号，例如 period: \"2022-09 ~ 2026-06\""
        ) from exc


# ------------------------------------------------------------------- 知识库

def build_knowledge_base(
    force: bool = False,
    workers: int = 3,
    on_progress: Any = None,
) -> dict[str, Any]:
    rep = build_all(force=force, workers=workers, on_progress=on_progress)
    if rep["folders"] == 0:
        rep["hint"] = (
            "workspace/projects 下没有项目文件夹。一个项目一个文件夹放进去；"
            "想先试跑，把 workspace/samples/ 里的两个示例复制过来"
        )
    # 抽完立刻告诉你「有多少条需要人工确认」，而不是让你自己去翻 YAML
    try:
        from .kb.review import audit

        res = audit(load_all_evidence())
        if res.findings:
            rep["review_hint"] = (
                f"{res.flagged}/{res.total} 条证据有疑点，跑 `kb review` 逐条看"
                f"（其中 {res.warnings} 个警告）"
            )
    except Exception:  # noqa: BLE001
        pass
    return rep


def scan_report() -> dict[str, Any]:
    """只扫描不抽取：先看清楚每个文件夹读到了什么、漏了什么。

    抽 20 个文件夹要花几分钟和不少 token，扫一遍只要几秒。
    真实资料里总有几份 .doc、扫描件 PDF 或截图是读不出来的，
    先看这份清单，比抽完对着「证据不对」发懵要强。
    """
    from .kb.builder import _load_index
    from .kb.scanner import scan_all

    folders = scan_all()
    index = _load_index()
    rows: list[dict[str, Any]] = []
    for f in folders:
        cached = index.get(f.name, {}).get("fingerprint") == f.fingerprint
        rows.append(
            {
                "folder": f.name,
                "files": f.files,
                "skipped": f.skipped,
                "chars": f.chars,
                "truncated": f.truncated,
                "cached": cached,
                "encodings": f.encodings,
            }
        )

    return {
        "ok": True,
        "folders": len(rows),
        "total_chars": sum(r["chars"] for r in rows),
        "no_text": [r["folder"] for r in rows if not r["files"]],
        "need_extract": [r["folder"] for r in rows if r["files"] and not r["cached"]],
        "items": rows,
    }


def knowledge_stats() -> dict[str, Any]:
    from .kb.builder import broken_evidence_files

    evs = load_all_evidence()
    kinds: dict[str, int] = {}
    for e in evs:
        kinds[e.kind] = kinds.get(e.kind, 0) + 1
    broken = broken_evidence_files()
    return {
        "evidence_count": len(evs),
        "by_kind": kinds,
        "total_metrics": sum(len(e.metrics) for e in evs),
        "missing_source_metrics": sum(1 for e in evs for m in e.metrics if m.source in ("", "未标注")),
        # 读不出来的证据文件：这些条目不会出现在任何简历里，必须让用户看见
        "broken_files": broken,
        "hint": f"{len(broken)} 个证据文件有问题，这些条目不会被用上" if broken else None,
    }


def review_knowledge_base(write_html: bool = True) -> dict[str, Any]:
    """抽取结果校对：规则检测出可疑项，可选生成卡片页方便逐条看。"""
    from .kb.review import audit, render_html, to_text

    evs = load_all_evidence()
    if not evs:
        return {"ok": False, "error": "知识库为空，请先跑 kb build"}

    res = audit(evs)
    out: dict[str, Any] = {"ok": True, **res.to_dict(), "text": to_text(res), "html_path": ""}
    if write_html:
        path = DATA / "review.html"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render_html(res, evs, subtitle=""), encoding="utf-8")
        out["html_path"] = str(path)
    return out


def fix_evidence(evidence_id: str, **fields: Any) -> dict[str, Any]:
    """改一条证据的字段（kind / period / weight / title / role / claim）。"""
    from .kb.builder import update_evidence

    return update_evidence(evidence_id, **fields)


def find_orphan_evidence() -> dict[str, Any]:
    """列出「来源文件夹已不存在」的证据文件。"""
    from .kb.builder import find_orphans

    items = find_orphans()
    return {
        "ok": True,
        "count": len(items),
        "items": items,
        "hint": "文件夹改名或删除后遗留的幽灵证据，跑 kb prune -y 清掉" if items else None,
    }


def prune_orphan_evidence() -> dict[str, Any]:
    from .kb.builder import prune_orphans

    return prune_orphans()


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

    try:
        kind_enum = JobKind(kind)
    except ValueError:
        # 直接 JobKind(kind) 抛的 ValueError 一路穿到 CLI，用户看到的是
        # 「'实习' is not a valid JobKind」加一屏堆栈，而不是「请用 internship」
        return {
            "ok": False,
            "stage": "input",
            "error": f"kind 只能是 baoyan（保研）或 internship（实习），收到「{kind}」",
            "hint": "保研套磁用 baoyan，实习投递用 internship",
        }
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


def _read_ir(path: Path) -> ResumeIR:
    return ResumeIR(**(yaml.safe_load(path.read_text(encoding="utf-8")) or {}))


def rerender(app_id: str, template_key: str = "") -> dict[str, Any]:
    """用磁盘上的 ir.yaml 重新渲染 PDF（手改 IR 之后用）。

    这是「生成 → 手改 → 重渲染」闭环的最后一环：`gen` 把 IR 写成可读的 yaml，
    你可以改措辞、删掉不想放的条目、调整顺序，改完跑这个命令出 PDF。

    与 `gen` 的区别：不调模型、不重排内容，只重新校验 + 重新排版。
    校验有错也照常渲染——你是主动改的，应该立刻看见结果，而不是被闸门挡住。
    """
    app = db.get_application(app_id)
    if app is None:
        return {"ok": False, "stage": "load", "error": f"投递记录不存在：{app_id}"}

    out_dir = OUTPUT_DIR / app.id
    ir_path = out_dir / "ir.yaml"
    if not ir_path.exists():
        return {"ok": False, "stage": "load", "error": f"找不到 {ir_path}，请先跑 gen 生成"}

    try:
        ir = _read_ir(ir_path)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "stage": "parse", "error": f"ir.yaml 解析失败：{exc}"}

    key = template_key or ir.template or "single_column"
    try:
        template = load_template(key)
    except Exception:  # noqa: BLE001
        # 和 gen 说同一句话：只报「模板不存在：xxx（模板不存在：C:\...yaml）」
        # 等于让人自己猜有哪些模板可用
        keys = "、".join(t["key"] for t in list_templates()) or "（无）"
        return {
            "ok": False,
            "stage": "template",
            "error": f"模板不存在：{key}",
            "hint": f"可用模板：{keys}",
        }

    evs = load_all_evidence()
    profile = load_profile()
    result = lint(ir, evs, profile)

    typ_path, pdf_path = render_pdf(ir, template, out_dir)
    pages = pdf_pages(pdf_path)
    _warn_pages(result, pages, app.kind)
    if template_key:
        ir.template = template.key
        _write_ir(ir, ir_path)  # 换了模板就写回，避免下次又不一致

    db.set_snapshot(
        app.id,
        Snapshot(
            jd_path=str(JOBS_DIR / f"{app.job_id}.yaml"),
            ir_path=str(ir_path),
            pdf_path=str(pdf_path) if pdf_path else "",
        ),
    )

    return {
        "ok": pdf_path is not None,
        "application_id": app.id,
        "template": template.key,
        "typ_path": str(typ_path),
        "pdf_path": str(pdf_path) if pdf_path else "",
        "lint": result.to_dict(),
        "hint": None if result.ok else "校验未通过但已渲染；这些是编造风险，发出去前务必处理",
    }


def _warn_pages(result: Any, pages: int | None, kind: Any) -> None:
    """页数超常规就提醒。

    字号自适应只会往大里试、不会往小里收：内容塞不下时它只是停手，
    不会告诉你「这份简历已经两页了」。实习简历过一页基本没人翻到第二页，
    保研简历两页是常态，所以阈值按投递类型分。
    """
    if not pages:
        return
    limit = 2 if kind == JobKind.BAOYAN else 1
    if pages > limit:
        result.warnings.append(
            f"[页数] PDF 有 {pages} 页，"
            f"{'保研简历' if kind == JobKind.BAOYAN else '实习简历'}通常 {limit} 页以内；"
            "删掉最不相关的条目，或换更紧凑的模板"
        )


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

    template = None
    try:
        template = load_template(template_key)
    except FileNotFoundError:
        keys = "、".join(t["key"] for t in list_templates()) or "（无）"
        return {
            "ok": False,
            "stage": "template",
            "error": f"模板不存在：{template_key}",
            "hint": f"可用模板：{keys}",
        }
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
    pages = pdf_pages(pdf_path)
    _warn_pages(result, pages, job.kind)

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
        "pages": pages,
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
        target = AppStatus(to_status)
    except ValueError:
        # 原样透传的话，中文用户看到的是 "'已投递' is not a valid AppStatus"
        valid = "、".join(s.value for s in AppStatus)
        return {"ok": False, "error": f"状态只能是：{valid}（收到「{to_status}」）"}
    try:
        app = db.transition(app_id, target, note)
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


def application_timeline(app_id: str) -> dict[str, Any]:
    """某条投递的状态变更历史。

    必须先确认这条投递存在：不存在的 id 直接返回空 events，客户端会读成
    「这条投递从没动过」，而不是「你 id 打错了」——前者会让人以为记录丢了。
    """
    if db.get_application(app_id) is None:
        return {"ok": False, "error": f"投递记录不存在：{app_id}"}
    return {"ok": True, "events": db.app_timeline(app_id)}


def note_application(app_id: str, note: str) -> dict[str, Any]:
    """给投递记一笔，不改状态。"""
    try:
        tl = db.add_note(app_id, note)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "timeline": tl}


def board(write_html: bool = True, today: Any = None) -> dict[str, Any]:
    """投递看板。纯规则汇总，不调模型。"""
    from .board import build, to_text

    data = build(html=write_html, today=today)
    data["text"] = to_text(data)
    return data


def generate_pitch(job_id: str, max_retry: int = 1) -> dict[str, Any]:
    """为实习岗位生成投递开场白（站内信 + 邮件两版）。"""
    from .compose.pitch import generate as compose_pitch
    from .compose.pitch import to_markdown

    job = db.get_job(job_id)
    if job is None:
        return {"ok": False, "stage": "load", "error": f"岗位不存在：{job_id}"}
    if job.kind != JobKind.INTERNSHIP:
        return {
            "ok": False,
            "stage": "kind",
            "error": f"开场白只适用于实习岗位（kind=internship），保研请用 letter 生成套磁信。当前是 {job.kind.value}",
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

    pitch = compose_pitch(job, profile, report, evs, llm=get_llm(), max_retry=max_retry)

    app = db.find_application_by_job(job_id) or db.create_application(job)
    out_dir = OUTPUT_DIR / app.id
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path = out_dir / "pitch.md"
    md_path.write_text(to_markdown(pitch, job), encoding="utf-8")
    pitch.path = str(md_path)

    return {
        "ok": True,
        "application_id": app.id,
        "pitch": pitch.model_dump(mode="json"),
        "char_count": pitch.char_count(),
        "warnings": pitch.warnings,
        "path": str(md_path),
        "hint": None if not pitch.warnings else "存在提示项，建议人工复核后再发送",
    }


def all_templates() -> list[dict[str, str]]:
    return list_templates()
