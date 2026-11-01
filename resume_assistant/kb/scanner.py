"""知识库扫描：把「一个项目一个文件夹」的资料读成纯文本。

真实资料比测试资料脏得多，所以这里的重点不是「支持多少格式」，而是
**读不到的时候要说清楚为什么**：

- 中文 txt 大量是 GBK 编码，直接按 utf-8 读会静默变成乱码或空字符串
- 老版 .doc 不是 zip 包，python-docx 打不开，必须提示另存为 docx
- 扫描件 PDF 没有文字层，抽出来是空的，看起来像「资料不存在」
- 截图（png/jpg）同理

`scan_folder()` 把读不到的每个文件连同原因记进 `skipped`，由 `kb scan`
汇总给用户看——比让人对着「0 条证据」猜要强。
"""

from __future__ import annotations

import codecs
import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

from ..config import PROJECTS_DIR, settings

TEXT_SUFFIXES = {".md", ".markdown", ".txt", ".rst", ".csv", ".json", ".yaml", ".yml", ".log"}
# 代码不整篇读（几万行喂给模型只会淹掉真正的信息），但也不该直接丢掉：
# 真实学生项目的 README 常常只有三百字，剩下的信息全在注释、docstring 和模块划分里。
# 所以代码走「结构摘要」：只留 docstring + 注释 + 定义名。
CODE_SUFFIXES = {
    ".py", ".java", ".js", ".ts", ".jsx", ".tsx", ".go", ".rs", ".c", ".cpp", ".cc",
    ".h", ".hpp", ".cs", ".rb", ".php", ".swift", ".kt", ".scala", ".m", ".sh",
    ".sql", ".vue", ".html", ".css", ".scss", ".ipynb", ".r", ".jl",
}
# 二进制产物与许可证：列进「读不出来」只会淹没真正的问题。
# 一个 Django 项目光 migrations 就能刷出二十行「不支持的格式 .py」，用户会以为工具坏了。
IGNORE_SUFFIXES = {
    ".pyc", ".pyo", ".pyd", ".class", ".o", ".obj", ".so", ".dll", ".exe", ".dylib",
    ".iml", ".lock", ".map", ".swp", ".bak", ".tmp", ".lnk",
}
IGNORE_NAMES = {
    "LICENSE", "LICENSE.txt", "LICENSE.md", "COPYING", "NOTICE",
    ".gitignore", ".gitattributes", ".ds_store", ".editorconfig", "thumbs.db",
}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp", ".tif", ".tiff", ".heic"}
ARCHIVE_SUFFIXES = {".zip", ".rar", ".7z", ".tar", ".gz"}
SKIP_DIRS = {".git", "__pycache__", ".venv", "node_modules", ".idea", ".vscode", ".pytest_cache", ".mypy_cache"}
# 代码摘要的预算：单个文件最多留这么多字，整个文件夹最多留这么多。
# 注释和 docstring 才是信息所在，函数体一律不读。
MAX_CODE_CHARS_PER_FILE = 1200
MAX_CODE_CHARS_PER_FOLDER = 6000
# 根目录 README 之外，其余文档共享的预算。
# 真实项目里常混进 vendored 的第三方库（把 BERT 源码整个拷进 bert/），
# 它那份几万字的官方 README 会把整个项目的预算吃掉，而它对自己的简历毫无用处。
MAX_DOC_CHARS_PER_FOLDER = 10000
MAX_PDF_PAGES = 40
MAX_SHEET_ROWS = 300
MAX_FILES_PER_FOLDER = 80
# 单个文件最多读这么多字。项目文件夹里混进训练日志、导出的 csv 很常见，
# 一个 6MB 的 log 就能把整个项目的字符预算吃光，真正有内容的笔记反而被截断掉
MAX_CHARS_PER_FILE = 8000
# 单个文件超过这个大小直接跳过：实验数据集、录屏、模型权重都会塞进项目文件夹，
# 读进来既慢又是噪声（模型看到 5 万行 CSV 只会晕）
MAX_FILE_MB = 20


@dataclass
class ProjectFolder:
    name: str
    path: Path
    text: str = ""
    files: list[str] = field(default_factory=list)
    # [{"file": "报告.doc", "reason": "老版 .doc 打不开，请另存为 .docx"}]
    skipped: list[dict[str, str]] = field(default_factory=list)
    truncated: bool = False
    encodings: dict[str, str] = field(default_factory=dict)

    @property
    def fingerprint(self) -> str:
        h = hashlib.sha1()
        for p in sorted(self.path.rglob("*")):
            if p.is_file() and not any(part in SKIP_DIRS for part in p.parts):
                h.update(p.name.encode("utf-8", "ignore"))
                h.update(str(int(p.stat().st_mtime)).encode())
                h.update(str(p.stat().st_size).encode())
        return h.hexdigest()[:16]

    @property
    def chars(self) -> int:
        return len(self.text)


