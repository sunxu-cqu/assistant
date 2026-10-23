"""MCP server：简历助手对外的唯一契约。

VSCode 插件、终端 CLI、任意 agent harness 都通过这组工具操作，
业务逻辑全部在 pipeline 层，这里只做参数整形与错误兜底。

启动：python -m resume_assistant.mcp_server
"""

from __future__ import annotations

import sys
from typing import Any

from mcp.server.mcpserver import MCPServer as _Server

from . import pipeline

mcp = _Server("简历助手")


def _safe(fn, *args, **kwargs) -> dict[str, Any]:
    try:
        return fn(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


# ------------------------------------------------------------------ 知识库

@mcp.tool()
def kb_build(force: bool = False) -> dict:
    """扫描 workspace/projects 下的每个项目文件夹，用 LLM 抽取原子成就单元写入知识库。

    增量构建：资料未变化（按文件 mtime+大小计算指纹）的项目会自动跳过，不重复消耗 token。
    每个项目文件夹会产出 1~N 条证据，每条证据都带 domain_tags / skill_tags / metrics（含出处）。
    force=True 时强制全部重建。
    """
    return _safe(pipeline.build_knowledge_base, force=force)


@mcp.tool()
def kb_stats() -> dict:
    """返回知识库概况：证据条数、按类型分布、指标总数、以及缺少出处的指标数（应为 0）。"""
    return _safe(pipeline.knowledge_stats)


@mcp.tool()
def kb_list_evidence(limit: int = 100) -> dict:
    """列出知识库中的全部成就单元摘要，用于人工核对抽取质量。"""
    from .kb.builder import load_all_evidence

    def _run() -> dict:
        evs = load_all_evidence()
        return {
            "ok": True,
            "total": len(evs),
            "items": [
                {
                    "id": e.id,
                    "kind": e.kind,
                    "title": e.title,
                    "role": e.role,
                    "claim": e.claim,
                    "domain_tags": e.domain_tags,
                    "skill_tags": e.skill_tags,
                    "metrics": [f"{m.name}={m.rendered()}" for m in e.metrics],
                    "weight": e.weight,
                }
                for e in evs[:limit]
            ],
        }

    return _safe(_run)


# -------------------------------------------------------------------- 岗位

@mcp.tool()
def job_add(
    kind: str,
    raw_text: str = "",
    url: str = "",
    title: str = "",
    org: str = "",
    mentor: str = "",
    location: str = "",
    deadline: str = "",
) -> dict:
    """新增一个投递目标。

    kind: "baoyan"（保研套磁联系导师）或 "internship"（实习）。

    两种来源：
    - 手动：把 JD 或导师主页内容整段放进 raw_text（推荐，最稳）
    - 自动：只给 url，工具会先检查 robots.txt，再抓取公开页面正文。
      若目标站点是 JS 渲染或需要登录，会返回 stage="fetch" 的错误并提示改用手动粘贴。

    入库后会用 LLM 把原文解析成 requirements（要求清单）+ keywords（检索关键词），
    保研类还会额外抽取 mentor_topics（导师研究方向）。
    """
    return _safe(
        pipeline.add_job,
        kind=kind,
        raw_text=raw_text,
        url=url,
        title=title,
        org=org,
        mentor=mentor,
        location=location,
        deadline=deadline,
    )


@mcp.tool()
def job_list(kind: str = "") -> dict:
    """列出岗位池。kind 留空则返回全部，可选 "baoyan" / "internship"。"""
    return _safe(lambda: {"ok": True, "items": pipeline.list_jobs(kind or None)})


# -------------------------------------------------------------------- 匹配

@mcp.tool()
def job_match(job_id: str, refresh: bool = False) -> dict:
    """对指定岗位做匹配：BM25+标签召回 Top-30，再用 LLM 精排并给出理由与缺口。

    返回 hits（按对口度降序，含 score/reason/matched_keywords）和 gaps
    （岗位要求但知识库里没有对应证据的能力）。结果会缓存，refresh=True 强制重算。
    """
    return _safe(pipeline.run_match, job_id=job_id, refresh=refresh)


# ---------------------------------------------------------------- 生成简历

@mcp.tool()
def resume_generate(job_id: str, template: str = "single_column") -> dict:
    """为指定岗位生成定向简历。

    流程：取匹配结果 → 按模板槽位选材 → LLM 只改写措辞 → 校验闸门 → Typst 渲染 PDF。

    校验闸门会拦截两类问题：引用了不存在的证据、bullet 里的数字在证据中找不到出处。
    校验不通过时会自动带反馈重写一次；仍不通过则 ok=false 并列出具体错误，不会产出 PDF。
    产物路径在返回值中：ir_path（结构化中间表示）、typ_path（可手改源码）、pdf_path。
    """
    return _safe(pipeline.generate_resume, job_id=job_id, template_key=template)


@mcp.tool()
def resume_preview(job_id: str, template: str = "single_column") -> dict:
    """只生成 IR 并跑校验，不渲染 PDF。用于快速确认选材与措辞是否合适。"""
    return _safe(pipeline.preview_ir, job_id=job_id, template_key=template)


@mcp.tool()
def template_list() -> dict:
    """列出可用简历模板。single_column 适合实习网申，academic 适合保研套磁。"""
    return _safe(lambda: {"ok": True, "items": pipeline.all_templates()})


# ------------------------------------------------------------------ 状态机

@mcp.tool()
def app_list(status: str = "") -> dict:
    """列出投递记录。status 可选 draft/matched/composed/sent/replied/meeting/screening/interview/offer/rejected/withdrawn。"""
    return _safe(lambda: {"ok": True, "items": pipeline.list_applications(status or None)})


@mcp.tool()
def app_detail(app_id: str) -> dict:
    """查看单条投递的完整信息与状态流转时间线。"""
    return _safe(pipeline.get_application, app_id)


@mcp.tool()
def app_advance(app_id: str, to_status: str, note: str = "") -> dict:
    """推进投递状态。非法转移会被拒绝并提示当前可去的状态。

    保研线：draft → matched → composed → sent → replied → meeting → offer
    实习线：draft → matched → composed → sent → screening → interview → offer
    两条线都可随时转 rejected 或 withdrawn。
    """
    return _safe(pipeline.advance, app_id=app_id, to_status=to_status, note=note)


@mcp.tool()
def app_timeline(app_id: str) -> dict:
    """查看某条投递的完整状态变更历史。"""
    from .store import db

    return _safe(lambda: {"ok": True, "events": db.app_timeline(app_id)})


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass
    mcp.run()


if __name__ == "__main__":
    main()
