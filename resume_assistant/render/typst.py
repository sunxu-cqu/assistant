"""Typst 渲染：Resume IR → .typ 源码 → PDF。

选 Typst 而非 LaTeX 的原因：单二进制、编译百毫秒级、中文排版无需额外配置、
源码可读可改（用户随时能手改 .typ 重编译）。

分成两层，因为「内容」和「摆在哪」是两件事：
- `build_sections()` 只负责把每个 section 变成 typst 片段，不关心它最终落在哪一栏；
- `layout()` 负责把这些片段摆成单栏或双栏。
单栏模板走 `_layout_single`，双栏走 `_layout_two_column`——后者把 `column: full`
的段整幅排在两栏之上，剩下的分左右两栏。加模板仍然只改 yaml，加**版式**才动这里。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..models import IREntry, IRSection, ResumeIR
from ..compose.templates import Template
from ..config import ROOT

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

# 页眉可显示的字段及其默认顺序。模板的 page.header_fields 只能从这个表里挑，
# 写错的字段名会被忽略而不是渲染成空白（YAML 里少个字母不该让简历开天窗）。
HEADER_ORDER = ["gender", "birth", "political", "phone", "email", "city", "homepage"]


def esc(text: str) -> str:
    return "".join(SPECIAL.get(ch, ch) for ch in (text or ""))


def _parse_pt(v: object, default: float = 10.0) -> float:
    try:
        return float(str(v).strip().rstrip("pt"))
    except (TypeError, ValueError):
        return default


def build_source(ir: ResumeIR, template: Template, font_size: float | None = None, photo: str = "") -> str:
    page = template.page or {}
    sz = _sizes(page, font_size)
    head = _header_lines(ir, page, sz, photo)
    blocks = build_sections(ir, template, sz)
    body = layout(blocks, page)
    return "\n".join(l for l in head + body if l is not None)


@dataclass
class _Sizes:
    """字号全部相对基准推导，放大时整体等比放大，不会出现「正文变大、标题不变」的错位。"""

    base: float
    size: str
    name_size: str
    head_size: str
    meta_size: str
    accent: str


def _sizes(page: dict, font_size: float | None = None) -> _Sizes:
    base = font_size if font_size else _parse_pt(page.get("font_size", "10pt"))
    return _Sizes(
        base=base,
        size=f"{base:g}pt",
        name_size=f"{base * 1.9:g}pt",
        head_size=f"{base * 1.15:g}pt",
        meta_size=f"{base * 0.9:g}pt",
        accent=str(page.get("accent", "#1f4e79")),
    )


def _header_lines(ir: ResumeIR, page: dict, sz: _Sizes, photo: str) -> list[str]:
    contact = ir.contact
    name = esc(contact.get("name", "未填写姓名"))
    # 页眉显示哪些字段由模板决定：体制内版要把性别/出生/政治面貌挪到「基本信息」段，
    # 页眉只留电话邮箱，否则同一份简历上「政治面貌」会出现两次。
    header_fields = page.get("header_fields") or HEADER_ORDER
    keys = [k for k in header_fields if k in HEADER_ORDER]
    info_parts = []
    for k in keys:
        v = contact.get(k, "")
        if not v:
            continue
        info_parts.append(esc(v + " 生") if k == "birth" else esc(v))
    info = "  ·  ".join(info_parts)

    title_lines = ['#align(center)[#text(size: %s, weight: "bold")[%s]]' % (sz.name_size, name)]
    if info:
        title_lines.append(
            '#align(center)[#v(-2pt)#text(size: %s, fill: rgb("#555555"))[%s]]' % (sz.meta_size, info)
        )

    lines: list[str] = [
        '#set page(paper: "a4", margin: (x: %s, y: %s))' % (page.get("margin_x", "1.6cm"), page.get("margin_y", "1.4cm")),
        "#set text(font: (%s), size: %s, lang: \"zh\", region: \"cn\")" % (FONT_STACK, sz.size),
        "#set par(leading: 0.72em, justify: false, spacing: 0.9em)",
        "#set list(indent: 0.8em, marker: [·], spacing: 0.42em)",
        "#set text(fill: rgb(\"#1a1a1a\"))",
        "",
    ]
    if photo:
        # 照片靠右、姓名居中：两侧各留同宽的空列，姓名才真的在纸张中线上。
        # 只放右列的话，姓名会被照片挤到偏左，打印出来一眼就能看出歪。
        lines += [
            "#grid(",
            "  columns: (2.6cm, 1fr, 2.6cm),",
            "  [],",
            "  [" + "\n   ".join(title_lines) + "],",
            '  align(right)[#image("%s", width: 2.4cm)]' % photo,
            ")",
        ]
    else:
        lines += title_lines
    lines.append("")
    return lines


def build_sections(ir: ResumeIR, template: Template, sz: _Sizes) -> list[tuple[IRSection, str, list[str]]]:
    """把每个非空 section 变成 (段, 落在哪一栏, typst 行)。

    「落在哪一栏」由模板的 `column` 决定：`full` 整幅、`left`/`right` 进双栏。
    没写就按 full 处理——单栏模板根本不用关心这个字段，写了也不会出错。
    """
    col_of: dict[str, str] = {}
    for spec in template.sections:
        if spec.column in {"full", "left", "right"}:
            col_of.setdefault(spec.type, spec.column)

    blocks: list[tuple[IRSection, str, list[str]]] = []
    for sec in ir.sections:
        if not sec.entries:
            continue
        col = col_of.get(sec.type, "full")
        # 进了栏里就别再把「条目名」和「日期」并排：栏宽只有半页，两段都会折行，
        # 中文又能在任意字符处断行，标题会被劈成「…校园社交 / 推荐」这种断法。
        blocks.append((sec, col, _section_lines(sec, sz, stack_meta=col != "full")))
    return blocks


def _entry_lines(entry: IREntry, sec_type: str, sz: _Sizes, stack_meta: bool) -> list[str]:
    lines: list[str] = []
    if entry.heading and entry.meta:
        if stack_meta:
            # 窄栏：条目名一行、时间/角色一行。并排的话两段都会折行，看着像排版事故。
            # 必须各自包一层 #block：typst 里连续两行 inline 文本会并成同一段，
            # 只有 block / par 才是块级元素，才会真的换行。
            lines.append('#block[#text(weight: "bold")[%s]]' % esc(entry.heading))
            lines.append('#block[#text(size: %s, fill: rgb("#555555"))[%s]]' % (sz.meta_size, esc(entry.meta)))
        else:
            lines.append(
                "#grid(columns: (1fr, auto), [#text(weight: \"bold\")[%s]], align(right)[#text(size: %s, fill: rgb(\"#555555\"))[%s]])"
                % (esc(entry.heading), sz.meta_size, esc(entry.meta))
            )
    elif entry.heading:
        lines.append('#text(weight: "bold")[%s]' % esc(entry.heading))
    elif entry.meta:
        lines.append('#text(size: %s, fill: rgb("#555555"))[%s]' % (sz.meta_size, esc(entry.meta)))

    if entry.bullets:
        # skills / basic_info / family 是逐行的信息，research_interest 是成段叙述，
        # 都不该加列表符号——加了会读成「若干条成就」，语义就错了
        if sec_type in {"skills", "basic_info", "family", "research_interest"}:
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
    return lines


def _section_lines(sec: IRSection, sz: _Sizes, stack_meta: bool = False) -> list[str]:
    lines = ["#v(2pt)"]
    lines.append('#text(size: %s, weight: "bold", fill: rgb("%s"))[%s]' % (sz.head_size, sz.accent, esc(sec.title)))
    lines.append('#v(-6pt)#line(length: 100%%, stroke: 0.7pt + rgb("%s"))#v(3pt)' % sz.accent)
    for idx, entry in enumerate(sec.entries):
        if idx > 0:
            lines.append("#v(3pt)")
        lines += _entry_lines(entry, sec.type, sz, stack_meta)
    return lines


def layout(blocks: list[tuple[IRSection, str, list[str]]], page: dict) -> list[str]:
    """把 section 片段摆成版面。单栏就是顺序拼，双栏见 _layout_two_column。"""
    if str(page.get("layout", "single")) != "two_column":
        return _layout_single(blocks)
    return _layout_two_column(blocks, page)


def _layout_single(blocks: list[tuple[IRSection, str, list[str]]]) -> list[str]:
    out: list[str] = []
    for _sec, _col, lines in blocks:
        out += lines
    return out


def _layout_two_column(blocks: list[tuple[IRSection, str, list[str]]], page: dict) -> list[str]:
    full = [lines for _s, col, lines in blocks if col == "full"]
    left = [lines for _s, col, lines in blocks if col == "left"]
    right = [lines for _s, col, lines in blocks if col == "right"]

    # 只有一栏有内容时不要画 grid：那会把内容挤到半幅宽，还不如老老实实单栏
    if not left or not right:
        return [l for chunk in (full, left, right) for lines in chunk for l in lines]

    ratio = page.get("columns_ratio") or [1, 1]
    try:
        a, b = float(ratio[0]), float(ratio[1])
    except (TypeError, ValueError, IndexError):
        a, b = 1.0, 1.0
    gutter = page.get("column_gutter", "1.4em")

    out: list[str] = [l for lines in full for l in lines]
    out += ["#grid(", "  columns: (%gfr, %gfr)," % (a, b), "  column-gutter: %s," % gutter]
    out.append("  [")
    out += ["    " + l if l else "" for lines in left for l in lines]
    out += ["  ],", "  ["]
    out += ["    " + l if l else "" for lines in right for l in lines]
    out += ["  ],", ")"]
    return out


_PHOTO_EXT = {".png", ".jpg", ".jpeg"}


def _prepare_photo(ir: ResumeIR, template: Template, out_dir: Path) -> str:
    """把证件照复制进输出目录，返回 typst 能引用的文件名；没有可用照片就返回空串。

    typst 编译时 root 设成 out_dir，引用 root 之外的文件会直接编译失败，
    而照片通常躺在用户自己的目录里。所以复制一份进来，不引用原路径。
    返回空串就表示「不画照片」——画个空框比不画更糟，那是「你没准备好」的证据。
    """
    if not (template.page or {}).get("photo"):
        return ""
    raw = str((ir.contact or {}).get("photo", "")).strip()
    if not raw:
        return ""
    src = Path(raw)
    if not src.is_absolute():
        src = ROOT / src
    if not src.exists() or src.suffix.lower() not in _PHOTO_EXT:
        return ""
    dst = out_dir / f"_photo{src.suffix.lower()}"
    try:
        dst.write_bytes(src.read_bytes())
    except OSError:
        return ""
    return dst.name


def _compile(ir: ResumeIR, template: Template, out_dir: Path, size: float, stem: str, photo: str = "") -> Path | None:
    """按指定字号编译一次，返回 PDF 路径；失败返回 None。"""
    typ_path = out_dir / f"{stem}.typ"
    typ_path.write_text(build_source(ir, template, font_size=size, photo=photo), encoding="utf-8")
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


def pdf_to_png(pdf_path: Path, out_dir: Path, pages: int = 1, scale: float = 2.0) -> list[Path]:
    """PDF 前 N 页 → PNG，给模板预览用（选模板的人得先看见版式）。

    失败返回空列表而不抛异常：预览图出不来不该让整个命令失败——PDF 还在，
    用户直接打开也能看。缺 pypdfium2 / Pillow 时同理，只是没有图。
    """
    try:
        import pypdfium2 as pdfium
    except Exception:  # noqa: BLE001
        return []

    try:
        doc = pdfium.PdfDocument(str(pdf_path))
    except Exception:  # noqa: BLE001
        return []

    out_dir.mkdir(parents=True, exist_ok=True)
    out: list[Path] = []
    try:
        for i in range(min(len(doc), max(1, pages))):
            try:
                img = doc[i].render(scale=scale).to_pil()
                path = out_dir / ("preview.png" if i == 0 else f"preview_{i + 1}.png")
                img.save(path)
                out.append(path)
            except Exception:  # noqa: BLE001
                continue
    finally:
        try:
            doc.close()
        except Exception:  # noqa: BLE001
            pass
    return out


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
    photo = _prepare_photo(ir, template, out_dir)

    typ_path = out_dir / "resume.typ"
    pdf_path = out_dir / "resume.pdf"

    # 基准字号：这一步必须成功，失败就是真的渲染不了
    typ_path.write_text(build_source(ir, template, font_size=base, photo=photo), encoding="utf-8")
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
        trial_pdf = _compile(ir, template, out_dir, size, stem=f"_fit{k}", photo=photo)
        pages = _page_count(trial_pdf) if trial_pdf else None
        if pages is not None and pages <= base_pages:
            chosen = size
            typ_path.write_text(build_source(ir, template, font_size=chosen, photo=photo), encoding="utf-8")
            pdf_path.write_bytes(trial_pdf.read_bytes())
            break

    # 清理试排中间产物
    for f in out_dir.glob("_fit*"):
        f.unlink(missing_ok=True)

    return typ_path, pdf_path
