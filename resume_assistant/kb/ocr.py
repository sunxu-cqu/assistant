"""扫描件 OCR：可选引擎，装了才用。

P0 只读文字层，扫描件直接告诉用户「读不到」。可真实资料里证书、成绩单、盖章证明
几乎全是扫描件——这条提示等于让人把内容手打一遍。

引擎按「装得上」排序：
1. rapidocr-onnxruntime：纯 onnx，一个 pip 包就能跑，CPU 上每页 1-2 秒
2. paddleocr：识别率略高，但要拖 paddle 全家桶
两个都没有就返回 None，scanner 保留原来的提示，不报错也不降速。

限制：只 OCR 前 MAX_OCR_PAGES 页、单页截到 MAX_OCR_CHARS 字。
扫描件动辄几十页，全跑一遍要等半分钟，而简历只关心第一页的关键信息。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

MAX_OCR_PAGES = int(os.environ.get("RESUME_ASSISTANT_OCR_PAGES", "5"))
MAX_OCR_CHARS = 4000

_ENGINE: Any = None
_NAME = ""
_ERR = ""
_TRIED = False


def _load_engine() -> Any:
    global _ENGINE, _NAME, _ERR, _TRIED
    if _ENGINE is not None or _TRIED:
        return _ENGINE
    _TRIED = True
    try:
        from rapidocr_onnxruntime import RapidOCR

        _ENGINE = RapidOCR()
        _NAME = "rapidocr"
        return _ENGINE
    except Exception:  # noqa: BLE001
        pass
    try:
        from paddleocr import PaddleOCR

        _ENGINE = PaddleOCR(use_angle_cls=False, lang="ch", show_log=False)
        _NAME = "paddleocr"
        return _ENGINE
    except Exception as exc:  # noqa: BLE001
        _ERR = f"未安装 OCR 引擎（{type(exc).__name__}）"
        _ENGINE = None
        return None


def available() -> tuple[bool, str]:
    engine = _load_engine()
    if engine is None:
        return False, _ERR or "未启用"
    return True, _NAME


def _ocr_pil(img: Any) -> str:
    """PIL.Image → 文本。两种引擎的调用方式不一样，这里抹平。"""
    engine = _load_engine()
    if engine is None:
        return ""
    try:
        import numpy as np
    except Exception:  # noqa: BLE001
        return ""
    arr = np.array(img.convert("RGB"))
    try:
        if _NAME == "rapidocr":
            res, _ = engine(arr)
            if not res:
                return ""
            return "\n".join(str(line[1]) for line in res if len(line) > 1)
        # paddleocr
        res = engine.ocr(arr, cls=False)
        lines: list[str] = []
        for block in res or []:
            for item in block or []:
                if isinstance(item, (list, tuple)) and len(item) >= 2 and isinstance(item[1], (list, tuple)):
                    lines.append(str(item[1][0]))
        return "\n".join(lines)
    except Exception:  # noqa: BLE001
        return ""


def read_image(path: Path) -> tuple[str, str]:
    """图片 → (文本, 失败原因)。"""
    if _load_engine() is None:
        return "", f"图片没有文字层，且{_ERR or '未启用 OCR'}（重要内容请手动写进 md）"
    try:
        from PIL import Image
    except Exception:  # noqa: BLE001
        return "", "缺少 Pillow，无法读取图片"
    try:
        with Image.open(str(path)) as img:
            text = _ocr_pil(img)
    except Exception as exc:  # noqa: BLE001
        return "", f"图片打不开：{type(exc).__name__}"
    text = text.strip()
    if not text:
        return "", "OCR 没识别出文字（图可能太小或全是图形）"
    return text[:MAX_OCR_CHARS], ""


def read_pdf(path: Path) -> tuple[str, str]:
    """扫描件 PDF → (文本, 失败原因)。需要 pypdfium2 渲染页面。"""
    if _load_engine() is None:
        return "", f"PDF 没有文字层（扫描件），且{_ERR or '未启用 OCR'}"
    try:
        import pypdfium2 as pdfium
    except Exception:  # noqa: BLE001
        return "", "缺少 pypdfium2，无法把 PDF 渲染成图片做 OCR"
    try:
        from PIL import Image  # noqa: F401
    except Exception:  # noqa: BLE001
        return "", "缺少 Pillow，无法做 OCR"

    try:
        doc = pdfium.PdfDocument(str(path))
    except Exception as exc:  # noqa: BLE001
        return "", f"PDF 打开失败：{type(exc).__name__}"

    pages: list[str] = []
    try:
        total = min(len(doc), MAX_OCR_PAGES)
        for i in range(total):
            try:
                bitmap = doc[i].render(scale=2.0)
                img = bitmap.to_pil()
                text = _ocr_pil(img).strip()
                if text:
                    pages.append(text)
            except Exception:  # noqa: BLE001
                continue
    finally:
        try:
            doc.close()
        except Exception:  # noqa: BLE001
            pass

    if not pages:
        return "", "OCR 没识别出文字（扫描件可能太模糊）"
    text = "\n".join(pages)
    if len(text) > MAX_OCR_CHARS:
        text = text[:MAX_OCR_CHARS] + "\n[OCR 结果过长，已截断]"
    return text, ""


def info() -> dict[str, Any]:
    ok, why = available()
    return {
        "enabled": ok,
        "engine": why,
        "max_pages": MAX_OCR_PAGES,
        "hint": (
            f"已启用 OCR（{why}），扫描件会按前 {MAX_OCR_PAGES} 页识别"
            if ok
            else "扫描件/截图会读不到。要启用：pip install rapidocr-onnxruntime"
        ),
    }


def reset() -> None:
    global _ENGINE, _NAME, _ERR, _TRIED
    _ENGINE, _NAME, _ERR, _TRIED = None, "", "", False
