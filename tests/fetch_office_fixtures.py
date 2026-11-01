"""把真实 Office 文件下载到 tests/fixtures/office/。

这些文件已经提交在仓库里，正常跑测试不需要执行本脚本。
留着它有两个用处：
1. fixture 被误删或损坏时能重新拉回来；
2. 想换一批样本（比如换个学校的简历模板）时，改下面的列表就行。

来源与选择理由见 tests/fixtures/office/README.md。

运行：
    ./.venv/Scripts/python.exe tests/fetch_office_fixtures.py
"""

from __future__ import annotations

import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEST = ROOT / "tests" / "fixtures" / "office"

# (仓库, 分支, 仓库内路径, 存成什么名字)
SOURCES = [
    # 2132 套真实中文 Word 简历模板。选「研究生复试」和「表格简历」两类：
    # 前者整页内容在文本框里，后者是一个 96 格大表格，正好覆盖两种最坑的结构。
    (
        "HICV-CN/hicv-word-resume-templates",
        "main",
        "templates/12_研究生复试/复试单页001.docx",
        "研究生复试-001.docx",
    ),
    (
        "HICV-CN/hicv-word-resume-templates",
        "main",
        "templates/12_研究生复试/复试单页004.docx",
        "研究生复试-004.docx",
    ),
    (
        "HICV-CN/hicv-word-resume-templates",
        "main",
        "templates/01_表格简历/单页/单页表格简历 01.docx",
        "表格简历-单页01.docx",
    ),
    # 真实答辩 PPT，目录页整块打包在一个组合形状里。
    (
        "zouchenzhen/thesis-defense-pptx-skill",
        "main",
        "examples/templates/zhengzhou-university-midterm-blank-template.pptx",
        "答辩模板-郑大中期.pptx",
    ),
    # 天津大学物理实验的真实数据处理表，有几十上百个公式。
    ("jingwei-junko/-excel-", "main", "声速.xlsx", "物理实验-声速.xlsx"),
    ("jingwei-junko/-excel-", "main", "磁滞回线.xlsx", "物理实验-磁滞回线.xlsx"),
    ("jingwei-junko/-excel-", "main", "迈克尔逊干涉仪.xlsx", "物理实验-迈克尔逊.xlsx"),
]


def download(repo: str, branch: str, path: str, name: str) -> bool:
    url = f"https://raw.githubusercontent.com/{repo}/{branch}/" + urllib.parse.quote(path)
    target = DEST / name
    for attempt in range(5):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "resume-assistant-test"})
            raw = urllib.request.urlopen(req, timeout=90).read()
        except Exception as exc:  # noqa: BLE001
            # GitHub 的 raw 偶尔 SSL 断连（UNEXPECTED_EOF / RemoteDisconnected），
            # 退避一下再试基本能过；连续失败才放弃。
            if attempt == 4:
                print(f"  FAIL {name}: {type(exc).__name__}: {exc}")
                return False
            time.sleep(2 * (attempt + 1))
            continue
        target.write_bytes(raw)
        print(f"  ok   {name:28s} {len(raw) / 1024:6.0f}KB")
        return True
    return False


def main() -> int:
    DEST.mkdir(parents=True, exist_ok=True)
    print(f"下载到 {DEST}")
    failed = [name for repo, br, path, name in SOURCES if not download(repo, br, path, name)]
    print(f"\n完成：{len(SOURCES) - len(failed)}/{len(SOURCES)}")
    if failed:
        print("失败：", failed)
        return 1
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass
    raise SystemExit(main())