def decode_bytes(raw: bytes) -> tuple[str, str]:
    """按 BOM → utf-8 → gb18030 → big5 的顺序试，返回 (文本, 实际编码)。

    gb18030 是 GBK/GB2312 的超集，中文 Windows 上导出的 txt、csv 基本都是它。
    直接 utf-8 + errors="ignore" 会把整篇中文吃掉，而且不报错——这是最坑的一种。
    """
    if raw.startswith(codecs.BOM_UTF8):
        return raw.decode("utf-8-sig", "replace"), "utf-8-sig"
    if raw.startswith(codecs.BOM_UTF16_LE) or raw.startswith(codecs.BOM_UTF16_BE):
        return raw.decode("utf-16", "replace"), "utf-16"
    for enc in ("utf-8", "gb18030", "big5"):
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace"), "utf-8(有损)"


def _read_text_file(path: Path) -> tuple[str, str, str]:
    raw = path.read_bytes()
    if not raw.strip():
        return "", "", "文件是空的"
    text, enc = decode_bytes(raw)
    # 控制字符太多说明猜错编码了（正常中文文本不会有大量 \x00-\x08）
    if text and sum(1 for ch in text[:2000] if ord(ch) < 9) > len(text[:2000]) * 0.05:
        return "", "", "编码无法识别，读出来是乱码"
    return text, enc, ""


def _read_pdf(path: Path) -> tuple[str, str, str]:
    """返回 (文本, 编码标记, 失败原因)。编码标记为 "ocr" 表示文字来自 OCR。"""
    try:
        import pdfplumber
    except ImportError:
        return "", "", "缺少 pdfplumber，无法读 PDF"
    out: list[str] = []
    try:
        with pdfplumber.open(str(path)) as pdf:
            for page in pdf.pages[:MAX_PDF_PAGES]:
                out.append(page.extract_text() or "")
    except Exception as exc:  # noqa: BLE001
        return "", "", f"PDF 打开失败：{type(exc).__name__}"
    text = "\n".join(out)
    if not text.strip():
        # 没有文字层 = 扫描件。装了 OCR 就识别一遍，没装才报「读不到」
        from . import ocr

        ocr_text, why = ocr.read_pdf(path)
        if ocr_text.strip():
            return ocr_text, "ocr", ""
        return "", "", why or "PDF 没有文字层（扫描件/图片版），且未启用 OCR"
    return text, "", ""


def _read_docx(path: Path) -> tuple[str, str]:
    try:
        import docx
    except ImportError:
        return "", "缺少 python-docx，无法读 Word"
    try:
        doc = docx.Document(str(path))
    except Exception:  # noqa: BLE001
        if path.suffix.lower() == ".doc":
            return "", "老版 .doc 打不开，请用 Word 另存为 .docx 后重试"
        return "", "Word 文件损坏或格式异常"
    parts = [p.text for p in doc.paragraphs if p.text.strip()]
    for table in doc.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells if c.text.strip()]
            if cells:
                parts.append(" | ".join(cells))
    return "\n".join(parts), ""


def _read_pptx(path: Path) -> tuple[str, str]:
    try:
        from pptx import Presentation
    except ImportError:
        return "", "缺少 python-pptx，无法读 PPT"
    try:
        prs = Presentation(str(path))
    except Exception:  # noqa: BLE001
        return "", "PPT 文件损坏或格式异常"
    out: list[str] = []
    for i, slide in enumerate(prs.slides, 1):
        texts: list[str] = []
        for shape in slide.shapes:
            if getattr(shape, "has_text_frame", False):
                t = (shape.text_frame.text or "").strip()
                if t:
                    texts.append(t)
            if getattr(shape, "has_table", False):
                try:
                    for row in shape.table.rows:
                        cells = [c.text.strip() for c in row.cells if c.text.strip()]
                        if cells:
                            texts.append(" | ".join(cells))
                except Exception:  # noqa: BLE001
                    pass
        if texts:
            out.append(f"[第 {i} 页]\n" + "\n".join(texts))
    if not out:
        return "", "PPT 里只有图片，没有可读文字"
    return "\n".join(out), ""


