"""用**真实开源仓库**当简历资料，跑完整链路（真实 API）。

和 e2e_real / e2e_folders 的区别：
- e2e_real 的资料是我按「刁难」编的
- e2e_folders 的资料是按「像真人」编的
- 这个脚本的资料是**从 GitHub 下载的真实仓库**：README 是作者写的原话，
  代码有几千行，数据文件是分词结果和 IDF 表，README 里的数字是真实成绩

为什么要这么做：编的资料里，数字是干净的、结构是对的、没有第三方库。
真实仓库反过来——README 只有几百字，九成内容是代码，还混着 vendored 的 BERT 源码。
第一轮跑下来暴露的问题（代码全被丢掉、IDF 值污染数字池、一个项目抽出四条重复证据）
全都是编的资料测不出来的。

跑法：
    ./.venv/Scripts/python.exe tests/e2e_opensource.py          # 用已有缓存
    ./.venv/Scripts/python.exe tests/e2e_opensource.py --refetch  # 重新下载仓库
"""

from __future__ import annotations

import os
import shutil
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SB = ROOT / "tests" / "sandbox" / "opensource"
PROJ = SB / "workspace" / "projects"
FIXTURES = ROOT / "tests" / "fixtures" / "real"

os.environ["RESUME_ASSISTANT_DATA"] = str(SB / "data")
os.environ["RESUME_ASSISTANT_WORKSPACE"] = str(SB / "workspace")
os.environ.pop("RESUME_ASSISTANT_MOCK", None)

sys.path.insert(0, str(ROOT))

from resume_assistant import pipeline  # noqa: E402
from resume_assistant.compose.ir import _collapse_same_project  # noqa: E402
from resume_assistant.kb.builder import load_all_evidence  # noqa: E402
from resume_assistant.kb.scanner import scan_all  # noqa: E402

# 真实招聘 JD（来源见 README 里的说明，均为官方招聘页面原文）
JOBS = [
    ("internship", "jd_腾讯_推荐算法方向.txt", "腾讯", "算法-推荐算法方向"),
    ("internship", "jd_趣丸_推荐算法工程师.txt", "广州趣丸网络", "推荐算法工程师"),
    ("internship", "jd_达卯_算法实习生.txt", "上海达卯科技", "算法实习生"),
    ("internship", "jd_虹星_计算机视觉算法.txt", "天津中科虹星", "计算机视觉算法工程师"),
]

PASS: list[str] = []
FAIL: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(("  ok   " if cond else "  FAIL ") + name + (f"  -> {detail}" if detail and not cond else ""))


def hr(title: str) -> None:
    print("\n" + "=" * 70)
    print(title)
    print("=" * 70)


