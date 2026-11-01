"""保研套磁 PPT 渲染：DeckIR → .pptx。

为什么用 python-pptx 而不是「Typst 排版后转 PPT」：
生成的 .pptx 是要**能改**的。导师姓名写错、想加一页、换个配色，用户会直接在
PowerPoint 里动手；导出的图片或 PDF 做不到这一点。

中文字体这里有个坑：只设 `run.font.name` 管不到中文——PowerPoint 用 `a:ea`
（East Asian）字体渲染汉字，不设就会退回宋体，和西文的微软雅黑混在一起。
"""

from __future__ import annotations

from pathlib import Path

from ..models import DeckIR, DeckSlide
from ..compose.deck import DeckTemplate

# 16:9 画布，单位英寸
SLIDE_W = 13.333
SLIDE_H = 7.5
MARGIN = 0.9
CONTENT_W = SLIDE_W - MARGIN * 2

DEFAULTS = {
    "accent": "#1f4e79",
    "text": "#1a1a1a",
    "muted": "#666666",
    "light": "#f5f7fa",
    "font": "微软雅黑",
}


def _rgb(value: str):
    from pptx.dml.color import RGBColor

    return RGBColor.from_string((value or "").lstrip("#") or "000000")


def _set_font(run, size: float, *, bold: bool = False, color: str = "", font: str = "") -> None:
    from lxml import etree
    from pptx.oxml.ns import qn
    from pptx.util import Pt

    run.font.size = Pt(size)
    run.font.bold = bold
    if color:
        run.font.color.rgb = _rgb(color)

    rPr = run._r.get_or_add_rPr()
    # latin 由 python-pptx 管，ea（东亚）没有封装，得手动插到 latin 后面——
    # 顺序错了 PowerPoint 会判定文件损坏
    latin = rPr.get_or_add_latin()
    latin.set("typeface", font)
    ea = rPr.find(qn("a:ea"))
    if ea is None:
        ea = etree.SubElement(rPr, qn("a:ea"))
        latin.addnext(ea)
    ea.set("typeface", font)


def _textbox(
    slide,
    left: float,
    top: float,
    width: float,
    height: float,
    text: str,
    *,
    size: float = 14,
    bold: bool = False,
    color: str = "",
    font: str = "",
    align=None,
    anchor=None,
    line_spacing: float = 1.2,
):
    from pptx.util import Inches

    box = slide.shapes.add_textbox(Inches(left), Inches(top), Inches(width), Inches(height))
    tf = box.text_frame
    tf.word_wrap = True
    if anchor is not None:
        tf.vertical_anchor = anchor
    tf.margin_left = 0
    tf.margin_right = 0
    tf.margin_top = 0
    tf.margin_bottom = 0
    p = tf.paragraphs[0]
    if align is not None:
        p.alignment = align
    p.line_spacing = line_spacing
    run = p.add_run()
    run.text = text
    _set_font(run, size, bold=bold, color=color, font=font)
    return box


def _bullets_box(
    slide,
    left: float,
    top: float,
    width: float,
    height: float,
    items: list[str],
    *,
    size: float = 15,
    color: str = "",
    font: str = "",
    gap_pt: float = 12,
    line_spacing: float = 1.35,
    anchor=None,
):
    from pptx.util import Inches, Pt

    box = slide.shapes.add_textbox(Inches(left), Inches(top), Inches(width), Inches(height))
    tf = box.text_frame
    tf.word_wrap = True
    if anchor is not None:
        tf.vertical_anchor = anchor
    tf.margin_left = 0
    tf.margin_right = 0
    tf.margin_top = 0
    tf.margin_bottom = 0
    for i, item in enumerate(items):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.space_after = Pt(gap_pt)
        p.line_spacing = line_spacing
        run = p.add_run()
        run.text = f"•  {item}"
        _set_font(run, size, color=color, font=font)
    return box


def _rect(slide, left: float, top: float, width: float, height: float, color: str):
    from pptx.enum.shapes import MSO_SHAPE
    from pptx.util import Inches

    shape = slide.shapes.add_shape(
        MSO_SHAPE.RECTANGLE, Inches(left), Inches(top), Inches(width), Inches(height)
    )
    shape.fill.solid()
    shape.fill.fore_color.rgb = _rgb(color)
    shape.line.fill.background()
    shape.shadow.inherit = False
    return shape


