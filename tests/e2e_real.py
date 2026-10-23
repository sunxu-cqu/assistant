"""真实 API 端到端测试（沙箱隔离，不碰真实 data/）。

跑法：
    ./.venv/Scripts/python.exe tests/e2e_real.py

流程：10 个刁难资料 → 抽取 → 4 个岗位（2 实习 + 2 保研）→ 匹配 → 生成 → 状态机
"""

from __future__ import annotations

import os
import shutil
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SANDBOX = ROOT / "tests" / "sandbox"
FIXTURES = ROOT / "tests" / "fixtures"

# 必须在 import 业务模块之前设置
os.environ["RESUME_ASSISTANT_DATA"] = str(SANDBOX / "data")
os.environ["RESUME_ASSISTANT_WORKSPACE"] = str(SANDBOX / "workspace")

sys.path.insert(0, str(ROOT))

from resume_assistant import pipeline  # noqa: E402
from resume_assistant.kb.builder import load_all_evidence  # noqa: E402

JOBS = [
    ("internship", "jd_推荐算法实习生.txt", "某互联网公司", "推荐算法实习生", ""),
    ("internship", "jd_后端开发实习生.txt", "某电商公司", "后端开发实习生", ""),
    ("baoyan", "baoyan_张老师_图神经网络.txt", "某某大学计算机学院", "图神经网络与推荐系统", "张老师"),
    ("baoyan", "baoyan_王老师_操作系统.txt", "某某大学软件学院", "操作系统与存储系统", "王老师"),
]


def hr(title: str) -> None:
    print(f"\n{'=' * 68}\n{title}\n{'=' * 68}")


def main() -> int:
    t0 = time.time()
    (SANDBOX / "data").mkdir(parents=True, exist_ok=True)
    shutil.copy(ROOT / "data" / "profile.example.yaml", SANDBOX / "data" / "profile.yaml")

    # ---------------------------------------------------------------- 知识库
    hr("1. 知识库构建（10 个刁难资料）")
    t = time.time()
    rep = pipeline.build_knowledge_base(force=False)
    print(f"耗时 {time.time() - t:.1f}s | 文件夹 {rep['folders']}")
    print(f"  成功 {len(rep['created'])}  空 {len(rep['empty'])}  错误 {len(rep['errors'])}")
    if rep["empty"]:
        print(f"  空文件夹: {rep['empty']}")
    for e in rep["errors"]:
        print(f"  错误: {e['folder']} -> {e['error'][:120]}")

    evs = load_all_evidence()
    hr("2. 抽取结果明细")
    print(f"共 {len(evs)} 条证据\n")
    for e in sorted(evs, key=lambda x: x.title):
        missing = sum(1 for m in e.metrics if m.source in ("", "未标注"))
        print(f"{e.title}")
        print(f"   kind={e.kind:9s} period={e.period or '(空)':18s} weight={e.weight}")
        print(f"   domain={e.domain_tags}")
        print(f"   skill ={e.skill_tags}")
        print(f"   metrics={len(e.metrics)} 条，缺出处 {missing} 条")
        print(f"   claim={e.claim[:70]}")
        if not e.metrics:
            print("   ⚠ 无任何量化指标（该资料本身没数字，属预期）")
        print()

    # ---------------------------------------------------------------- 岗位
    hr("3. 岗位录入与解析")
    job_ids = []
    for kind, fname, org, title, mentor in JOBS:
        text = (FIXTURES / fname).read_text(encoding="utf-8")
        t = time.time()
        res = pipeline.add_job(kind=kind, raw_text=text, org=org, title=title, mentor=mentor)
        if not res.get("ok"):
            print(f"  FAIL {title}: {res.get('error')}")
            continue
        j = res["job"]
        job_ids.append((j["id"], kind, title))
        print(f"  {title}  ({kind}, {time.time() - t:.1f}s)")
        print(f"    要求 {len(j['requirements'])} 条 | 关键词 {len(j['keywords'])} 个")
        if j["mentor_topics"]:
            print(f"    导师方向: {j['mentor_topics']}")

    # ---------------------------------------------------------------- 匹配
    hr("4. 匹配结果")
    for job_id, kind, title in job_ids:
        t = time.time()
        m = pipeline.run_match(job_id, refresh=True)
        if not m.get("ok"):
            print(f"  FAIL {title}: {m.get('error')}")
            continue
        r = m["report"]
        print(f"\n【{title}】{time.time() - t:.1f}s | 命中 {len(r['hits'])} 条")
        for h in r["hits"]:
            print(f"   {h['score']:5.1f}  {h['title']}")
            print(f"          {h['reason'][:80]}")
        if r["gaps"]:
            print("   缺口: " + " / ".join(r["gaps"][:4]))

    # ---------------------------------------------------------------- 生成
    hr("5. 简历生成")
    apps = []
    for job_id, kind, title in job_ids:
        tmpl = "academic" if kind == "baoyan" else "single_column"
        t = time.time()
        try:
            g = pipeline.generate_resume(job_id, template_key=tmpl)
        except Exception:  # noqa: BLE001
            print(f"  EXCEPTION {title}")
            traceback.print_exc()
            continue
        if not g.get("ok"):
            print(f"  FAIL {title} ({time.time() - t:.1f}s)")
            print(f"    lint errors: {g.get('lint', {}).get('errors', [])[:5]}")
            print(f"    raw: {str(g)[:200]}")
            continue
        lint = g["lint"]
        pages = "-"
        if g.get("pdf_path"):
            try:
                import pypdfium2 as pdfium

                pages = len(pdfium.PdfDocument(g["pdf_path"]))
            except Exception:  # noqa: BLE001
                pages = "?"
        apps.append((g["application_id"], kind, title, tmpl))
        print(f"  OK {title} [{tmpl}] {time.time() - t:.1f}s")
        print(f"     bullet {lint['stats']['bullets']} 条 | 错误 {lint['stats']['errors']} | 提示 {lint['stats']['warnings']} | PDF {pages} 页")
        for w in lint["warnings"][:3]:
            print(f"     ! {w}")

    # ---------------------------------------------------------------- 状态机
    hr("6. 状态机全流程")
    for app_id, kind, title, _ in apps:
        line = ["sent"]
        if kind == "baoyan":
            line += ["replied", "meeting", "offer"]
        else:
            line += ["screening", "interview", "offer"]
        print(f"\n【{title}】composed → " + " → ".join(line))
        for st in line:
            r = pipeline.advance(app_id, st, "e2e 自动推进")
            ok = r.get("ok")
            print(f"   {'ok ' if ok else 'FAIL'} {st}" + ("" if ok else f"  {r.get('error')}"))
            if not ok:
                break

    # ---------------------------------------------------------------- 汇总
    hr("汇总")
    total = time.time() - t0
    print(f"总耗时 {total:.1f}s")
    print(f"证据 {len(evs)} 条 | 岗位 {len(job_ids)} 个 | 成功生成 {len(apps)} 份")
    print(f"产物目录: {SANDBOX / 'data' / 'output'}")
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass
    raise SystemExit(main())
