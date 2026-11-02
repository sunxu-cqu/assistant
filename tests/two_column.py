"""第二轮 2B：渲染器支持布局（单栏 / 双栏）+ 模板预览。

测的不是「PDF 能不能编译出来」——编译失败会直接返回 None，一眼就看见了。
难发现的是这几类：

1. 段落归属：模板写了 `column: left`，渲染时却排到了右栏（或干脆整幅）；
2. 整幅段的顺序：`column: full` 的段必须在两栏之上，掉进栏里就变成半页宽；
3. 窄栏粘连：typst 里连续两行 inline 文本会并成一段，标题和日期会挤成
   「…校园社交 / 推荐核心成员 | 2025.03」，看着像排版事故；
4. 降级：只有一栏有内容时还硬画 grid，会把内容挤到半幅宽；
5. 预览：`template-preview` 必须能在没配 API key 的机器上跑（选模板不该要 key）。

运行：./.venv/Scripts/python.exe tests/two_column.py
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_SANDBOX = ROOT / "tests" / "sandbox" / "two_column"

os.environ["RESUME_ASSISTANT_MOCK"] = "1"
os.environ["RESUME_ASSISTANT_DATA"] = str(_SANDBOX / "data")
os.environ["RESUME_ASSISTANT_WORKSPACE"] = str(_SANDBOX / "workspace")

sys.path.insert(0, str(ROOT))

PASS: list[str] = []
FAIL: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(("  ok   " if cond else "  FAIL ") + name + (f"  -> {detail}" if detail and not cond else ""))


def _ir(template, profile=None, job=None, report=None, evs=None):
    from resume_assistant.compose.demo import demo_inputs
    from resume_assistant.compose.ir import compose
    from resume_assistant.llm import DeepSeek

    p, j, r, e = demo_inputs()
    return compose(profile or p, job or j, report or r, evs or e, template, llm=DeepSeek(mock=True))


def _source(template, ir=None):
    from resume_assistant.render.typst import build_source

    return build_source(ir or _ir(template), template)


def _blocks(template, ir=None):
    from resume_assistant.render.typst import _sizes, build_sections

    ir = ir or _ir(template)
    sz = _sizes(template.page or {})
    return [(sec.type, col) for sec, col, _lines in build_sections(ir, template, sz)]


def main() -> int:
    from resume_assistant.compose.templates import load_template

    two = load_template("two_column")
    single = load_template("single_column")

    # ------------------------------------------------------------ 模板字段
    print("[1] 模板里的 column 声明")
    col_of = {s.type: s.column for s in two.sections}
    check("教育背景整幅", col_of.get("education") == "full", str(col_of.get("education")))
    check("技能在左栏", col_of.get("skills") == "left", str(col_of.get("skills")))
    check("荣誉在左栏", col_of.get("honors") == "left", str(col_of.get("honors")))
    check("实习在右栏", col_of.get("experience") == "right", str(col_of.get("experience")))
    check("项目在右栏", col_of.get("projects") == "right", str(col_of.get("projects")))
    check("layout 声明为 two_column", str((two.page or {}).get("layout")) == "two_column", str(two.page.get("layout")))
    check("栏宽比例是左窄右宽", list((two.page or {}).get("columns_ratio") or []) == [1, 1.75],
          str(two.page.get("columns_ratio")))

    # ------------------------------------------------------------ 段落归属
    print("\n[2] build_sections 的段落归属")
    blocks = _blocks(two)
    got = dict(blocks)
    check("教育背景归 full", got.get("education") == "full", str(got))
    check("技能归 left", got.get("skills") == "left", str(got))
    check("荣誉归 left", got.get("honors") == "left", str(got))
    check("实习归 right", got.get("experience") == "right", str(got))
    check("项目归 right", got.get("projects") == "right", str(got))
    check("没有段落漏成默认值以外的栏", all(c in {"full", "left", "right"} for _t, c in blocks), str(blocks))

    # 单栏模板一个 column 都没写，全部应落 full
    single_cols = {c for _t, c in _blocks(single)}
    check("单栏模板全部落 full（不用写 column）", single_cols == {"full"}, str(single_cols))

    # ------------------------------------------------------------ 源码结构
    print("\n[3] 双栏源码结构")
    src = _source(two)
    check("出现双栏 grid", "column-gutter" in src)
    check("栏宽按模板比例", "columns: (1fr, 1.75fr)," in src, src[:0])
    check("双栏 grid 只出现一次", src.count("column-gutter") == 1, str(src.count("column-gutter")))

    i_grid = src.find("column-gutter")
    i_edu = src.find("教育背景")
    i_skill = src.find("专业技能")
    i_exp = src.find("实习经历")
    check("整幅段（教育背景）排在 grid 之前", 0 <= i_edu < i_grid, f"edu={i_edu} grid={i_grid}")
    check("左栏段排在 grid 之后", i_skill > i_grid, f"skill={i_skill} grid={i_grid}")
    check("右栏段在左栏段之后", i_exp > i_skill, f"exp={i_exp} skill={i_skill}")
    check("两栏都在 grid 内（右栏在源码末尾闭合前）", src.rfind(")") > i_exp)

    # 窄栏里标题与日期必须各占一行：连续 inline 文本在 typst 里会并成一段
    check("窄栏条目名包了 block（不粘连）", '#block[#text(weight: "bold")[' in src)
    check("窄栏 meta 也包了 block", '#block[#text(size: ' in src)
    # 整幅段仍走「标题左、日期右」的并排
    check("整幅段仍是并排（1fr, auto）", "columns: (1fr, auto)" in src)

    # ------------------------------------------------------------ 降级
    print("\n[4] 降级路径")
    from resume_assistant.compose.templates import Template
    from resume_assistant.render.typst import _layout_two_column, _sizes, build_sections, layout

    # 只声明 left、没有 right：画 grid 会把内容挤成半幅宽，应该退回单栏
    only_left = Template("only_left", {
        "name": "只有一栏",
        "page": {"layout": "two_column"},
        "sections": [
            {"type": "education", "title": "教育背景", "column": "full"},
            {"type": "skills", "title": "专业技能", "column": "left"},
        ],
    })
    left_src = _source(only_left)
    check("只有一栏有内容时退回单栏（不画 grid）", "column-gutter" not in left_src)
    check("退回单栏后内容仍然都在", "教育背景" in left_src and "专业技能" in left_src)

    # columns_ratio 缺省 → 1fr 1fr；写坏了也不能崩
    from resume_assistant.models import IREntry, IRSection, ResumeIR

    ir_min = ResumeIR(
        template="t",
        contact={"name": "张三"},
        sections=[
            IRSection(type="skills", title="左", entries=[IREntry(heading="", meta="", bullets=[])]),
            IRSection(type="projects", title="右", entries=[IREntry(heading="", meta="", bullets=[])]),
        ],
    )
    fake = Template("fake", {"page": {"layout": "two_column"}, "sections": [
        {"type": "skills", "column": "left"}, {"type": "projects", "column": "right"},
    ]})
    sz = _sizes({})
    out_default = "\n".join(_layout_two_column(build_sections(ir_min, fake, sz), {}))
    check("columns_ratio 缺省用 1fr 1fr", "columns: (1fr, 1fr)," in out_default, out_default[:80])
    out_bad = "\n".join(_layout_two_column(build_sections(ir_min, fake, sz), {"columns_ratio": ["x", "y"]}))
    check("columns_ratio 写坏也不崩", "columns: (1fr, 1fr)," in out_bad, out_bad[:80])

    # layout 字段没写 / 写成 single → 单栏顺序流
    plain = Template("plain", {"page": {}, "sections": [
        {"type": "skills", "column": "left"}, {"type": "projects", "column": "right"},
    ]})
    plain_src = "\n".join(layout(build_sections(ir_min, plain, sz), plain.page))
    check("没写 layout 就是单栏", "column-gutter" not in plain_src)

    # ------------------------------------------------------------ 真实渲染
    print("\n[5] 渲染与页数")
    from resume_assistant.render.typst import pdf_pages, pdf_to_png, render_pdf

    out_dir = _SANDBOX / "render" / "two_column"
    ir = _ir(two)
    _typ, pdf = render_pdf(ir, two, out_dir)
    check("双栏 PDF 编译成功", pdf is not None)
    pages = pdf_pages(pdf)
    check("双栏一页装得下（示例档案）", pages == 1, str(pages))

    text = ""
    if pdf:
        import pypdfium2 as pdfium

        doc = pdfium.PdfDocument(str(pdf))
        try:
            text = doc[0].get_textpage().get_text_range()
        finally:
            doc.close()
    check("PDF 文本里有教育背景", "教育背景" in text)
    check("PDF 文本里有左栏段", "专业技能" in text)
    check("PDF 文本里有右栏段", "实习经历" in text)
    check("整幅段排在左栏段前（全文顺序）", 0 <= text.find("教育背景") < text.find("专业技能"))
    check("左栏段排在右栏段前", 0 <= text.find("专业技能") < text.find("实习经历"))
    check("条目名没被吃掉（推荐算法实习在正文里）", "推荐算法实习" in text)
    check("条目名与日期没粘成一行", "推荐算法实习算法实习生" not in text.replace(" ", "").replace("\n", ""))

    pngs = pdf_to_png(pdf, out_dir) if pdf else []
    check("能出预览图", bool(pngs) and pngs[0].exists(), str(pngs))
    check("预览图不是空文件", bool(pngs) and pngs[0].stat().st_size > 10_000,
          str(pngs[0].stat().st_size) if pngs else "no png")

    # ------------------------------------------------------------ template_preview
    print("\n[6] template-preview 命令背后那层")
    from resume_assistant import pipeline

    r = pipeline.template_preview("two_column")
    check("two_column 预览 ok", r.get("ok") is True, str(r)[:200])
    check("预览返回 layout=two_column", r.get("layout") == "two_column", str(r.get("layout")))
    check("预览返回 png 路径", bool(r.get("png_path")) and Path(r["png_path"]).exists(), str(r.get("png_path")))
    check("预览返回 pdf 路径", bool(r.get("pdf_path")) and Path(r["pdf_path"]).exists(), str(r.get("pdf_path")))
    check("预览段序里教育背景在最前", (r.get("sections") or [""])[0] == "教育背景", str(r.get("sections")))
    check("示例档案过校验闸门（预览不该挂红字）", r["lint"]["errors"] == [], str(r["lint"]["errors"]))

    r2 = pipeline.template_preview("academic")
    check("academic 也能预览", r2.get("ok") is True and bool(r2.get("png_path")), str(r2)[:160])

    bad = pipeline.template_preview("不存在的模板")
    check("模板不存在时 ok=false", bad.get("ok") is False)
    check("模板不存在时给出可用模板", "可用模板" in (bad.get("hint") or ""), str(bad.get("hint")))

    # 预览不能依赖 API key：这条要真起一个子进程，因为 settings 在 import 时就定型了
    print("\n[7] 没配 API key 也能预览")
    env = dict(os.environ)
    env["RESUME_ASSISTANT_MOCK"] = "0"
    env["DEEPSEEK_API_KEY"] = ""
    code = (
        "import json,sys;"
        "sys.path.insert(0, r'%s');"
        "from resume_assistant import pipeline;"
        "r=pipeline.template_preview('single_column');"
        "print(json.dumps({'ok': r.get('ok'), 'png': bool(r.get('png_path'))}))" % ROOT
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, timeout=180)
    out = (proc.stdout or "").strip().splitlines()
    payload = out[-1] if out else ""
    check("无 key 时预览仍然成功", '"ok": true' in payload.lower(), payload or proc.stderr[-300:])
    check("无 key 时预览仍然出图", '"png": true' in payload.lower(), payload or proc.stderr[-300:])

    print(f"\n{'=' * 46}")
    print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    if FAIL:
        print("失败项：\n  - " + "\n  - ".join(FAIL))
    print(f"{'=' * 46}\n")
    return 0 if not FAIL else 1


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass
    raise SystemExit(main())
