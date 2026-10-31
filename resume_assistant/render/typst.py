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


def _parse_pt(v: object, default: float = 10.0) -> float:
    try:
        return float(str(v).strip().rstrip("pt"))
    except (TypeError, ValueError):
        return default


def build_source(ir: ResumeIR, template: Template, font_size: float | None = None) -> str:
    page = template.page or {}
    mx = page.get("margin_x", "1.6cm")
    my = page.get("margin_y", "1.4cm")
    base = font_size if font_size else _parse_pt(page.get("font_size", "10pt"))
    accent = page.get("accent", "#1f4e79")

    # 字号全部相对基准推导，放大时整体等比放大，不会出现「正文变大、标题不变」的错位
    size = f"{base:g}pt"
    name_size = f"{base * 1.9:g}pt"
    head_size = f"{base * 1.15:g}pt"
    meta_size = f"{base * 0.9:g}pt"

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
        "#align(center)[#text(size: %s, weight: \"bold\")[%s]]" % (name_size, name),
        "#align(center)[#v(-2pt)#text(size: %s, fill: rgb(\"#555555\"))[%s]]" % (meta_size, info) if info else "",
        "",
    ]

    for sec in ir.sections:
        if not sec.entries:
            continue
        lines.append("#v(2pt)")
        lines.append(
            '#text(size: %s, weight: "bold", fill: rgb("%s"))[%s]' % (head_size, accent, esc(sec.title))
        )
        lines.append('#v(-6pt)#line(length: 100%%, stroke: 0.7pt + rgb("%s"))#v(3pt)' % accent)

        for idx, entry in enumerate(sec.entries):
            if idx > 0:
                lines.append("#v(3pt)")
            if entry.heading and entry.meta:
                lines.append(
                    "#grid(columns: (1fr, auto), [#text(weight: \"bold\")[%s]], align(right)[#text(size: %s, fill: rgb(\"#555555\"))[%s]])"
                    % (esc(entry.heading), meta_size, esc(entry.meta))
                )
            elif entry.heading:
                lines.append("#text(weight: \"bold\")[%s]" % esc(entry.heading))
            elif entry.meta:
                lines.append('#text(size: %s, fill: rgb("#555555"))[%s]' % (meta_size, esc(entry.meta)))

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


def _compile(ir: ResumeIR, template: Template, out_dir: Path, size: float, stem: str) -> Path | None:
    """按指定字号编译一次，返回 PDF 路径；失败返回 None。"""
    typ_path = out_dir / f"{stem}.typ"
    typ_path.write_text(build_source(ir, template, font_size=size), encoding="utf-8")
    pdf_path = out_dir / f"{stem}.pdf"
    try:
        import typst

        typst.compile(str(typ_path), output=str(pdf_path), root=str(out_dir))
    except Exception as exc:  # noqa: BLE001
        (out_dir / f"{stem}_error.txt").write_text(str(exc), encoding="utf-8")
        return None
    return pdf_path


def _page_count(pdf_path: Path) -> int | None:
    try:
        import pypdfium2 as pdfium

        return len(pdfium.PdfDocument(str(pdf_path)))
    except Exception:  # noqa: BLE001
        return None


def pdf_pages(pdf_path: Path | None) -> int | None:
    """渲染后有几页。拿不到页数返回 None（不要因为数不出页数就报错）。"""
    return _page_count(pdf_path) if pdf_path else None


def render_pdf(
    ir: ResumeIR,
    template: Template,
    out_dir: Path,
    autofit: bool = True,
) -> tuple[Path, Path | None]:
    """返回 (typ 路径, pdf 路径或 None)。PDF 失败不阻塞，typ 一定产出。

    字号自适应：内容偏少时（比如只有 3 个项目）基准字号会把 A4 填成半页空，
    看起来像没做完。这里以模板字号为起点逐步放大，取「再加就多出一页」之前
    的最大字号。放大上限由模板 page.max_scale 控制（默认 1.20）。
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    page = template.page or {}
    base = _parse_pt(page.get("font_size", "10pt"))
    max_scale = float(page.get("max_scale", 1.20))

    typ_path = out_dir / "resume.typ"
    pdf_path = out_dir / "resume.pdf"

    # 基准字号：这一步必须成功，失败就是真的渲染不了
    typ_path.write_text(build_source(ir, template, font_size=base), encoding="utf-8")
    try:
        import typst

        typst.compile(str(typ_path), output=str(pdf_path), root=str(out_dir))
    except Exception as exc:  # noqa: BLE001
        (out_dir / "render_error.txt").write_text(str(exc), encoding="utf-8")
        return typ_path, None

    if not autofit or max_scale <= 1.0:
        return typ_path, pdf_path

    base_pages = _page_count(pdf_path)
    if base_pages is None:
        return typ_path, pdf_path

    # 从大到小试，第一个「页数没变多」的就是能用的最大字号
    steps = [max_scale, (1 + max_scale) / 2, 1.0 + (max_scale - 1.0) / 3]
    chosen = base
    for k, scale in enumerate(steps):
        size = round(base * scale, 2)
        if size <= chosen:
            continue
        trial_pdf = _compile(ir, template, out_dir, size, stem=f"_fit{k}")
        pages = _page_count(trial_pdf) if trial_pdf else None
        if pages is not None and pages <= base_pages:
            chosen = size
            typ_path.write_text(build_source(ir, template, font_size=chosen), encoding="utf-8")
            pdf_path.write_bytes(trial_pdf.read_bytes())
            break

    # 清理试排中间产物
    for f in out_dir.glob("_fit*"):
        f.unlink(missing_ok=True)

    return typ_path, pdf_path