def main() -> int:
    t0 = time.time()
    if not PROJ.exists() or not any(PROJ.iterdir()):
        print("没找到项目文件夹，先跑：./.venv/Scripts/python.exe tests/sandbox/gh_fetch.py")
        return 1

    SB.joinpath("data").mkdir(parents=True, exist_ok=True)
    shutil.copy(ROOT / "data" / "profile.example.yaml", SB / "data" / "profile.yaml")
    # 岗位 id 是内容指纹，不清库的话上一轮的投递停在终点，这轮状态机必然非法转移。
    # 证据缓存存在 data/evidence/，删库不会重抽。
    (SB / "data" / "resume.db").unlink(missing_ok=True)

    # ------------------------------------------------------------ 1. 扫描
    hr("1. 扫描真实仓库")
    folders = scan_all()
    check("扫描到 5 个项目文件夹", len(folders) == 5, str(len(folders)))
    for pf in folders:
        check(
            f"{pf.name} 有可读内容",
            bool(pf.text.strip()),
            f"文件 {len(pf.files)} 个，跳过 {len(pf.skipped)} 个",
        )
    total = sum(len(pf.text) for pf in folders)
    print(f"\n合计 {total} 字符；文件构成：")
    for pf in folders:
        code = sum(1 for f in pf.files if Path(f).suffix.lower() in {".py", ".java", ".js"})
        print(f"  {pf.name:22s} {len(pf.files):2d} 个文件（其中代码 {code}）{len(pf.text):6d} 字")

    # 真实仓库的核心断言：代码不能整份被丢掉。
    # 这些项目 README 只有 300~4000 字，光靠 README 写不出三条 bullet。
    code_read = sum(
        1 for pf in folders for f in pf.files if Path(f).suffix.lower() in {".py", ".java", ".js"}
    )
    check("读到了代码文件（而不是只有 README）", code_read >= 10, f"只读到 {code_read} 个代码文件")

    # 数据文件不该进正文：IDF 表几万行数字会把预算吃光，还会污染数字池
    dumped = [s for pf in folders for s in pf.skipped if "数据文件" in s["reason"]]
    check("识别出数据文件并跳过", len(dumped) >= 3, f"只识别出 {len(dumped)} 个")

    # 没有项目因为超长被截断（预算控制生效）
    truncated = [pf.name for pf in folders if pf.truncated]
    check("没有项目被截断", not truncated, str(truncated))

    # ------------------------------------------------------------ 2. 抽取
    hr("2. 证据抽取（真实 API）")
    t = time.time()
    rep = pipeline.build_knowledge_base(force=False)
    print(f"耗时 {time.time() - t:.1f}s | 新建 {len(rep['created'])} | 缓存命中 {len(rep.get('skipped', []))}")
    check("抽取无报错", not rep["errors"], str(rep["errors"][:2]))

    evs = load_all_evidence()
    check("证据数 >= 10（真实资料足够拆多条）", len(evs) >= 10, f"只有 {len(evs)} 条")

    # 时间：文件夹名带年份，period 不该为空
    no_period = [e.id for e in evs if not (e.period or "").strip()]
    check("文件夹名带年份的证据都有 period", not no_period, str(no_period[:3]))

    # 年份必须和文件夹名一致（曾经把 BERT 论文的 2018/2019 当成项目时间）
    wrong_year = []
    for e in evs:
        folder = Path(e.source_dir).name if e.source_dir else ""
        if len(folder) >= 4 and folder[:4].isdigit() and e.period[:4].isdigit():
            if folder[:4] != e.period[:4]:
                wrong_year.append(f"{folder} -> {e.period}")
    check("period 的年份与文件夹名一致", not wrong_year, str(wrong_year[:3]))

    # 数字池不该被数据文件灌满
    noisy = [e.id for e in evs if len(e.raw_numbers) > 120]
    check("数字池没有被数据文件灌爆（<=120 个）", not noisy, str(noisy[:3]))
    stamps = [
        n for e in evs for n in e.raw_numbers if len(n) > 10
    ]
    check("数字池里没有时间戳这类超长数字", not stamps, str(stamps[:3]))

    # ------------------------------------------------------------ 3. 折叠
    hr("3. 同一项目折叠（简历上一个项目只占一格）")
    kept = _collapse_same_project(evs)
    print(f"{len(evs)} 条证据 -> 进简历 {len(kept)} 条")
    check("折叠后每项目恰好一条", len(kept) == len({e.source_dir for e in kept}))
    for e in kept:
        print(f"  [{e.period}] {e.title}")

    # ------------------------------------------------------------ 4. 岗位
    hr("4. 录入真实招聘 JD")
    job_ids = []
    for kind, fname, org, title in JOBS:
        text = (FIXTURES / fname).read_text(encoding="utf-8")
        t = time.time()
        res = pipeline.add_job(kind=kind, raw_text=text, org=org, title=title)
        if not res.get("ok"):
            check(f"录入 {title}", False, str(res.get("error")))
            continue
        j = res["job"]
        job_ids.append((j["id"], title))
        print(f"  {title}  {time.time() - t:.1f}s | 要求 {len(j['requirements'])} 条 | 关键词 {len(j['keywords'])} 个")
    check("4 个岗位全部录入", len(job_ids) == 4, str(len(job_ids)))

    # ------------------------------------------------------------ 5. 匹配
    hr("5. 匹配")
    for job_id, title in job_ids:
        t = time.time()
        m = pipeline.run_match(job_id, refresh=True)
        if not m.get("ok"):
            check(f"匹配 {title}", False, str(m.get("error")))
            continue
        r = m["report"]
        print(f"\n【{title}】{time.time() - t:.1f}s | 命中 {len(r['hits'])} 条")
        for e in r.get("eligibility") or []:
            print(f"   [{e['level']}] {e['requirement']} — {e['detail']}")
        for h in r["hits"][:4]:
            print(f"   {h['score']:5.1f}  {h['title']}")
        if r["gaps"]:
            print("   缺口: " + " / ".join(r["gaps"][:4]))
        check(f"{title} 有命中", bool(r["hits"]), "0 条命中")

    # ------------------------------------------------------------ 6. 生成
    hr("6. 简历生成")
    for job_id, title in job_ids:
        t = time.time()
        try:
            g = pipeline.generate_resume(job_id, template_key="single_column")
        except Exception:  # noqa: BLE001
            check(f"生成 {title}", False, "抛异常")
            traceback.print_exc()
            continue
        if not g.get("ok"):
            check(f"生成 {title}", False, str(g.get("lint", {}).get("errors", [])[:3]))
            continue
        lint = g["lint"]
        pages = g.get("pages", "-")
        print(f"  {title}  {time.time() - t:.1f}s | bullet {lint['stats']['bullets']} | "
              f"错误 {lint['stats']['errors']} | 提示 {lint['stats']['warnings']} | PDF {pages} 页")
        for w in lint["warnings"][:3]:
            print(f"     ! {w}")
        check(f"{title} 生成无错误", lint["stats"]["errors"] == 0, str(lint["errors"][:3]))
        check(f"{title} PDF 不超过 1 页", pages in (1, "-"), f"{pages} 页")

        # 简历里同一项目不该出现两次（真实项目常被拆成多条证据）
        import yaml as _yaml

        ir = _yaml.safe_load(Path(g["ir_path"]).read_text(encoding="utf-8")) or {}
        headings = [
            ent.get("heading", "")
            for sec in ir.get("sections", [])
            for ent in sec.get("entries", [])
            if ent.get("heading")  # 技能段的条目没有标题，别把空串算成重复
        ]
        dup = [h for h in set(headings) if headings.count(h) > 1]
        check(f"{title} 没有重复条目", not dup, str(dup[:3]))

    # ------------------------------------------------------------ 汇总
    print("\n" + "=" * 70)
    print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项，耗时 {time.time() - t0:.0f}s")
    if FAIL:
        for f in FAIL:
            print("  FAIL " + f)
    print("=" * 70)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
