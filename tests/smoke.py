"""离线冒烟测试：不消耗 API 额度，验证全链路代码路径。

运行：
    set RESUME_ASSISTANT_MOCK=1
    ./.venv/Scripts/python.exe tests/smoke.py
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

os.environ["RESUME_ASSISTANT_MOCK"] = "1"

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PASS, FAIL = [], []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(("  ok   " if cond else "  FAIL ") + name + (f"  -> {detail}" if detail and not cond else ""))


def main() -> int:
    from resume_assistant import pipeline
    from resume_assistant.kb.builder import load_all_evidence
    from resume_assistant.models import AppStatus, JobKind, can_transition
    from resume_assistant.store import db

    print("\n[1] 模块导入")
    check("pipeline 可导入", True)

    print("\n[2] 知识库构建（mock）")
    rep = pipeline.build_knowledge_base(force=True)
    check("扫描到项目文件夹", rep["folders"] >= 2, str(rep))
    check("无抽取错误", not rep["errors"], str(rep["errors"]))
    evs = load_all_evidence()
    check("产出证据", len(evs) >= 2, f"got {len(evs)}")

    print("\n[3] 知识库概况")
    st = pipeline.knowledge_stats()
    check("统计可用", st["evidence_count"] == len(evs), str(st))

    print("\n[4] 岗位录入")
    res = pipeline.add_job(
        kind="internship",
        title="推荐算法实习生",
        raw_text="岗位职责：负责推荐系统召回与排序优化。任职要求：熟悉 Python、PyTorch，"
        "有图神经网络相关项目经验，熟悉 Spark 者优先。",
        org="某科技公司",
    )
    check("录入成功", res.get("ok") is True, str(res))
    job_id = res["job"]["id"]

    res2 = pipeline.add_job(
        kind="baoyan",
        title="社交网络与推荐方向",
        raw_text="导师研究方向：图神经网络、社交推荐、冷启动问题。要求：具备扎实的机器学习基础，"
        "有论文复现经验，熟悉 PyTorch。",
        org="某大学计算机学院",
        mentor="李老师",
    )
    check("保研岗位录入成功", res2.get("ok") is True, str(res2))
    job2_id = res2["job"]["id"]

    print("\n[5] 匹配")
    m = pipeline.run_match(job_id, refresh=True)
    check("匹配返回成功", m.get("ok") is True, str(m))
    check("有命中", len(m["report"]["hits"]) > 0, str(m["report"]))

    print("\n[6] 生成简历（mock）")
    g = pipeline.generate_resume(job_id, template_key="single_column")
    check("生成成功", g.get("ok") is True, str(g.get("lint")))
    check("产出 typ 文件", bool(g.get("typ_path")) and Path(g["typ_path"]).exists(), str(g.get("typ_path")))
    check("状态推进到 composed", g.get("status") == "composed", str(g.get("status")))

    g2 = pipeline.generate_resume(job2_id, template_key="academic")
    check("学术模板生成成功", g2.get("ok") is True, str(g2.get("lint")))

    print("\n[7] 状态机")
    app_id = g["application_id"]
    ok_sent = pipeline.advance(app_id, "sent", "已投递")
    check("composed → sent 合法", ok_sent.get("ok") is True, str(ok_sent))
    bad = pipeline.advance(app_id, "offer")
    check("sent → offer 被拒绝", bad.get("ok") is False, str(bad))
    ok_screen = pipeline.advance(app_id, "screening")
    check("sent → screening 合法", ok_screen.get("ok") is True, str(ok_screen))
    check(
        "保研线不允许 screening",
        not can_transition(JobKind.BAOYAN, AppStatus.SENT, AppStatus.SCREENING),
    )
    tl = db.app_timeline(app_id)
    check("时间线有记录", len(tl) >= 4, str(tl))

    print("\n[8] 校验闸门")
    from resume_assistant.compose.ir import compose
    from resume_assistant.compose.templates import load_template
    from resume_assistant.models import ResumeIR
    from resume_assistant.validate.linter import lint

    profile = pipeline.load_profile()
    bad_ir = ResumeIR(
        job_id=job_id,
        sections=[
            {
                "type": "projects",
                "title": "项目经历",
                "entries": [
                    {
                        "heading": "测试",
                        "meta": "",
                        "bullets": [
                            {"text": "将系统吞吐提升到 99999 QPS", "evidence_id": evs[0].id, "metrics": ["99999"]},
                            {"text": "引用不存在的证据", "evidence_id": "ev_not_exist", "metrics": []},
                        ],
                    }
                ],
            }
        ],
    )
    lr = lint(bad_ir, evs, profile)
    check("拦住编造数字", any("99999" in e for e in lr.errors), str(lr.errors))
    check("拦住虚假 evidence_id", any("ev_not_exist" in e for e in lr.errors), str(lr.errors))

    print("\n[9] 资格校验（届别 / 学历 / 专业）")
    from resume_assistant.models import Education, Job, JobKind, JobRequirement, Profile
    from resume_assistant.validate.eligibility import check as elig_check

    def mk_job(reqs: list[str]) -> Job:
        return Job(id="j", kind=JobKind.INTERNSHIP, title="t", requirements=[JobRequirement(text=t) for t in reqs])

    cs = Profile(
        name="测试",
        education=[Education(school="X", degree="本科", major="计算机科学与技术", period="2022-09 ~ 2026-06")],
    )
    check("届别不符被判 fail", elig_check(mk_job(["2027届本科及以上学历"]), cs).blocked)
    check("届别相符不报警", not elig_check(mk_job(["2026届本科及以上"]), cs).blocked)
    check("学历不足被判 fail", elig_check(mk_job(["硕士及以上学历"]), cs).blocked)
    check("无硬性条件不报警", not elig_check(mk_job(["熟悉Python"]), cs).issues)

    mech = Profile(
        name="测试",
        education=[Education(school="X", degree="本科", major="机械工程", period="2022-09 ~ 2026-06")],
    )
    check(
        "专业不符给 warn",
        any(i.level == "warn" for i in elig_check(mk_job(["计算机、数学、统计等相关专业"]), mech).issues),
    )

    print("\n[10] 排版字号自适应")
    from resume_assistant.compose.templates import load_template
    from resume_assistant.models import Bullet, IREntry, IRSection, ResumeIR
    from resume_assistant.render.typst import build_source, render_pdf

    tmpl = load_template("single_column")
    tiny = ResumeIR(
        contact={"name": "张三", "phone": "13800000000"},
        sections=[
            IRSection(
                type="projects",
                title="项目经历",
                entries=[
                    IREntry(
                        heading="某小项目",
                        meta="2024.03 ~ 2024.06",
                        bullets=[Bullet(text="做了点小事。", evidence_id="ev_x")],
                    )
                ],
            )
        ],
    )
    # 字号必须等比推导，否则放大后会出现「正文变大、标题不变」的错位
    src10 = build_source(tiny, tmpl, font_size=10)
    src12 = build_source(tiny, tmpl, font_size=12)
    check("标题字号随基准等比放大", "size: 13.8pt" in src12 and "size: 11.5pt" in src10)
    check("姓名字号随基准等比放大", "size: 22.8pt" in src12 and "size: 19pt" in src10)

    out = ROOT / "tests" / "sandbox" / "_fit"
    shutil.rmtree(out, ignore_errors=True)
    typ, pdf = render_pdf(tiny, tmpl, out, autofit=True)
    base_pt = float(tmpl.page["font_size"].rstrip("pt"))
    picked = float(typ.read_text(encoding="utf-8").split("size: ")[1].split("pt")[0])
    check("内容极少时自动放大字号", pdf is not None and picked > base_pt, f"{picked}pt")
    check("试排中间产物已清理", not list(out.glob("_fit*")))
    check("自适应关闭时用模板原字号", "size: %gpt" % base_pt in build_source(tiny, tmpl))
    shutil.rmtree(out, ignore_errors=True)

    print(f"\n{'=' * 46}")
    print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    if FAIL:
        print("失败项：" + ", ".join(FAIL))
    print(f"{'=' * 46}\n")
    return 0 if not FAIL else 1


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass
    raise SystemExit(main())
