"""知识库扫描：把「一个项目一个文件夹」的资料读成纯文本。

支持 .md .txt .pdf .docx .csv；图片/压缩包 P0 阶段跳过（后续接 OCR）。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

from ..config import PROJECTS_DIR, settings

TEXT_SUFFIXES = {".md", ".markdown", ".txt", ".rst", ".csv", ".json", ".yaml", ".yml"}
SKIP_DIRS = {".git", "__pycache__", ".venv", "node_modules", ".idea", ".vscode"}
MAX_PDF_PAGES = 40


@dataclass
class ProjectFolder:
    name: str
    path: Path
    text: str = ""
    files: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    truncated: bool = False

    @property
    def fingerprint(self) -> str:
        h = hashlib.sha1()
        for p in sorted(self.path.rglob("*")):
            if p.is_file() and not any(part in SKIP_DIRS for part in p.parts):
                h.update(p.name.encode("utf-8", "ignore"))
                h.update(str(int(p.stat().st_mtime)).encode())
                h.update(str(p.stat().st_size).encode())
        return h.hexdigest()[:16]


def _read_pdf(path: Path) -> str:
    try:
        import pdfplumber
    except ImportError:
        return ""
    out: list[str] = []
    try:
        with pdfplumber.open(str(path)) as pdf:
            for page in pdf.pages[:MAX_PDF_PAGES]:
                out.append(page.extract_text() or "")
    except Exception:  # noqa: BLE001
        return ""
    return "\n".join(out)


def _read_docx(path: Path) -> str:
    try:
        import docx
    except ImportError:
        return ""
    try:
        doc = docx.Document(str(path))
    except Exception:  # noqa: BLE001
        return ""
    parts = [p.text for p in doc.paragraphs if p.text.strip()]
    for table in doc.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells if c.text.strip()]
            if cells:
                parts.append(" | ".join(cells))
    return "\n".join(parts)


def read_file(path: Path) -> str:
    suffix = path.suffix.lower()
    try:
        if suffix in TEXT_SUFFIXES:
            return path.read_text(encoding="utf-8", errors="ignore")
        if suffix == ".pdf":
            return _read_pdf(path)
        if suffix in {".docx", ".doc"}:
            return _read_docx(path)
    except Exception:  # noqa: BLE001
        return ""
    return ""


def scan_folder(folder: Path) -> ProjectFolder:
    pf = ProjectFolder(name=folder.name, path=folder)
    chunks: list[str] = []
    for p in sorted(folder.rglob("*")):
        if not p.is_file():
            continue
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        text = read_file(p)
        rel = str(p.relative_to(folder))
        if text.strip():
            pf.files.append(rel)
            chunks.append(f"\n\n===== 文件：{rel} =====\n{text.strip()}")
        else:
            pf.skipped.append(rel)

    full = "".join(chunks).strip()
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
