"""真实 Office 文件测试：拿从公开渠道下载的 docx / pptx / xlsx 验证解析器。

这些文件不是 mock 生成的——里面有文本框、合并单元格、组合形状、17 位浮点。
用 python-docx 自己写出来的 mock 文件一个都造不出来，所以之前所有 Office 相关
的测试都是「看着通过、真实文件上全崩」。

文件来源见 tests/fixtures/office/README.md。

不消耗 API 额度（只读文件，不调模型）。

运行：
    ./.venv/Scripts/python.exe tests/office_real.py
"""

from __future__ import annotations

import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures" / "office"
sys.path.insert(0, str(ROOT))

from resume_assistant.kb.scanner import (  # noqa: E402
    _read_docx,
    _read_pptx,
    _read_xlsx,
)

PASS: list[str] = []
FAIL: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(("  ok   " if cond else "  FAIL ") + name + (f"  -> {detail}" if detail and not cond else ""))


def _read(name: str) -> tuple[str, str]:
    path = FIXTURES / name
    if name.endswith(".docx"):
        return _read_docx(path)
    if name.endswith(".pptx"):
        return _read_pptx(path)
    return _read_xlsx(path)


def main() -> int:
    missing = [p.name for p in [
        FIXTURES / "研究生复试-001.docx",
        FIXTURES / "研究生复试-004.docx",
        FIXTURES / "表格简历-单页01.docx",
        FIXTURES / "答辩模板-郑大中期.pptx",
        FIXTURES / "物理实验-声速.xlsx",
    ] if not p.exists()]
    if missing:
        print("缺少 fixture：", missing)
        print("这些文件要从 tests/fixtures/office/README.md 里写的地址下载")
        return 2

    # ---------------------------------------------------------------- docx 文本框
    print("\n[1] docx：文本框里的正文")
    text, why = _read("研究生复试-001.docx")
    check("文本框简历不再是 0 字", len(text) >= 800, f"{len(text)} 字 | why={why}")
    check("读到文本框里的姓名", "小豆" in text, text[:120])
    check("读到文本框里的专业", "市场营销" in text, text[:120])
    check("读到文本框里的实习单位", "华纳科技" in text or "信息科技" in text, text[:200])

    text4, why4 = _read("研究生复试-004.docx")
    check("第二份文本框简历读到 ≥800 字", len(text4) >= 800, f"{len(text4)} 字 | why={why4}")
    check("读到文本框里的奖项", "国家奖学金" in text4, text4[:200])
    check("读到文本框里的技能", "Photoshop" in text4 or "Excel" in text4, text4[:200])

    # ---------------------------------------------------------------- docx 合并单元格
    print("\n[2] docx：合并单元格不重复")
    table_text, _ = _read("表格简历-单页01.docx")
    dup = re.findall(r"([^|\n]{2,20}) \| \1(?:\s*\| \1)+", table_text)
    check("没有连续重复的单元格", not dup, str(dup[:4]))
    check("合并行还原成「标签 | 值」", "姓名 | 小豆" in table_text, table_text[:200])
    check("去重后字数落在合理区间", 400 <= len(table_text) <= 1600, f"{len(table_text)} 字")

    # ---------------------------------------------------------------- docx 结构
    print("\n[3] docx：小标题标记")
    check("表格简历识别出小标题", "## 个" in table_text or "## 技能" in table_text, table_text[:200])
    heads = [ln for ln in text4.splitlines() if ln.startswith("## ")]
    check("第二份简历识别出 ≥3 个小标题", len(heads) >= 3, str(heads[:6]))
    check("小标题里包含「教育背景」", any("教育背景" in h for h in heads), str(heads[:6]))

    print("\n[4] docx：页眉页脚")
    check("读到页脚", "[页脚]" in table_text, table_text[-120:])

    # ---------------------------------------------------------------- pptx
    print("\n[5] pptx：组合形状与分页")
    ppt, why_p = _read("答辩模板-郑大中期.pptx")
    check("pptx 读到了内容", len(ppt) >= 600, f"{len(ppt)} 字 | why={why_p}")
    check("组合形状里的目录读到了", "系统框图" in ppt and "算法设计" in ppt, ppt[:300])
    check("有分页标记", "[第 1 页]" in ppt and "[第 3 页]" in ppt, ppt[:120])

    # ---------------------------------------------------------------- xlsx
    print("\n[6] xlsx：精度与序号")
    xls, why_x = _read("物理实验-声速.xlsx")
    check("xlsx 读到了内容", len(xls) >= 500, f"{len(xls)} 字 | why={why_x}")
    long_float = re.findall(r"\d+\.\d{10,}", xls)
    check("没有 17 位浮点（会污染数字池）", not long_float, str(long_float[:3]))
    check("序号列没变成 1.0 / 2.0", "1.0 | 2.0" not in xls, xls[:200])
    check("保留真实测量值", "32.36" in xls, xls[:260])
    check("整数结果没有多余的 .0", "101500.0" not in xls, xls[:200])

    xls2, _ = _read("物理实验-磁滞回线.xlsx")
    check("第二份 xlsx 读到了内容", len(xls2) >= 500, f"{len(xls2)} 字")

    # ---------------------------------------------------------------- 合成：合并单元格
    print("\n[7] xlsx：合并单元格回填（合成文件）")
    # 真实的学生 Excel 里合并单元格很常见（成绩单的「姓名」占两列），
    # 但公开渠道找不到合适样本，这里用 openpyxl 现场造一个，只验证回填逻辑本身。
    try:
        import openpyxl

        tmp = Path(tempfile.mkdtemp()) / "merged.xlsx"
        wb = openpyxl.Workbook()
        ws = wb.active
        ws["A1"] = "姓名"
        ws.merge_cells("A1:B1")
        ws["C1"] = "小豆"
        ws["A2"] = "专业"
        ws.merge_cells("A2:B2")
        ws["C2"] = "市场营销"
        wb.save(str(tmp))
        merged_text, why_m = _read_xlsx(tmp)
        check("合并单元格的标签没丢", "姓名" in merged_text and "专业" in merged_text, f"{merged_text!r} | why={why_m}")
        check("合并不制造重复值", merged_text.count("姓名") == 1, merged_text)
        check("值没被合并冲掉", "小豆" in merged_text and "市场营销" in merged_text, merged_text)
    except ImportError:
        print("  (跳过：没装 openpyxl)")

    # ---------------------------------------------------------------- 坏文件
    print("\n[8] 坏文件不崩")
    bad = Path(tempfile.mkdtemp()) / "broken.docx"
    bad.write_bytes(b"not a real docx")
    text_b, why_b = _read_docx(bad)
    check("损坏的 docx 返回空文本", text_b == "", repr(text_b))
    check("损坏的 docx 给了原因", bool(why_b), repr(why_b))

    fake_ppt = Path(tempfile.mkdtemp()) / "broken.pptx"
    fake_ppt.write_bytes(b"nope")
    text_p, why_p2 = _read_pptx(fake_ppt)
    check("损坏的 pptx 给了原因", text_p == "" and bool(why_p2), repr(why_p2))

    fake_xls = Path(tempfile.mkdtemp()) / "broken.xlsx"
    fake_xls.write_bytes(b"nope")
    text_x, why_x2 = _read_xlsx(fake_xls)
    check("损坏的 xlsx 给了原因", text_x == "" and bool(why_x2), repr(why_x2))

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