def _read_xlsx(path: Path) -> tuple[str, str]:
    try:
        import openpyxl
    except ImportError:
        return "", "缺少 openpyxl，无法读 Excel"
    try:
        wb = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
    except Exception:  # noqa: BLE001
        return "", "Excel 文件损坏或格式异常"
    out: list[str] = []
    try:
        for ws in wb.worksheets:
            rows: list[str] = []
            for n, row in enumerate(ws.iter_rows(values_only=True)):
                if n >= MAX_SHEET_ROWS:
                    rows.append(f"[超过 {MAX_SHEET_ROWS} 行，已截断]")
                    break
                cells = [str(c).strip() for c in row if c is not None and str(c).strip()]
                if cells:
                    rows.append(" | ".join(cells))
            if rows:
                out.append(f"[工作表 {ws.title}]\n" + "\n".join(rows))
    finally:
        wb.close()
    if not out:
        return "", "Excel 是空的"
    return "\n".join(out), ""


_COMMENT_RE = re.compile(r"^\s*(?:#|//|--|%)\s?(.*)$")
_DEF_RE = re.compile(
    r"^\s*(?:export\s+)?(?:public\s+|private\s+|protected\s+|static\s+|async\s+|final\s+)*"
    r"(?:class|def|function|func|fn|interface|struct|impl|module|object)\s+([A-Za-z_]\w*)"
)
_IMPORT_RE = re.compile(r"^\s*(?:import\s+([\w.]+)|from\s+([\w.]+)\s+import|require\(['\"]([\w@/.-]+)['\"]\))")
_DECORATION = "-=*#/ \t~_+"


def _looks_like_data(text: str) -> bool:
    """判断一份「文本」其实是数据文件（分词结果、IDF 表、词典、三元组导出）。

    这类文件动辄几万行，读进来有两个后果：吃掉整个项目的字符预算，
    以及把 raw_numbers 灌成数字池——反幻觉闸门于是对任何数字都放行。

    注意不能只看「有没有中文」：分词结果和词典全是中文，却一点叙述价值都没有。
    真正区分「资料」和「数据」的是**有没有句子**——标点、长句、段落。
    """
    head = text[:30000]
    if len(head) < 800:
        return False

    # 1) 数字远多于字母/汉字：IDF 表、坐标、嵌入向量
    digits = sum(ch.isdigit() for ch in head)
    alpha = sum(ch.isalpha() for ch in head)
    if digits > alpha * 1.2:
        return True

    # 2) 分词结果：空格极密，几乎没有句读。
    #    这条必须先于「行数」判断——分词结果常常是一行几百字的超长行，
    #    按行数筛会漏掉（一份 10 万字的 data_cut.txt 只有 43 行）。
    punct = sum(1 for ch in head if ch in "。！？；，、,.!?;")
    if punct / len(head) < 0.002 and head.count(" ") / len(head) > 0.12:
        return True

    lines = [ln for ln in head.splitlines() if ln.strip()]
    if len(lines) < 60:
        return False

    # 3) 一行一个词 / 一个数：词典、停用词表
    avg_len = sum(len(ln) for ln in lines) / len(lines)
    if avg_len < 20 and len(lines) > 300:
        return True

    # 4) 键值对 / 箭头对：推荐结果、"A => B C D" 这种
    sample = lines[:500]
    paired = sum(1 for ln in sample if "=>" in ln or "\t" in ln or ln.count("|") >= 2)
    return paired > len(sample) * 0.7


def _read_code(path: Path) -> tuple[str, str, str]:
    """读代码的「骨架」：docstring、注释、定义名、导入的库。

    函数体一行不读。理由是简历需要的是「做了什么、用了什么技术、怎么分模块」，
    这些恰好写在注释和命名里；把两千行实现喂给模型，只会挤掉 README 的位置。
    """
    raw = path.read_bytes()
    if not raw.strip():
        return "", "", "文件是空的"
    text, enc = decode_bytes(raw)
    if "\x00" in text[:2000]:
        return "", "", "二进制文件"

    kept: list[str] = []
    imports: list[str] = []
    in_doc = False
    for line in text.splitlines()[:4000]:
        s = line.strip()
        if not s:
            continue
        # 三引号 docstring（单行或跨行）
        quotes = s.count('"""') + s.count("'''")
        if quotes >= 2:
            inner = s.strip("\"'").strip()
            if inner:
                kept.append(inner)
            continue
        if s.startswith(('"""', "'''")):
            in_doc = not in_doc
            inner = s.strip("\"'").strip()
            if inner:
                kept.append(inner)
            continue
        if in_doc:
            kept.append(s)
            continue
        m = _IMPORT_RE.match(line)
        if m:
            mod = (m.group(1) or m.group(2) or m.group(3) or "").split(".")[0]
            if mod and mod not in ("os", "sys", "re", "json", "time", "math", "typing"):
                imports.append(mod)
            continue
        m = _DEF_RE.match(line)
        if m:
            kept.append(f"[定义] {m.group(1)}")
            continue
        m = _COMMENT_RE.match(line)
        if m:
            body = m.group(1).strip().strip(_DECORATION).strip()
            # 纯分隔线（"====="）去掉装饰后是空的，没有信息
            if len(body) > 3:
                kept.append(body)

    body = "\n".join(kept)
    if len(body) > MAX_CODE_CHARS_PER_FILE:
        body = body[:MAX_CODE_CHARS_PER_FILE] + "\n[代码摘要已截断]"
    if not body.strip():
        return "", "", "代码里没有注释或文档字符串，跳过"
    dep = f"[依赖] {', '.join(sorted(set(imports))[:12])}\n" if imports else ""
    return f"{dep}{body}", enc, ""


