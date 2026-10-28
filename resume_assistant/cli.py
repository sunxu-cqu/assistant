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


# ------------------------------------------------------------------ 自检

@app.command("doctor")
def doctor_cmd(as_json: bool = typer.Option(False, "--json", help="输出 JSON")) -> None:
    """环境自检：依赖、API key、profile、资料、字体、模板。"""
    from . import doctor

    res = doctor.run()
    if as_json:
        _out(res)
    else:
        typer.echo(doctor.to_text(res))
    if not res["ok"]:
        raise typer.Exit(1)


# ------------------------------------------------------------------ 知识库

@kb_app.command("build")
def kb_build(
    force: bool = typer.Option(False, "--force", "-f", help="忽略缓存，全部重建"),
    workers: int = typer.Option(3, "--workers", "-j", help="并发抽取数（1 表示串行）"),
) -> None:
    """扫描 workspace/projects 下的项目文件夹，抽取成就单元。"""
    def progress(name: str, done: int, all_: int) -> None:
        if name:
            typer.echo(f"  [{done}/{all_}] {name}", err=True)

    sr = pipeline.scan_report()
    todo = len(sr["need_extract"])
    if todo:
        typer.echo(f"待抽取 {todo} 个文件夹，并发 {workers}…（每个文件夹一次模型调用）", err=True)
    elif sr["no_text"]:
        typer.echo(f"没有待抽取的文件夹（{len(sr['no_text'])} 个读不到文字，跑 kb scan -v 看原因）", err=True)
    elif sr["folders"]:
        typer.echo(f"{sr['folders']} 个文件夹都已是最新，跳过（要重抽加 --force）", err=True)
    _out(pipeline.build_knowledge_base(force=force, workers=workers, on_progress=progress))


@kb_app.command("scan")
def kb_scan(verbose: bool = typer.Option(False, "--verbose", "-v", help="列出每个文件")) -> None:
    """只扫描不抽取：看清楚每个文件夹读到了什么、哪些文件读不出来。"""
    rep = pipeline.scan_report()
    if not rep["folders"]:
        typer.echo("workspace/projects 下没有文件夹。")
        return

    typer.echo(f"{'文件夹':<32}{'文件':>4}{'字符':>8}   状态")
    typer.echo("─" * 68)
    for r in rep["items"]:
        if not r["files"]:
            status = "读不到任何文字"
        elif r["cached"]:
            status = "已抽取（资料未变，会跳过）"
        else:
            status = "待抽取"
        if r["truncated"]:
            status += " · 超长已截断"
        typer.echo(f"{r['folder'][:30]:<32}{len(r['files']):>4}{r['chars']:>8}   {status}")
        if verbose:
            for f in r["files"]:
                enc = r["encodings"].get(f)
                typer.echo(f"      ✓ {f}" + (f"  [{enc}]" if enc and enc != "utf-8" else ""))

    skipped = [(r["folder"], s) for r in rep["items"] for s in r["skipped"]]
    if skipped:
        typer.echo(f"\n读不出来的文件（{len(skipped)} 个）：")
        for folder, s in skipped:
            typer.echo(f"  ✗ {folder}/{s['file']}")
            typer.echo(f"      {s['reason']}")

    typer.echo(f"\n共 {rep['folders']} 个文件夹，{rep['total_chars']} 字符；待抽取 {len(rep['need_extract'])} 个。")
    if rep["no_text"]:
        typer.echo(f"读不到文字：{', '.join(rep['no_text'])}")
        typer.echo("  这些文件夹不会消耗 token，但也不会有任何证据。")


@kb_app.command("prune")
def kb_prune(yes: bool = typer.Option(False, "--yes", "-y", help="确认删除")) -> None:
    """清理来源文件夹已不存在的证据（文件夹改名/删除后遗留的幽灵证据）。"""
    res = pipeline.find_orphan_evidence()
    if not res["count"]:
        typer.echo("没有需要清理的证据。")
        return

    typer.echo(f"发现 {res['count']} 个幽灵证据文件（来源文件夹已不存在）：")
    for o in res["items"]:
        typer.echo(f"  {o['folder']}  ({o['count']} 条)")
    typer.echo("\n它们仍会参与检索，简历上可能冒出你早已删掉的项目。")

    if not yes:
        typer.echo("确认删除请加 -y：kb prune -y")
        return
    out = pipeline.prune_orphan_evidence()
    typer.echo(f"已删除 {out['count']} 个证据文件。")


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


@kb_app.command("review")
def kb_review(
    html: bool = typer.Option(True, "--html/--no-html", help="是否生成卡片页 data/review.html"),
) -> None:
    """校对抽取结果：规则查出可疑项（标错类型、时间缺失、指标无出处、重复项目）。"""
    res = pipeline.review_knowledge_base(write_html=html)
    if not res.get("ok"):
        _out(res)
        raise typer.Exit(1)

    typer.echo(res["text"])
    if res["html_path"]:
        typer.echo(f"\n卡片页：{res['html_path']}")
    if res["warnings"]:
        typer.echo("\n改完重跑 kb review 确认，或 kb build --force 重抽。")