def _header(slide, title: str, colors: dict) -> None:
    """顶部标题条：整条色带 + 白字。比「标题 + 一条细线」更容易在投影上看清。"""
    from pptx.enum.text import MSO_ANCHOR

    _rect(slide, 0, 0, SLIDE_W, 0.78, colors["accent"])
    _textbox(
        slide, MARGIN, 0.14, CONTENT_W, 0.5, title,
        size=20, bold=True, color="#ffffff", font=colors["font"], anchor=MSO_ANCHOR.MIDDLE,
    )


def _page_number(slide, n: int, total: int, colors: dict) -> None:
    from pptx.enum.text import PP_ALIGN

    _textbox(
        slide, SLIDE_W - MARGIN - 1.2, SLIDE_H - 0.55, 1.2, 0.3,
        f"{n} / {total}", size=10, color=colors["muted"], font=colors["font"], align=PP_ALIGN.RIGHT,
    )


# ------------------------------------------------------------------ 各类页面

def _slide_cover(slide, data: DeckSlide, colors: dict) -> None:
    from pptx.enum.text import MSO_ANCHOR

    # 左侧竖条：比满屏色块克制，打印出来也不费墨
    _rect(slide, 0, 0, 0.42, SLIDE_H, colors["accent"])
    _rect(slide, 1.35, 2.35, 1.1, 0.06, colors["accent"])

    _textbox(
        slide, 1.35, 2.55, CONTENT_W - 0.45, 1.1, data.title,
        size=46, bold=True, color=colors["text"], font=colors["font"],
    )
    if data.subtitle:
        _textbox(
            slide, 1.35, 3.75, CONTENT_W - 0.45, 0.5, data.subtitle,
            size=19, color=colors["muted"], font=colors["font"],
        )
    if data.meta:
        _textbox(
            slide, 1.35, 4.45, CONTENT_W - 0.45, 0.45, data.meta,
            size=16, color=colors["accent"], font=colors["font"],
        )
    if data.footer:
        # 跟着内容走，不要钉在页面底部——中间空出一大截会显得封面没做完
        _textbox(
            slide, 1.35, 5.45, CONTENT_W - 0.45, 0.35, data.footer,
            size=12, color=colors["muted"], font=colors["font"],
        )


def _slide_list(slide, data: DeckSlide, colors: dict) -> None:
    """个人概况 / 为什么 / 研究设想：都是一串要点，用同一种版式。"""
    _header(slide, data.title, colors)

    # 每条一张浅色卡片，条目少时自动加高，不会缩成一条细缝
    items = data.bullets or [data.subtitle or ""]
    n = max(1, len(items))
    area_top = 1.35
    area_h = SLIDE_H - area_top - 0.75
    gap = 0.22
    # 一两条时压薄一点，否则一张卡片孤零零占着 1.15 英寸高，像没排版
    cap = 0.95 if n <= 2 else 1.15
    card_h = min(cap, (area_h - gap * (n - 1)) / n)
    font_size = 16 if card_h >= 0.95 else (14 if card_h >= 0.75 else 12)

    # 整组垂直居中。只有一两条时如果从顶格开始排，下面会空掉大半个页面，
    # 看着像「这页没做完」。
    total_h = n * card_h + gap * (n - 1)
    start = area_top + max(0.0, (area_h - total_h) / 2)

    for i, item in enumerate(items):
        top = start + i * (card_h + gap)
        _rect(slide, MARGIN, top, CONTENT_W, card_h, colors["light"])
        _rect(slide, MARGIN, top, 0.07, card_h, colors["accent"])
        from pptx.enum.text import MSO_ANCHOR

        _textbox(
            slide, MARGIN + 0.32, top, CONTENT_W - 0.55, card_h, item,
            size=font_size, color=colors["text"], font=colors["font"],
            anchor=MSO_ANCHOR.MIDDLE, line_spacing=1.25,
        )


