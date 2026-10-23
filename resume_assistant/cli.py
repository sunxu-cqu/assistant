"""命令行入口：python -m resume_assistant.cli <命令>

与 MCP 工具共用 pipeline 层，行为完全一致。
适合批量操作与脚本化，例如一次性给多个岗位生成简历。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import typer

from . import pipeline

app = typer.Typer(add_completion=False, help="简历助手 · 本地知识库驱动的定向简历生成")
kb_app = typer.Typer(help="知识库操作")
job_app = typer.Typer(help="岗位池操作")
app_app = typer.Typer(help="投递记录与状态机")
app.add_typer(kb_app, name="kb")
app.add_typer(job_app, name="job")
app.add_typer(app_app, name="app")


def _out(data) -> None:
    typer.echo(json.dumps(data, ensure_ascii=False, indent=2))


# ------------------------------------------------------------------ 知识库

@kb_app.command("build")
def kb_build(force: bool = typer.Option(False, "--force", "-f", help="忽略缓存，全部重建")) -> None:
    """扫描 workspace/projects 下的项目文件夹，抽取成就单元。"""
    _out(pipeline.build_knowledge_base(force=force))


@kb_app.command("stats")
def kb_stats() -> None:
    """知识库概况。"""
    _out(pipeline.knowledge_stats())


@kb_app.command("list")
def kb_list(limit: int = 100) -> None:
    """列出全部成就单元。"""
    from .kb.builder import load_all_evidence

    evs = load_all_evidence()[:limit]
    for e in evs:
        typer.echo(f"[{e.id}] {e.title}  ({e.kind})  weight={e.weight}")
        typer.echo(f"    {e.claim}")
        if e.metrics:
            typer.echo("    指标: " + ", ".join(f"{m.name}={m.rendered()}" for m in e.metrics))


# -------------------------------------------------------------------- 岗位

@job_app.command("add")
def job_add(
    kind: str = typer.Option(..., "--kind", "-k", help="baoyan 或 internship"),
    text: str = typer.Option("", "--text", "-t", help="JD 原文"),
    file: str = typer.Option("", "--file", help="从文件读 JD 原文"),
    url: str = typer.Option("", "--url", "-u", help="官网链接（自动抓取，可能失败）"),
    title: str = typer.Option("", "--title"),
    org: str = typer.Option("", "--org"),
    mentor: str = typer.Option("", "--mentor"),
    location: str = typer.Option("", "--location"),
    deadline: str = typer.Option("", "--deadline"),
) -> None:
    """新增投递目标（手动粘贴或官网抓取）。"""
    raw = text
    if file:
        raw = Path(file).read_text(encoding="utf-8")
    _out(
        pipeline.add_job(
            kind=kind,
            raw_text=raw,
            url=url,
            title=title,
            org=org,
            mentor=mentor,
            location=location,
            deadline=deadline,
        )
    )


@job_app.command("list")
def job_list(kind: str = typer.Option("", "--kind", "-k")) -> None:
    """列出岗位池。"""
    for j in pipeline.list_jobs(kind or None):
        typer.echo(f"[{j['id']}] {j['kind']:10s} {j['title']}  @{j['org']}  要求{len(j['requirements'])}条")


# -------------------------------------------------------------------- 匹配

@app.command("match")
def match_cmd(job_id: str, refresh: bool = typer.Option(False, "--refresh")) -> None:
    """对岗位做匹配，输出命中与缺口。"""
    res = pipeline.run_match(job_id, refresh=refresh)
    if not res.get("ok"):
        _out(res)
        raise typer.Exit(1)
    report = res["report"]
    typer.echo(f"\n匹配结果 · {len(report['hits'])} 条命中\n")
    for h in report["hits"]:
        typer.echo(f"  {h['score']:5.1f}  {h['title']}")
        typer.echo(f"         {h['reason']}")
    if report["gaps"]:
        typer.echo("\n能力缺口：")
        for g in report["gaps"]:
            typer.echo(f"  - {g}")


@app.command("gen")
def gen_cmd(
    job_id: str,
    template: str = typer.Option("single_column", "--template", "-T"),
) -> None:
    """生成定向简历（PDF）。"""
    res = pipeline.generate_resume(job_id, template_key=template)
    if not res.get("ok"):
        typer.echo("生成失败：")
        _out(res)
        raise typer.Exit(1)
    lint = res["lint"]
    typer.echo(f"投递编号：{res['application_id']}  状态：{res['status']}")
    typer.echo(f"校验：{lint['stats']['bullets']} 条 bullet，{lint['stats']['errors']} 错误，{lint['stats']['warnings']} 提示")
    if lint["warnings"]:
        for w in lint["warnings"][:10]:
            typer.echo(f"  ! {w}")
    typer.echo(f"PDF：{res['pdf_path'] or '未生成'}")
    typer.echo(f"源码：{res['typ_path']}")


@app.command("templates")
def templates_cmd() -> None:
    """列出模板。"""
    for t in pipeline.all_templates():
        typer.echo(f"{t['key']:16s} {t['name']}  —  {t['description']}")


# ---------------------------------------------------------------- 投递状态机

@app_app.command("list")
def app_list(status: str = typer.Option("", "--status", "-s")) -> None:
    """列出投递记录。"""
    items = pipeline.list_applications(status or None)
    if not items:
        typer.echo("暂无投递记录")
        return
    for a in items:
        typer.echo(f"[{a['id']}] {a['status']:11s} {a['kind']:10s} {a['title']}  @{a['org']}")


@app_app.command("advance")
def app_advance(
    app_id: str,
    to_status: str,
    note: str = typer.Option("", "--note", "-n"),
) -> None:
    """推进投递状态。"""
    res = pipeline.advance(app_id, to_status, note)
    if not res.get("ok"):
        _out(res)
        raise typer.Exit(1)
    typer.echo(f"已推进到 {res['application']['status']}")


@app_app.command("show")
def app_show(app_id: str) -> None:
    """查看投递详情与时间线。"""
    res = pipeline.get_application(app_id)
    if not res.get("ok"):
        _out(res)
        raise typer.Exit(1)
    a = res["application"]
    typer.echo(f"{a['id']}  {a['status']}  ({a['kind']})")
    typer.echo(f"PDF: {a['snapshot'].get('pdf_path') or '-'}")
    typer.echo("\n时间线：")
    for e in res["timeline"]:
        typer.echo(f"  {e['at']}  {e['status']:11s} {e['note'] or ''}")


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass
    app()


if __name__ == "__main__":
    main()
