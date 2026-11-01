"""把真实 GitHub 仓库下载成「项目文件夹」，模拟学生直接丢整个项目目录。

选的都是真实的学生项目（课程设计 / 毕设），不是热门大库：
README 只有几百字、九成内容是代码，这才是真实资料的常态。

用法：
    ./.venv/Scripts/python.exe tests/gh_fetch.py
下载到 tests/sandbox/opensource/workspace/projects/，再跑 e2e_opensource.py。
"""

import io
import shutil
import tarfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SB = ROOT / "tests" / "sandbox" / "opensource"
PROJ = SB / "workspace" / "projects"

REPOS = [
    ("rongdisun/BookRecommend", "2024-图书推荐系统"),
    ("Muci567/BookRec_ALS_ItemCF_Competition", "2025-图书推荐竞赛"),
    ("heyanLE/GenshinBookRecommend", "2024-原神书籍文本挖掘"),
    ("Wangxianke123/Improving-methods-for-small-objects-detection", "2025-小目标检测优化"),
    ("WenRichard/KBQA-BERT", "2023-知识图谱问答"),
]

if PROJ.exists():
    shutil.rmtree(PROJ)
PROJ.mkdir(parents=True, exist_ok=True)

for repo, name in REPOS:
    ok = False
    for br in ("main", "master"):
        url = f"https://codeload.github.com/{repo}/tar.gz/refs/heads/{br}"
        try:
            raw = urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "x"}), timeout=90).read()
            ok = True
            break
        except Exception as e:
            last = e
    if not ok:
        print(f"  跳过 {repo}: {last}"); continue
    dest = PROJ / name
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tf:
        members = tf.getmembers()
        prefix = members[0].name.split("/")[0] + "/"
        for m in members:
            if not m.isfile():
                continue
            rel = m.name[len(prefix):]
            if not rel or "/.git/" in "/" + rel:
                continue
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                target.write_bytes(tf.extractfile(m).read())
            except Exception:
                pass
    n = sum(1 for _ in dest.rglob("*") if _.is_file())
    sz = sum(f.stat().st_size for f in dest.rglob("*") if f.is_file())
    print(f"  ok  {name:22s} 文件 {n:3d}  {sz/1024:7.0f}KB   来自 {repo}")