def _slide_project(slide, data: DeckSlide, colors: dict) -> None:
    from pptx.enum.text import MSO_ANCHOR, PP_ALIGN

    _header(slide, "科研与项目经历", colors)

    _textbox(
        slide, MARGIN, 1.05, CONTENT_W - 3.6, 0.55, data.title,
        size=24, bold=True, color=colors["text"], font=colors["font"],
    )
    if data.subtitle:
        _textbox(
            slide, SLIDE_W - MARGIN - 3.4, 1.18, 3.4, 0.35, data.subtitle,
            size=12, color=colors["muted"], font=colors["font"], align=PP_ALIGN.RIGHT,
        )
    _rect(slide, MARGIN, 1.72, CONTENT_W, 0.02, colors["accent"])

    has_metrics = bool(data.metrics)
    bullets_h = SLIDE_H - 2.35 - (1.55 if has_metrics else 0.2)
    _bullets_box(
        slide, MARGIN, 1.95, CONTENT_W, bullets_h, data.bullets,
        size=16, color=colors["text"], font=colors["font"],
        gap_pt=18, anchor=MSO_ANCHOR.MIDDLE,
    )

    if has_metrics:
        n = len(data.metrics)
        gap = 0.3
        # 卡片不撑满整行：两个指标铺满 11.5 英寸会显得又扁又空
        card_w = min(2.7, (CONTENT_W - gap * (n - 1)) / n)
        group_w = n * card_w + gap * (n - 1)
        base_left = MARGIN + (CONTENT_W - group_w) / 2
        top = SLIDE_H - 1.75
        for i, item in enumerate(data.metrics):
            value, _, label = item.partition("|")
            left = base_left + i * (card_w + gap)
            _rect(slide, left, top, card_w, 1.05, colors["light"])
            _rect(slide, left, top, card_w, 0.06, colors["accent"])
            # 真实指标里有 0.06736673 这种 8 位小数，22pt 会顶出卡片
            vsize = 22 if len(value) <= 6 else (17 if len(value) <= 9 else 13)
            _textbox(
                slide, left, top + 0.2, card_w, 0.45, value,
                size=vsize, bold=True, color=colors["accent"], font=colors["font"],
                align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE,
            )
            _textbox(
                slide, left, top + 0.7, card_w, 0.3, label,
                size=11, color=colors["muted"], font=colors["font"], align=PP_ALIGN.CENTER,
            )


def _slide_end(slide, data: DeckSlide, colors: dict) -> None:
    from pptx.enum.text import MSO_ANCHOR, PP_ALIGN

    _rect(slide, 0, 0, SLIDE_W, 0.42, colors["accent"])
    _textbox(
        slide, MARGIN, 2.75, CONTENT_W, 1.0, data.title,
        size=44, bold=True, color=colors["text"], font=colors["font"], align=PP_ALIGN.CENTER,
    )
    _rect(slide, SLIDE_W / 2 - 0.6, 3.95, 1.2, 0.05, colors["accent"])
    if data.subtitle:
        _textbox(
            slide, MARGIN, 4.25, CONTENT_W, 0.4, data.subtitle,
            size=16, color=colors["muted"], font=colors["font"], align=PP_ALIGN.CENTER,
        )
    if data.meta:
        _textbox(
            slide, MARGIN, 4.85, CONTENT_W, 0.4, data.meta,
            size=15, color=colors["accent"], font=colors["font"], align=PP_ALIGN.CENTER,
        )


# ------------------------------------------------------------------ 入口

def render_deck(ir: DeckIR, template: DeckTemplate, out_dir: Path) -> Path:
    from pptx import Presentation
    from pptx.util import Inches

    colors = {**DEFAULTS, **{k: v for k, v in (template.page or {}).items() if k in DEFAULTS}}

    prs = Presentation()
    prs.slide_width = Inches(SLIDE_W)
    prs.slide_height = Inches(SLIDE_H)
    blank = prs.slide_layouts[6]  # Blank

    total = len(ir.slides)
    for idx, data in enumerate(ir.slides, 1):
        slide = prs.slides.add_slide(blank)
        if data.type == "cover":
            _slide_cover(slide, data, colors)
        elif data.type == "project":
            _slide_project(slide, data, colors)
        elif data.type == "end":
            _slide_end(slide, data, colors)
        else:
            _slide_list(slide, data, colors)

        if data.notes:
            try:
                slide.notes_slide.notes_text_frame.text = data.notes
            except Exception:  # noqa: BLE001
                pass
        if data.type != "cover":
            _page_number(slide, idx, total, colors)

    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "deck.pptx"
    prs.save(str(path))
    return path
