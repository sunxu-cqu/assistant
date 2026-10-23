"""Typst 渲染：Resume IR → .typ 源码 → PDF。

选 Typst 而非 LaTeX 的原因：单二进制、编译百毫秒级、中文排版无需额外配置、
源码可读可改（用户随时能手改 .typ 重编译）。
"""

from __future__ import annotations

from pathlib import Path

from ..models import ResumeIR
from ..compose.templates import Template

SPECIAL = {
    "\\": "\\\\",
    "#": "\\#",
    "*": "\\*",
    "_": "\\_",
    "@": "\\@",
    "$": "\\$",
    "[": "\\[",
    "]": "\\]",
    "<": "\\<",
    ">": "\\>",
    "~": "\\~",
    "`": "\\`",
}

FONT_STACK = '"Microsoft YaHei", "SimHei", "Source Han Sans SC", "Noto Sans CJK SC", "PingFang SC"'


def esc(text: str) -> str:
    return "".join(SPECIAL.get(ch, ch) for ch in (text or ""))


def build_source(ir: ResumeIR, template: Template) -> str:
    page = template.page or {}
    mx = page.get("margin_x", "1.6cm")
    my = page.get("margin_y", "1.4cm")
    size = page.get("font_size", "10pt")
    accent = page.get("accent", "#1f4e79")

    contact = ir.contact
    name = esc(contact.get("name", "未填写姓名"))
    info_parts = [
        contact.get("gender", ""),
        (contact.get("birth", "") + " 生") if contact.get("birth") else "",
        contact.get("political", ""),
        contact.get("phone", ""),
        contact.get("email", ""),
        contact.get("city", ""),
        contact.get("homepage", ""),
    ]
    info = "  ·  ".join(esc(p) for p in info_parts if p)

    lines: list[str] = [
        '#set page(paper: "a4", margin: (x: %s, y: %s))' % (mx, my),
        "#set text(font: (%s), size: %s, lang: \"zh\", region: \"cn\")" % (FONT_STACK, size),
        "#set par(leading: 0.72em, justify: false, spacing: 0.9em)",
        "#set list(indent: 0.8em, marker: [·], spacing: 0.42em)",
        "#set text(fill: rgb(\"#1a1a1a\"))",
        "",
        "#align(center)[#text(size: 19pt, weight: \"bold\")[%s]]" % name,
        "#align(center)[#v(-2pt)#text(size: 9pt, fill: rgb(\"#555555\"))[%s]]" % info if info else "",
        "",
    ]

    for sec in ir.sections:
        if not sec.entries:
            continue
        lines.append("#v(2pt)")
        lines.append(
            '#text(size: 11.5pt, weight: "bold", fill: rgb("%s"))[%s]' % (accent, esc(sec.title))
        )
        lines.append('#v(-6pt)#line(length: 100%%, stroke: 0.7pt + rgb("%s"))#v(3pt)' % accent)

        for idx, entry in enumerate(sec.entries):
            if idx > 0:
                lines.append("#v(3pt)")
            if entry.heading and entry.meta:
                lines.append(
                    "#grid(columns: (1fr, auto), [#text(weight: \"bold\")[%s]], align(right)[#text(size: 9pt, fill: rgb(\"#555555\"))[%s]])"
                    % (esc(entry.heading), esc(entry.meta))
                )
            elif entry.heading:
                lines.append("#text(weight: \"bold\")[%s]" % esc(entry.heading))
            elif entry.meta:
                lines.append('#text(size: 9pt, fill: rgb("#555555"))[%s]' % esc(entry.meta))

            if entry.bullets:
                if sec.type == "skills":
                    # 技能行不用列表符号，逐行排列
                    lines.append("#v(5pt)")
                    for b in entry.bullets:
                        lines.append("#block(spacing: 0.62em)[%s]" % esc(b.text))
                    lines.append("")
                else:
                    lines.append("")
                    for b in entry.bullets:
                        lines.append("- " + esc(b.text))
                    lines.append("")
            else:
                lines.append("#v(2pt)")

    return "\n".join(l for l in lines if l is not None)


def render_pdf(ir: ResumeIR, template: Template, out_dir: Path) -> tuple[Path, Path | None]:
    """返回 (typ 路径, pdf 路径或 None)。PDF 失败不阻塞，typ 一定产出。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    typ_path = out_dir / "resume.typ"
    typ_path.write_text(build_source(ir, template), encoding="utf-8")

    pdf_path: Path | None = out_dir / "resume.pdf"
    try:
        import typst

        typst.compile(str(typ_path), output=str(pdf_path), root=str(out_dir))
    except Exception as exc:  # noqa: BLE001
        (out_dir / "render_error.txt").write_text(str(exc), encoding="utf-8")
        pdf_path = None
    return typ_path, pdf_path