def _structure_tree(folder: Path, files: list[Path], limit: int = 45) -> str:
    """目录树。真实项目的模块划分（Django 的 app、bert/ 这种子包）本身就是信息。"""
    if not files:
        return ""
    rels = sorted(str(p.relative_to(folder)).replace("\\", "/") for p in files)
    if len(rels) <= limit:
        return "项目文件：\n" + "\n".join(f"  {r}" for r in rels)
    return (
        "项目文件（前 %d 个，共 %d 个）：\n" % (limit, len(rels))
        + "\n".join(f"  {r}" for r in rels[:limit])
    )


def read_file(path: Path) -> tuple[str, str, str]:
    """返回 (文本, 编码, 跳过原因)。跳过原因非空表示这个文件没读到内容。"""
    suffix = path.suffix.lower()
    try:
        size_mb = path.stat().st_size / (1024 * 1024)
        if size_mb > MAX_FILE_MB:
            return "", "", (
                f"文件 {size_mb:.0f}MB，超过 {MAX_FILE_MB}MB 上限，"
                "多半是数据集/录屏，读进来也是噪声；有结论请写进 md"
            )
    except OSError as exc:
        return "", "", f"无法读取文件信息：{type(exc).__name__}"
    try:
        if suffix in TEXT_SUFFIXES:
            text, enc, why = _read_text_file(path)
            if text.strip() and _looks_like_data(text):
                return "", "", (
                    "看起来是数据文件（分词结果 / IDF 表 / 导出的三元组），"
                    "读进来会挤掉真正有内容的文件；结论请写进 md"
                )
            return text, enc, why
        if suffix in CODE_SUFFIXES:
            return _read_code(path)
        if suffix == ".pdf":
            text, enc, why = _read_pdf(path)
            return text, enc, why
        if suffix in {".docx", ".doc"}:
            text, why = _read_docx(path)
            return text, "", why
        if suffix == ".pptx":
            text, why = _read_pptx(path)
            return text, "", why
        if suffix in {".xlsx", ".xlsm"}:
            text, why = _read_xlsx(path)
            return text, "", why
        if suffix == ".ppt":
            return "", "", "老版 .ppt 打不开，请另存为 .pptx 后重试"
        if suffix in {".xls", ".et"}:
            return "", "", "老版 .xls 打不开，请另存为 .xlsx 后重试"
        if suffix in IMAGE_SUFFIXES:
            from . import ocr

            text, why = ocr.read_image(path)
            return text, ("ocr" if text else ""), why
        if suffix in ARCHIVE_SUFFIXES:
            return "", "", "压缩包不解压，请解压后放入文件夹"
    except Exception as exc:  # noqa: BLE001
        return "", "", f"读取失败：{type(exc).__name__}"
    return "", "", f"不支持的格式 {suffix or '(无扩展名)'}"


# 依赖声明优先于普通代码：一行 requirements.txt 就把技术栈说全了，
# 比读二十个 import 更准。
_DEP_FILES = {
    "requirements.txt", "package.json", "pom.xml", "pipfile", "setup.py",
    "pyproject.toml", "go.mod", "cargo.toml", "gemfile", "environment.yml",
}


