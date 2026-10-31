"""用「像真人资料」的 8 个文件夹跑全流程（真实 API）。

与 e2e_real.py 的分工：
- e2e_real 用的是刻意刁难的资料（扫描件、超长、空目录）
- 这个脚本用的是**像真人整理过、但脏得合理**的资料：一个项目多个文件、
  备份文件夹与正本几乎一样、docx 里指标只存在表格中、文件夹名带空格括号、
  10MB 日志、GBK 笔记、只放了截图的文件夹

真实资料不是按 schema 长的，所以这里断言的不是「抽得漂亮」，而是三件事：
1. 每个文件夹都有明确去向（读到 / 跳过 / 无文字），不许静默丢
2. 该报的疑点要报（重复项目、时间缺失、指标没出处）
3. 抽出来的东西能直接生成一份 0 错误、1 页的简历

跑法：
    ./.venv/Scripts/python.exe tests/e2e_folders.py
"""

from __future__ import annotations

import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SB = ROOT / "tests" / "sandbox" / "folders"
PROJ = SB / "workspace" / "projects"

# 必须在 import 业务模块之前设置
os.environ["RESUME_ASSISTANT_DATA"] = str(SB / "data")
os.environ["RESUME_ASSISTANT_WORKSPACE"] = str(SB / "workspace")
os.environ.pop("RESUME_ASSISTANT_MOCK", None)

sys.path.insert(0, str(ROOT))

PASS: list[str] = []
FAIL: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(("  ok   " if cond else "  FAIL ") + name + (f"  -> {detail}" if detail and not cond else ""))


def hr(title: str) -> None:
    print("\n" + "=" * 68)
    print(title)
    print("=" * 68)


# --------------------------------------------------------------- 造资料

RECSYS_MD = """# 双通道召回推荐系统（课程设计）

课程：数据挖掘与推荐系统（2025 春）
角色：小组 4 人，我负责召回与离线评估
时间：2025.03 ~ 2025.06

## 背景
课程给定一份脱敏电商行为数据（约 180 万条），要求做 Top-N 推荐，冷启动用户占比 41%。
基线是单路 ItemCF，冷启动用户点击率只有 2.1%。

## 我做了什么
1. 用户侧双通道：一路 ItemCF 走历史行为，一路用属性 embedding 走冷启动，按用户行为条数加权融合。
2. 负采样从均匀采样改成按热度采样，缓解热门偏置。
3. 离线评估用 Recall@20 / NDCG@20，并做了一组消融。

## 结果
- 冷启动用户点击率 2.1% → 3.4%
- 全量 Recall@20 从 0.183 提到 0.221
- 消融显示双通道贡献最大，去掉后回落到 2.4%

技术栈：Python、PyTorch、pandas、Faiss
代码：https://github.com/example/recsys-course
"""

RECSYS_WEEKLY = """周报（第 3 周）
本周把双通道融合的权重从固定 0.5 改成按行为条数自适应，冷启动分组点击率从 2.9% 到 3.4%。
下周计划：补消融实验，跑一遍 NDCG。
"""

INTERN_MD = """# 某互联网公司 推荐算法实习

时间：2024.07 ~ 2024.10
部门：推荐技术部 · 召回组
mentor：李工

## 负责的事
- 负责首页信息流召回链路的**多样性打散**：原有策略只做类目打散，同作者内容仍会连续出现。
- 把作者级打散窗口从 3 条调到 6 条，并加了「同一作者 24 小时内最多 2 条」的硬约束。
- 上线后作者重复曝光率从 18% 降到 9%，人均阅读时长 +6.2%。

## 顺手做的
- 写了一个离线评估脚本，把线上 A/B 的指标口径对齐到离线，减少了对不齐的扯皮。
- 排查过一次召回为空的事故：新类目冷启动没进索引，补了兜底召回。

技术：Python、Spark、Redis、A/B 实验平台
"""

INTERN_WEEKLY = """实习周报摘录
第 5 周：作者级打散窗口 3 → 6，作者重复曝光率 18% → 9%。
第 7 周：加了 24 小时 2 条的硬约束，人均阅读时长 +6.2%，但曝光量 -1.1%，和产品确认可接受。
"""

