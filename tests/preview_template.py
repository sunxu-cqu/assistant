"""模板预览：不消耗 API 额度，用示例档案 + 示例证据走一遍**真实组稿**，再逐套模板渲染。

为什么不是手写 IR：手写 IR 只能验证「渲染器画得出来」，验证不了模板本身——
段序（荣誉有没有提前）、`variant: research`（排名有没有排到时间前面）、
`include_kinds`（论文会不会混进项目段）全都由 compose 决定。手写 IR 会把这些
问题一起绕过去：预览看着挺美，真跑一遍才发现不对。

所以这里跟线上走同一条路：Profile + Evidence + MatchReport → compose() → render_pdf()，
顺带跑一遍校验闸门——模板改动要是让数字溯源失效，预览输出里直接能看到。

示例档案本身在 `resume_assistant/compose/demo.py`：`template-preview` 命令要用同一份，
放两处迟早会不一致（一处加了论文、另一处没有，两条路看到的模板就不是同一张）。

运行：./.venv/Scripts/python.exe tests/preview_template.py
产物：data/output/_preview/<模板名>/resume.pdf + preview.png
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 组稿要调模型写 bullet；预览不该花 token，也不该因为没网就出不来
os.environ.setdefault("RESUME_ASSISTANT_MOCK", "1")

from resume_assistant.compose.demo import demo_inputs  # noqa: E402
from resume_assistant.compose.ir import compose  # noqa: E402
from resume_assistant.compose.templates import list_templates, load_template  # noqa: E402
from resume_assistant.llm import get_llm  # noqa: E402
from resume_assistant.render.typst import pdf_pages, pdf_to_png, render_pdf  # noqa: E402
from resume_assistant.validate.linter import lint  # noqa: E402


def main() -> int:
    out_root = ROOT / "data" / "output" / "_preview"
    profile, job, report, evs = demo_inputs()
    llm = get_llm()

    for t in list_templates():
        template = load_template(t["key"])
        ir = compose(profile, job, report, evs, template, llm=llm)
        out = out_root / t["key"]
        _typ, pdf = render_pdf(ir, template, out)
        pngs = pdf_to_png(pdf, out) if pdf else []

        res = lint(ir, evs, profile)
        sections = " / ".join(s.title for s in ir.sections)
        layout = "双栏" if str((template.page or {}).get("layout", "single")) == "two_column" else "单栏"
        pages = pdf_pages(pdf)
        flag = "校验 OK" if res.ok else f"{len(res.errors)} 处校验错误"
        print(f"{t['key']:14s} {layout} {pages or '?'} 页 -> {pdf or 'PDF 编译失败，见 render_error.txt'}  [{flag}]")
        print(f"{'':14s}    段序：{sections}")
        if pngs:
            print(f"{'':14s}    预览：{pngs[0]}")
        if res.errors:
            for e in res.errors[:3]:
                print(f"{'':14s}    ! {e}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