def _rank(rel: str) -> int:
    """决定先读哪个文件。

    一个项目文件夹里常有几十个代码文件 + 一份 README。截断是按字符数从后往前砍的，
    如果按文件名排序，README 很可能排在中间被砍掉，留下的全是代码。
    所以先读「叙述性」文件：README / 说明 / 报告 / 总结 / md。
    """
    low = rel.lower()
    name = low.rsplit("/", 1)[-1]
    # 根目录的 README 是作者对项目的总述，一定先读。
    # 子目录里常有一堆占位 README（每个目录放一句「本项目说明」），
    # 它们按路径排序会排在主 README 前面，把预算先花掉。
    depth = low.replace("\\", "/").count("/")
    is_readme = any(k in name for k in ("readme", "说明", "报告", "总结", "简介", "介绍", "方案", "记录"))
    if is_readme or low.endswith(".md"):
        return 0 if depth == 0 else 1
    if name in _DEP_FILES:
        return 1
    if low.endswith((".docx", ".doc", ".pdf", ".pptx", ".txt")):
        return 1
    if low.endswith(tuple(CODE_SUFFIXES)):
        return 2
    return 3


def scan_folder(folder: Path) -> ProjectFolder:
    pf = ProjectFolder(name=folder.name, path=folder)
    candidates: list[tuple[int, str, Path]] = []
    all_files: list[Path] = []
    for p in sorted(folder.rglob("*")):
        if not p.is_file():
            continue
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        if p.name.startswith("~$"):  # Office 打开文件时留下的临时锁文件
            continue
        # 二进制产物、许可证、编辑器配置：静默略过，不占「读不出来」的名额
        if p.suffix.lower() in IGNORE_SUFFIXES or p.name in IGNORE_NAMES:
            continue
        all_files.append(p)
        rel = str(p.relative_to(folder))
        candidates.append((_rank(rel), rel, p))

    candidates.sort(key=lambda x: (x[0], x[1]))
    if len(candidates) > MAX_FILES_PER_FOLDER:
        for _, rel, _p in candidates[MAX_FILES_PER_FOLDER:]:
            pf.skipped.append(
                {"file": rel, "reason": f"单个文件夹文件过多，只读前 {MAX_FILES_PER_FOLDER} 个"}
            )
        candidates = candidates[:MAX_FILES_PER_FOLDER]

    chunks: list[str] = []
    code_budget = MAX_CODE_CHARS_PER_FOLDER
    doc_budget = MAX_DOC_CHARS_PER_FOLDER
    for rank, rel, p in candidates:
        # 0 字节的**代码**文件静默跳过：空的 __init__.py 是脚手架，
        # 既没内容也没问题。空的文档/数据文件仍然要报——那多半是放错了。
        try:
            if p.stat().st_size == 0 and p.suffix.lower() in CODE_SUFFIXES:
                continue
        except OSError:
            pass
        text, enc, why = read_file(p)
        if text.strip():
            is_code = p.suffix.lower() in CODE_SUFFIXES
            if is_code:
                if code_budget <= 0:
                    pf.skipped.append(
                        {"file": rel, "reason": "代码摘要预算已用完（前面的文件更值得读），这份没读"}
                    )
                    continue
                if len(text) > code_budget:
                    text = text[:code_budget] + "\n[代码摘要预算用尽]"
                code_budget -= len(text)
            elif rank >= 1:
                if doc_budget <= 0:
                    pf.skipped.append(
                        {"file": rel, "reason": "文档预算已用完（根目录 README 优先），这份没读"}
                    )
                    continue
                if len(text) > doc_budget:
                    text = text[:doc_budget] + "\n[文档预算用尽]"
                doc_budget -= len(text)
            pf.files.append(rel)
            if enc:
                pf.encodings[rel] = enc
            body = text.strip()
            if len(body) > MAX_CHARS_PER_FILE:
                body = body[:MAX_CHARS_PER_FILE] + f"\n[本文件超过 {MAX_CHARS_PER_FILE} 字，已截断]"
            chunks.append(f"\n\n===== 文件：{rel} =====\n{body}")
        else:
            pf.skipped.append({"file": rel, "reason": why or "没有读到文字"})

    full = "".join(chunks).strip()
    # 目录树放最前面：模块怎么划分（Django 的 app、bert/ 子包）本身就是技术信息，
    # 而且它很短，截断时不会被砍掉。
    tree = _structure_tree(folder, all_files) if len(all_files) > 4 else ""
    if tree:
        full = f"===== 项目结构 =====\n{tree}\n\n{full}"
    if len(full) > settings.max_project_chars:
        full = full[: settings.max_project_chars] + "\n\n[内容已截断]"
        pf.truncated = True
    pf.text = full
    return pf


def scan_all(base: Path | None = None) -> list[ProjectFolder]:
    base = base or PROJECTS_DIR
    if not base.exists():
        return []
    out: list[ProjectFolder] = []
    for child in sorted(base.iterdir()):
        if not child.is_dir() or child.name in SKIP_DIRS or child.name.startswith("."):
            continue
        out.append(scan_folder(child))
    return out