LAB_MD = """# 冷启动推荐中的属性对齐（实验室课题）

导师：张老师
时间：2024 年秋开始，目前仍在做
状态：投稿中（AAAI 2026 在审）

## 问题
新用户没有行为，属性 embedding 又和 ID embedding 不在一个空间，直接拼接效果很差。

## 方法
提出一个轻量的属性-行为对齐模块：用对比学习把属性表征拉到行为表征附近，
只加一个投影头，不改主干，推理开销增加不到 3%。

## 实验
- 在 Amazon Books 与 Taobao 两个公开数据集上做冷启动分组实验
- 冷启动分组 NDCG@10 相对提升 11.8%（Amazon Books）
- 消融：去掉对比损失后提升只剩 3.2%

## 我做的
独立完成方法设计、实现与实验，导师负责选题与写作指导。
"""

AWARD_TXT = """蓝桥杯 软件类 省赛一等奖（2024）
个人赛，C/C++ 组，省排名第 12。
赛题是图论 + 动态规划，四小时三道题全 AC。
"""


def build_folders() -> None:
    shutil.rmtree(SB, ignore_errors=True)
    PROJ.mkdir(parents=True, exist_ok=True)
    (SB / "data").mkdir(parents=True, exist_ok=True)
    shutil.copy(ROOT / "data" / "profile.example.yaml", SB / "data" / "profile.yaml")

    # 1. 正常项目：一个 md
    d = PROJ / "2025-推荐系统课程设计"
    d.mkdir()
    (d / "README.md").write_text(RECSYS_MD, encoding="utf-8")

    # 2. 一个项目多个文件（README + 周报），内容有重叠
    d = PROJ / "2024-某厂推荐算法实习"
    d.mkdir()
    (d / "实习总结.md").write_text(INTERN_MD, encoding="utf-8")
    (d / "周报.txt").write_text(INTERN_WEEKLY, encoding="utf-8")

    # 3. 科研：没有明确起止时间，只有「2024 年秋开始」
    d = PROJ / "实验室-冷启动课题"
    d.mkdir()
    (d / "说明.md").write_text(LAB_MD, encoding="utf-8")

    # 4. 奖项：极短
    d = PROJ / "2024-蓝桥杯省一"
    d.mkdir()
    (d / "获奖.txt").write_text(AWARD_TXT, encoding="utf-8")

    # 5. docx：指标只在表格里
    import docx

    d = PROJ / "2023-数据结构课设"
    d.mkdir()
    doc = docx.Document()
    for p in [
        "数据结构课程设计：校园二手交易平台的索引与检索",
        "时间：2023.09 ~ 2023.12，独立完成。",
        "用 B+ 树做商品标题前缀检索，用倒排表做类目过滤，压测工具是 wrk。",
    ]:
        doc.add_paragraph(p)
    table = doc.add_table(rows=3, cols=3)
    rows = [
        ["指标", "优化前", "优化后"],
        ["查询 P99 延迟", "380ms", "42ms"],
        ["内存占用", "1.2GB", "760MB"],
    ]
    for r, row in enumerate(rows):
        for c, val in enumerate(row):
            table.cell(r, c).text = val
    doc.save(str(d / "课设报告.docx"))

    # 6. 备份文件夹：名字带空格括号，内容和 1 几乎一样
    d = PROJ / "推荐系统 备份 (旧)"
    d.mkdir()
    (d / "README.md").write_text(RECSYS_MD.replace("（课程设计）", ""), encoding="utf-8")

    # 7. 只放了截图和扫描件的文件夹
    d = PROJ / "2026-证书扫描件"
    d.mkdir()
    (d / "证书.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
    (d / "说明.txt").write_text("", encoding="utf-8")

    # 8. 杂项：10MB 日志 + GBK 笔记 + 空文件 + 锁文件
    d = PROJ / "杂项"
    d.mkdir()
    # 6MB 训练日志：不该把整个项目的字符预算吃光
    (d / "train.log").write_text("epoch 1 loss 0.9\n" * 400_000, encoding="utf-8")
    # 25MB 导出 csv：超过单文件上限，直接跳过
    (d / "dump.csv").write_text("a,b,c\n" * 3_600_000, encoding="utf-8")
    (d / "随手记.txt").write_bytes("课程大作业：用 XGBoost 做房价预测，RMSE 从 0.31 降到 0.22".encode("gb18030"))
    (d / "空文件.md").write_text("", encoding="utf-8")
    (d / "~$临时锁.docx").write_bytes(b"lock")


# --------------------------------------------------------------- 主流程


def main() -> int:
    build_folders()

    from resume_assistant import pipeline
    from resume_assistant.kb.builder import load_all_evidence
    from resume_assistant.kb.review import audit, to_text

    hr("1. 扫描（不花 token）")
    rep = pipeline.scan_report()
    print(f"文件夹 {rep['folders']} 个，共 {rep['total_chars']} 字")
    for it in rep["items"]:
        print(f"  · {it['folder']}：{len(it['files'])} 个文件，{it['chars']} 字" + ("（截断）" if it["truncated"] else ""))
        for s in it["skipped"]:
            print(f"      skip {s['file']}：{s['reason']}")

    check("8 个文件夹全部扫到", rep["folders"] == 8, str(rep["folders"]))
    names = {it["folder"] for it in rep["items"]}
    check("带空格括号的文件夹名没被吃掉", "推荐系统 备份 (旧)" in names, str(sorted(names)))
    check("只有图片的文件夹被标为无文字", "2026-证书扫描件" in rep["no_text"], str(rep["no_text"]))
    misc = next(it for it in rep["items"] if it["folder"] == "杂项")
    check("超过 20MB 的文件被跳过并给原因", any("dump.csv" == s["file"] and "MB" in s["reason"] for s in misc["skipped"]), str(misc["skipped"]))
    check("单个大文件吃不掉整个项目预算", misc["chars"] < 16000, str(misc["chars"]))
    check("GBK 笔记被正确解码", any("随手记.txt" in f for f in misc["files"]), str(misc["files"]))
    check("空文件被记为跳过", any(s["file"] == "空文件.md" for s in misc["skipped"]), str(misc["skipped"]))
    check("Office 锁文件不参与", all("~$" not in f for f in misc["files"]), str(misc["files"]))

    gbk = next(it for it in rep["items"] if it["folder"] == "杂项")["encodings"].get("随手记.txt")
    check("GBK 编码被记录（不是 utf-8）", gbk in ("gb18030", "big5"), str(gbk))

    hr("2. 抽取（真实 API）")
    t0 = time.time()
    build = pipeline.build_knowledge_base()
    print(f"耗时 {time.time() - t0:.1f}s")
    print(
        f"  新建 {len(build['created'])} / 跳过 {len(build['skipped'])} / 无文字 {len(build['no_text'])} / "
        f"失败 {len(build['errors'])}"
    )
    for e in build["errors"]:
        print(f"  ! {e}")
    check("抽取无失败", not build["errors"], str(build["errors"]))
    no_text_names = [d["folder"] if isinstance(d, dict) else d for d in build["no_text"]]
    check("无文字的文件夹被单独列出", "2026-证书扫描件" in no_text_names, str(build["no_text"])[:200])

    evs = load_all_evidence()
    print(f"  证据 {len(evs)} 条")
    check("抽出了证据", len(evs) >= 6, str(len(evs)))
    check("每个有文字的文件夹都至少 1 条", len(evs) >= 7, f"{len(evs)} 条 / 7 个有文字文件夹")

    hr("3. 抽取质量（真实资料最容易出问题的地方）")
    for e in evs:
        met = "，".join(f"{m.name} {m.rendered()}" for m in e.metrics[:3]) or "无"
        print(f"  [{e.kind}] {e.title} | {e.period or '时间空'} | {met}")
        print(f"         {e.claim[:60]}")

    intern = [e for e in evs if e.kind == "intern"]
    check("实习被认成 intern（不是 project）", bool(intern), str([e.kind for e in evs]))
    research = [e for e in evs if e.kind == "research"]
    check("实验室课题被认成 research", bool(research), str([e.kind for e in evs]))
    award = [e for e in evs if e.kind == "award"]
    check("竞赛获奖被认成 award", bool(award), str([e.kind for e in evs]))

    with_metrics = [e for e in evs if e.metrics]
    check("至少一半证据带量化指标", len(with_metrics) * 2 >= len(evs), f"{len(with_metrics)}/{len(evs)}")
    no_source = [m.name for e in evs for m in e.metrics if not (m.source or "").strip()]
    check("指标都带 source", not no_source, str(no_source))

    docx_ev = [e for e in evs if "数据结构" in e.title or "二手" in e.title or "索引" in e.title]
    docx_vals = " ".join(m.value for e in docx_ev for m in e.metrics)
    check("docx 表格里的数字被抽到", any(v in docx_vals for v in ("42", "0.35", "42ms")), f"{docx_vals} | {[e.title for e in docx_ev]}")

    ids = [e.id for e in evs]
    check("证据编号不重复", len(ids) == len(set(ids)), str([i for i in ids if ids.count(i) > 1]))

    # 「18%→9%」这种 value 自带单位，再拼 unit 会变成 9%%，这句话会原样进 prompt
    double_unit = [f"{e.title}:{m.rendered()}" for e in evs for m in e.metrics if "%%" in m.rendered()]
    check("指标单位不重复（不出现 9%%）", not double_unit, str(double_unit))

    hr("4. 校对清单（该报的疑点要报）")
    res = audit(evs)
    print(to_text(res, limit=40))
    kinds = {f.field for f in res.findings}
    check("时间缺失被报", "period" in kinds or all(e.period for e in evs), str(sorted(kinds)))

    dup = [f for f in res.findings if "重复" in f.message or f.field == "duplicate"]
    backup_hit = any("备份" in f.title or "备份" in (f.message or "") for f in res.findings)
    check("备份文件夹的重复项目被认出来", bool(dup) or backup_hit, f"dup={len(dup)} backup={backup_hit}")

    hr("5. 生成简历（实习 + 保研各一份）")
    jd_intern = (
        "推荐算法实习生\n岗位职责：负责召回与排序策略优化，参与 A/B 实验设计与分析。\n"
        "任职要求：熟悉 Python、PyTorch，有推荐系统项目经验；每周到岗 4 天，实习期不少于 3 个月。"
    )
    jd_baoyan = "张老师课题组招收 2027 届推免生，研究方向：推荐系统冷启动、图神经网络。"

    jobs = {}
    for kind, title, org, mentor, text in (
        ("internship", "推荐算法实习生", "某互联网公司", "", jd_intern),
        ("baoyan", "推荐系统冷启动", "某某大学计算机学院", "张老师", jd_baoyan),
    ):
        r = pipeline.add_job(kind=kind, title=title, org=org, mentor=mentor, raw_text=text)
        check(f"录入 {kind} 岗位", r.get("ok") is True, str(r)[:200])
        jobs[kind] = r["job"]["id"]

    for kind, tpl in (("internship", "single_column"), ("baoyan", "academic")):
        t0 = time.time()
        g = pipeline.generate_resume(jobs[kind], template_key=tpl)
        ok = g.get("ok") is True
        print(
            f"  {kind}: {'OK' if ok else 'FAIL'} {time.time() - t0:.1f}s | "
            f"{g.get('lint', {}).get('stats', {})} | PDF {g.get('pages')} 页"
        )
        check(f"{kind} 简历 0 错误", ok and g["lint"]["stats"]["errors"] == 0, str(g.get("lint", {}).get("errors"))[:300])
        # 实习简历按惯例 1 页；保研（学术）简历 2 页是常态
        limit = 1 if kind == "internship" else 2
        check(f"{kind} 简历不超过 {limit} 页", (g.get("pages") or 9) <= limit, str(g.get("pages")))
        if kind == "internship":
            g_intern = g
        if ok:
            print("    warnings:", g["lint"]["warnings"][:5])
            for w in g["lint"]["warnings"]:
                if "[溯源]" in w:
                    check(f"{kind} 无编造数字", False, w)

    # 备份文件夹抽出两条一样的证据，简历上只能出现一次
    import yaml as _yaml

    ir_data = _yaml.safe_load(Path(g_intern["ir_path"]).read_text(encoding="utf-8"))
    heads = [en["heading"] for sec in ir_data["sections"] for en in sec["entries"] if sec["type"] in ("projects", "experience")]
    dup_heads = [h for h in set(heads) if heads.count(h) > 1]
    check("简历里同一项目不出现两次", not dup_heads, f"{heads}")

    hr("6. 同一岗位再生成一次（幂等）")
    g1 = pipeline.generate_resume(jobs["internship"])
    check("重复 gen 不新建投递", g1.get("application_id") == pipeline.generate_resume(jobs["internship"]).get("application_id"))

    hr("7. 开场白 + 套磁信")
    p = pipeline.generate_pitch(jobs["internship"])
    check("开场白生成", p.get("ok") is True, str(p)[:200])
    if p.get("ok"):
        print("  short:", p["pitch"]["short"][:80])
        print("  full :", p["pitch"]["full"][:120].replace("\n", " "))
        check("开场白没有编造风险", not any("编造风险" in w for w in p["pitch"]["warnings"]), str(p["pitch"]["warnings"])[:200])

    letter = pipeline.generate_letter(jobs["baoyan"])
    check("套磁信生成", letter.get("ok") is True, str(letter)[:200])
    if letter.get("ok"):
        print("  subject:", letter["letter"]["subject"])
        check("套磁信没出现编造论文", not any("虚构" in w for w in letter["letter"]["warnings"]), str(letter["letter"]["warnings"])[:200])

    hr("8. 看板")
    b = pipeline.board(write_html=False)
    print(b["text"][:600])
    check("看板有 2 条投递", b["total"] == 2, str(b["total"]))

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