@kb_app.command("fix")
def kb_fix(
    evidence_id: str,
    kind: str = typer.Option("", "--kind", "-k", help="research / intern / project / course / award / other"),
    period: str = typer.Option("", "--period", "-p", help="例如 2024.03 ~ 2024.06"),
    weight: float = typer.Option(0.0, "--weight", "-w", help="0~1，越大越容易被选中"),
    title: str = typer.Option("", "--title", "-t"),
    role: str = typer.Option("", "--role", "-r"),
    claim: str = typer.Option("", "--claim", "-c", help="一句话描述"),
) -> None:
    """就地改一条证据的字段（只改传了的那些）。"""
    res = pipeline.fix_evidence(
        evidence_id,
        kind=kind or None,
        period=period or None,
        weight=weight or None,
        title=title or None,
        role=role or None,
        claim=claim or None,
    )
    _out(res)
    if not res.get("ok"):
        raise typer.Exit(1)


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
        typer.echo(f"[{j['id']}] {j['kind']:10s} {j['title']}  @{j['org']}  要求{j['requirements']}条")


# -------------------------------------------------------------------- 匹配

@app.command("match")
def match_cmd(job_id: str, refresh: bool = typer.Option(False, "--refresh")) -> None:
    """对岗位做匹配，输出命中与缺口。"""
    res = pipeline.run_match(job_id, refresh=refresh)
    if not res.get("ok"):
        _out(res)
        raise typer.Exit(1)
    report = res["report"]

    elig = report.get("eligibility") or []
    if elig:
        typer.echo("\n资格校验：")
        icon = {"fail": "✗", "warn": "!", "info": "i"}
        for e in elig:
            typer.echo(f"  {icon.get(e['level'], '-')} {e['requirement']}")
            typer.echo(f"    {e['detail']}")
        if report.get("blocked"):
            typer.echo("\n  ⚠ 存在硬性条件不符，投递前请确认")

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


@app.command("rerender")
def rerender_cmd(
    app_id: str,
    template: str = typer.Option("", "--template", "-T", help="换模板；留空则沿用 IR 里记录的模板"),
) -> None:
    """手改 data/output/<app_id>/ir.yaml 后重新出 PDF（不调模型）。"""
    res = pipeline.rerender(app_id, template_key=template)
    if not res.get("ok"):
        _out(res)
        raise typer.Exit(1)
    lint = res["lint"]
    typer.echo(f"模板：{res['template']}")
    typer.echo(f"校验：{lint['stats']['bullets']} 条 bullet，{lint['stats']['errors']} 错误，{lint['stats']['warnings']} 提示")
    for w in (lint["errors"] + lint["warnings"])[:10]:
        typer.echo(f"  ! {w}")
    typer.echo(f"PDF：{res['pdf_path']}")


@app.command("templates")
def templates_cmd() -> None:
    """列出模板。"""
    for t in pipeline.all_templates():
        typer.echo(f"{t['key']:16s} {t['name']}  —  {t['description']}")


@app.command("letter")
def letter_cmd(job_id: str) -> None:
    """生成保研套磁信（主题 + 正文）。"""
    res = pipeline.generate_letter(job_id)
    if not res.get("ok"):
        _out(res)
        raise typer.Exit(1)

    letter = res["letter"]
    typer.echo("─" * 60)
    typer.echo(f"主题：{letter['subject']}")
    typer.echo("─" * 60)
    typer.echo(letter["body"])
    typer.echo("─" * 60)
    typer.echo(f"字数：{res['word_count']}")
    if letter["evidence_ids"]:
        typer.echo(f"引用证据：{', '.join(letter['evidence_ids'])}")
    if res["warnings"]:
        typer.echo("\n需人工复核：")
        for w in res["warnings"]:
            typer.echo(f"  ! {w}")
    typer.echo(f"\n已保存：{res['path']}")


@app.command("pitch")
def pitch_cmd(job_id: str) -> None:
    """生成实习投递开场白（站内信 + 邮件两版）。"""
    res = pipeline.generate_pitch(job_id)
    if not res.get("ok"):
        _out(res)
        raise typer.Exit(1)

    p = res["pitch"]
    cc = res["char_count"]
    typer.echo("─" * 60)
    typer.echo(f"站内信（{cc['short']} 字，≤80 最好）")
    typer.echo("─" * 60)
    typer.echo(p["short"])
    typer.echo("\n" + "─" * 60)
    typer.echo(f"邮件正文（{cc['full']} 字，≤220）")
    typer.echo("─" * 60)
    typer.echo(p["full"])
    typer.echo("─" * 60)
    if p["evidence_ids"]:
        typer.echo(f"引用证据：{', '.join(p['evidence_ids'])}")
    if res["warnings"]:
        typer.echo("\n需人工复核：")
        for w in res["warnings"]:
            typer.echo(f"  ! {w}")
    typer.echo(f"\n已保存：{res['path']}")


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


@app_app.command("note")
def app_note(app_id: str, note: str) -> None:
    """给投递记一笔（HR 说了什么、约了几点），不改状态。"""
    res = pipeline.note_application(app_id, note)
    if not res.get("ok"):
        _out(res)
        raise typer.Exit(1)
    typer.echo(f"已记录。当前共 {len(res['timeline'])} 条时间线记录")


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


@app.command("board")
def board_cmd(
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
    html: bool = typer.Option(True, "--html/--no-html", help="是否生成 data/board.html"),
    open_in_browser: bool = typer.Option(False, "--open", help="生成后用浏览器打开"),
) -> None:
    """投递看板：卡在哪一步、谁快截止了、谁投出去太久没动静。"""
    res = pipeline.board(write_html=html)
    if as_json:
        _out(res)
    else:
        typer.echo(res["text"])
        if res.get("html_path"):
            typer.echo(f"\n看板：{res['html_path']}")

    if open_in_browser and res.get("html_path"):
        import webbrowser

        webbrowser.open(Path(res["html_path"]).resolve().as_uri())


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass
    app()


if __name__ == "__main__":
    main()
