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

import yaml

os.environ["RESUME_ASSISTANT_MOCK"] = "1"

ROOT = Path(__file__).resolve().parent.parent

# 沙箱隔离：这个脚本会 build_knowledge_base(force=True)，如果指向真实 data/
# 就会用 mock 数据把用户辛苦抽出来的证据全部覆盖掉。必须隔离。
_SANDBOX = ROOT / "tests" / "sandbox" / "smoke"
os.environ["RESUME_ASSISTANT_DATA"] = str(_SANDBOX / "data")
os.environ["RESUME_ASSISTANT_WORKSPACE"] = str(ROOT / "tests" / "sandbox" / "workspace")

sys.path.insert(0, str(ROOT))

PASS, FAIL = [], []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(("  ok   " if cond else "  FAIL ") + name + (f"  -> {detail}" if detail and not cond else ""))


def main() -> int:
    # 清空沙箱，保证每次从零开始，不受上次跑残留影响
    shutil.rmtree(_SANDBOX, ignore_errors=True)
    (_SANDBOX / "data").mkdir(parents=True, exist_ok=True)
    shutil.copy(ROOT / "data" / "profile.example.yaml", _SANDBOX / "data" / "profile.yaml")

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

    print("\n[11] 手改 IR 后重渲染")
    from resume_assistant.config import OUTPUT_DIR

    ir_file = OUTPUT_DIR / app_id / "ir.yaml"
    raw_ir = yaml.safe_load(ir_file.read_text(encoding="utf-8"))
    sec = next(s for s in raw_ir["sections"] if s["entries"] and s["entries"][0].get("bullets"))
    sec["entries"][0]["bullets"][0]["text"] = "手工改写的这一句"
    ir_file.write_text(yaml.safe_dump(raw_ir, allow_unicode=True, sort_keys=False), encoding="utf-8")

    rr = pipeline.rerender(app_id)
    check("重渲染成功", rr.get("ok") is True, str(rr)[:200])
    typ_text = Path(rr["typ_path"]).read_text(encoding="utf-8")
    check("手改内容进入 PDF 源码", "手工改写的这一句" in typ_text)
    check("重渲染不调模型（bullet 数不变）", rr["lint"]["stats"]["bullets"] == len(
        [b for s in raw_ir["sections"] for e in s["entries"] for b in (e.get("bullets") or [])]
    ))
    bad = pipeline.rerender(app_id, template_key="no_such_template")
    check("模板名写错时报错", bad.get("ok") is False and "模板" in bad.get("error", ""), str(bad)[:120])
    check("投递编号写错时报错", pipeline.rerender("app_not_exist").get("ok") is False)

    print("\n[12] 知识库校对")
    from resume_assistant.kb.review import audit
    from resume_assistant.models import Evidence, Metric

    def mk_ev(**kw) -> Evidence:
        base = dict(
            id="ev_a",
            kind="project",
            title="校园二手交易平台",
            claim="用 Spring Boot 做了个交易平台，支持商品发布与订单流转",
            period="2024.03 ~ 2024.06",
            raw_excerpt="校园二手交易平台，Spring Boot 后端，支持商品发布、订单流转与评价",
        )
        base.update(kw)
        return Evidence(**base)

    r = audit([mk_ev(period="")])
    check("查出时间为空", any(f.field == "period" for f in r.findings))

    r = audit([mk_ev(period="大三上学期")])
    check("查出时间没有年份", any("没有年份" in f.message for f in r.findings))

    r = audit([mk_ev(kind="research", title="课程大作业", raw_excerpt="这是一门课的期末大作业，做了个网站")])
    check("查出 kind 标错（课程被标成科研）", any(f.field == "kind" for f in r.findings))

    r = audit([mk_ev(kind="intern", title="后端接口开发", raw_excerpt="做了接口开发，写了文档")])
    check("查出 kind 标错（无实习字眼）", any(f.field == "kind" for f in r.findings))

    r = audit([mk_ev(id="a"), mk_ev(id="b", title="校园二手交易平台后端")])
    check("查出疑似重复项目", any(f.field == "duplicate" for f in r.findings))

    r = audit([mk_ev(metrics=[Metric(name="QPS", value="1200", unit="", source="")])])
    check("查出指标无出处", any(f.field == "metrics" and f.level == "warn" for f in r.findings))

    rv = pipeline.review_knowledge_base(write_html=True)
    check("校对产出卡片页", rv.get("ok") is True and Path(rv["html_path"]).exists(), str(rv)[:120])

    target = evs[0].id
    fx = pipeline.fix_evidence(target, period="2024.03 ~ 2024.06")
    check("能改单条证据的字段", fx.get("ok") is True, str(fx)[:150])
    check(
        "改动已落盘",
        next(e for e in load_all_evidence() if e.id == target).period == "2024.03 ~ 2024.06",
    )
    bad_fix = pipeline.fix_evidence(target, kind="不存在的类型")
    check("非法 kind 被拒绝", bad_fix.get("ok") is False, str(bad_fix)[:120])
    check("改不存在的证据报错", pipeline.fix_evidence("ev_not_exist", period="x").get("ok") is False)

    print("\n[14] 资料接入（编码 / 格式 / 跳过原因）")
    import codecs

    from resume_assistant.kb.scanner import decode_bytes, scan_folder

    check("GBK 能正确解码", decode_bytes("准确率 92%".encode("gb18030"))[0] == "准确率 92%")
    check("UTF-8 BOM 能正确解码", decode_bytes(codecs.BOM_UTF8 + "中文".encode())[0] == "中文")
    check("UTF-16 能正确解码", decode_bytes("中文".encode("utf-16"))[0] == "中文")

    dirty = ROOT / "tests" / "sandbox" / "workspace" / "projects" / "2026-脏资料测试"
    pf = scan_folder(dirty)
    check("GBK txt 读成正常中文", "编译原理" in pf.text)
    check("pptx 能读到正文", "74.5%" in pf.text)
    check("xlsx 能读到表格", "0.745" in pf.text)
    reasons = {s["file"]: s["reason"] for s in pf.skipped}
    check(
        "老版 .doc 给出可操作的原因",
        any(".doc" in f and ".docx" in r for f, r in reasons.items()),
        str(reasons),
    )
    check("截图给出 OCR 提示", any("OCR" in r for r in reasons.values()), str(reasons))
    check("Office 锁文件被静默忽略", not any("~$" in f for f in reasons), str(reasons))

    sr = pipeline.scan_report()
    check("扫描报告可用", sr["ok"] is True and sr["folders"] >= 10, str(sr.get("folders")))
    check("列出读不到文字的文件夹", "2026-空文件夹测试" in sr["no_text"], str(sr["no_text"]))

    order_dir = ROOT / "tests" / "sandbox" / "_order" / "proj"
    shutil.rmtree(order_dir.parent, ignore_errors=True)
    order_dir.mkdir(parents=True)
    (order_dir / "README.md").write_text("# 项目说明\n做了个东西，效果不错。", encoding="utf-8")
    (order_dir / "a_main.cpp").write_text("int main(){}", encoding="utf-8")
    (order_dir / "b_util.py").write_text("print(1)", encoding="utf-8")
    of = scan_folder(order_dir)
    check("README 优先读取（截断时不会先砍掉它）", of.files[0] == "README.md", str(of.files))
    shutil.rmtree(order_dir.parent, ignore_errors=True)

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
