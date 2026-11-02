"""第二轮 2A 的两套模板：体制内/国企版、科研·论文导向。

测的是「模板有没有真的起作用」，不是「PDF 能不能编译」——后者编译失败会直接返回 None，
一眼就看见了。真正难发现的是这三类：
1. 段序：模板把荣誉写在项目前面，compose 却按模板外的顺序拼；
2. 字段口径：GPA 前置、实验室/导师只在科研版出现，国企版不该有；
3. 重复与串段：政治面貌在页眉和基本信息各出现一次、论文同时混进项目段。

所以这里用真实组稿（compose）跑，不用手写 IR——手写 IR 会把上面三类问题一起绕过去。

运行：./.venv/Scripts/python.exe tests/templates_2a.py
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_SANDBOX = ROOT / "tests" / "sandbox" / "templates_2a"

# 组稿要写 bullet，这里只要结构，用 mock 就够
os.environ["RESUME_ASSISTANT_MOCK"] = "1"
os.environ["RESUME_ASSISTANT_DATA"] = str(_SANDBOX / "data")
os.environ["RESUME_ASSISTANT_WORKSPACE"] = str(_SANDBOX / "workspace")

sys.path.insert(0, str(ROOT))

PASS: list[str] = []
FAIL: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(("  ok   " if cond else "  FAIL ") + name + (f"  -> {detail}" if detail and not cond else ""))


def _inputs(photo: str = ""):
    from resume_assistant.models import (
        Education, Evidence, FamilyMember, Job, JobKind, MatchHit, MatchReport, Metric, Profile, Skills, Star,
    )

    profile = Profile(
        name="李四",
        gender="男",
        birth="2003-05",
        phone="13900000000",
        email="lisi@example.com",
        city="天津",
        political="中共党员",
        native_place="山东济南",
        height="175cm",
        weight="65kg",
        health="良好",
        photo=photo,
        family=[
            FamilyMember(name="李建国", relation="父亲", workplace="某中学教师", political="中共党员"),
            FamilyMember(name="王秀兰", relation="母亲", workplace="某医院护士", political="群众"),
        ],
        education=[
            Education(
                school="某某大学", degree="本科", major="计算机科学与技术",
                period="2022-09 ~ 2026-06", gpa="3.90/4.0", gpa_rank="3/150", gpa_rank_pct="2%",
                lab="智能感知实验室", advisor="张教授",
                courses=["数据结构 95", "机器学习 96"],
                honors=["国家奖学金（2025）"],
            )
        ],
        skills=Skills(languages=["Python", "C++"], frameworks=["PyTorch"], english="CET-6 550"),
        summary="关注小样本目标检测与工业质检落地。",
    )

    evs = [
        Evidence(
            id="ev_p1", kind="paper", title="小样本目标检测的特征对齐方法",
            org="CVPR 2025", role="第一作者", period="2025-06",
            claim="提出特征对齐模块，小样本检测 mAP 提升 3.1",
            metrics=[Metric(name="mAP 提升", value="3.1", source="论文实验")],
        ),
        Evidence(
            id="ev_p2", kind="patent", title="一种工业质检图像标注方法",
            org="发明专利", role="第二发明人", period="2024-11",
            claim="已授权，覆盖弱监督标注流程",
        ),
        Evidence(
            id="ev_pr1", kind="research", title="小目标检测优化方法研究",
            role="负责人", org="智能感知实验室", period="2025-03 ~ 至今",
            domain_tags=["计算机视觉"], skill_tags=["PyTorch", "YOLOv8"],
            claim="小目标检测 mAP 从 0.62 提升到 0.68",
            star=Star(
                situation="工业质检场景小目标漏检严重",
                task="在单阶段检测器上提升小目标召回",
                action=["引入高分辨率特征分支", "重设锚框尺度"],
                result="mAP 从 0.62 提升到 0.68",
            ),
            metrics=[Metric(name="mAP", value="0.68", source="实验记录")],
        ),
        Evidence(
            id="ev_pr2", kind="course", title="校园二手交易平台",
            role="后端负责人", org="某某大学", period="2024-03 ~ 2024-06",
            domain_tags=["后端"], skill_tags=["Spring Boot"],
            claim="实现 23 个 REST 接口，响应时间降至 180ms",
            metrics=[Metric(name="响应时间", value="180", unit="ms", source="压测")],
        ),
    ]
    job = Job(
        id="job_demo", kind=JobKind.BAOYAN, title="计算机视觉方向学硕",
        org="某某大学计算机学院", mentor="张老师",
        mentor_topics=["小样本检测", "工业质检"], keywords=["计算机视觉", "PyTorch"],
    )
    report = MatchReport(
        job_id="job_demo",
        hits=[MatchHit(evidence_id=e.id, title=e.title, score=0.9 - i * 0.05) for i, e in enumerate(evs)],
    )
    return profile, job, report, evs


def _compose(template_key: str, photo: str = ""):
    from resume_assistant.compose.ir import compose
    from resume_assistant.compose.templates import load_template
    from resume_assistant.llm import get_llm

    profile, job, report, evs = _inputs(photo)
    tpl = load_template(template_key)
    return compose(profile, job, report, evs, tpl, llm=get_llm()), tpl, evs, profile


def _text_of(ir, sec_type: str) -> str:
    for s in ir.sections:
        if s.type == sec_type:
            parts = []
            for e in s.entries:
                parts.append(e.heading)
                parts.append(e.meta)
                parts += [b.text for b in e.bullets]
            return "\n".join(p for p in parts if p)
    return ""


def main() -> int:
    shutil.rmtree(_SANDBOX, ignore_errors=True)
    (_SANDBOX / "data").mkdir(parents=True, exist_ok=True)

    from resume_assistant.compose.templates import list_templates, load_template
    from resume_assistant.render.typst import build_source, render_pdf
    from resume_assistant.validate.linter import lint

    print("\n[1] 模板清单")
    keys = {t["key"] for t in list_templates()}
    # 只断言「2A 的这两套在」，不锁死全集：加模板本来就该只改 yaml，
    # 这里写成等号的话，每加一套模板都要回来改一次测试
    check("2A 的两套模板都在", {"gov_soe", "research"} <= keys, str(sorted(keys)))
    check("模板清单跟着目录走（至少四套）", len(keys) >= 4, str(sorted(keys)))
    for k in ("gov_soe", "research"):
        t = load_template(k)
        check(f"{k} 有名称和描述", bool(t.name) and bool(t.description), f"{t.name} / {t.description}")

    # ---------------------------------------------------------------- 体制内
    print("\n[2] 体制内/国企版")
    gov_ir, gov_tpl, gov_evs, gov_profile = _compose("gov_soe")
    types = [s.type for s in gov_ir.sections]
    check("第一段是基本信息", types and types[0] == "basic_info", str(types))
    check("荣誉排在项目前面", types.index("honors") < types.index("projects"), str(types))
    check("有家庭成员段", "family" in types, str(types))
    check("没有研究兴趣段", "research_interest" not in types, str(types))

    basic = _text_of(gov_ir, "basic_info")
    for field in ("政治面貌", "籍贯", "身高", "体重", "健康状况"):
        check(f"基本信息含{field}", field in basic, basic)
    check("基本信息不含电话（页眉已有）", "13900000000" not in basic, basic)

    # 页眉：模板把性别/出生/政治面貌挪走了，只剩联系方式
    src = build_source(gov_ir, gov_tpl)
    header = src.split("基本信息")[0]
    check("页眉不重复政治面貌", "中共党员" not in header, header[:400])
    check("页眉保留手机号", "13900000000" in header, header[:400])

    fam = _text_of(gov_ir, "family")
    check("家庭成员有父亲", "父亲" in fam and "李建国" in fam, fam)
    check("家庭成员带工作单位", "某中学教师" in fam, fam)

    # 论文不该出现在项目段：国企版的项目段是「做过什么」，不是「发过什么」
    proj = _text_of(gov_ir, "projects")
    check("论文没混进项目段", "小样本目标检测的特征对齐方法" not in proj, proj[:200])
    check("项目段有科研经历", "小目标检测优化方法研究" in proj, proj[:200])

    # 但也不能整份丢掉：国企网申表里「科研成果」是独立一栏
    check("国企版有论文与专利段", "publications" in types, str(types))
    check("国企版论文段有内容", "小样本目标检测的特征对齐方法" in _text_of(gov_ir, "publications"),
          _text_of(gov_ir, "publications")[:200])

    edu = _text_of(gov_ir, "education")
    check("国企版不写实验室导师", "实验室" not in edu and "张教授" not in edu, edu)

    # ---------------------------------------------------------------- 科研型
    print("\n[3] 科研·论文导向")
    res_ir, res_tpl, res_evs, res_profile = _compose("research")
    rtypes = [s.type for s in res_ir.sections]
    check("有论文与专利段", "publications" in rtypes, str(rtypes))
    check("论文段在教育之后", rtypes.index("publications") > rtypes.index("education"), str(rtypes))

    pubs = _text_of(res_ir, "publications")
    check("论文标题进了论文段", "小样本目标检测的特征对齐方法" in pubs, pubs)
    check("专利标题进了论文段", "一种工业质检图像标注方法" in pubs, pubs)
    check("论文段带期刊名", "CVPR 2025" in pubs, pubs)
    check("论文段带作者位次", "第一作者" in pubs, pubs)
    # 时间倒序：2025-06 的论文在 2024-11 的专利前面
    check("论文按时间倒序", pubs.index("CVPR 2025") < pubs.index("发明专利"), pubs)

    r_edu = _text_of(res_ir, "education")
    check("科研版 GPA 在时间前面",
          r_edu.index("GPA") < r_edu.index("2022"), r_edu)
    check("科研版带实验室", "智能感知实验室" in r_edu, r_edu)
    check("科研版带导师", "张教授" in r_edu, r_edu)

    r_proj = _text_of(res_ir, "projects")
    check("论文没在项目段重复", "小样本目标检测的特征对齐方法" not in r_proj, r_proj[:200])

    # ---------------------------------------------------------------- 校验闸门
    print("\n[4] 两套模板都要过校验闸门")
    for key, ir, evs, prof in (
        ("gov_soe", gov_ir, gov_evs, gov_profile),
        ("research", res_ir, res_evs, res_profile),
    ):
        res = lint(ir, evs, prof)
        check(f"{key} 校验 0 错误", res.ok, "; ".join(res.errors[:3]))

    # 身高体重这类档案字段不该被判成编造
    check("身高 175 没被误判", not any("175" in e for e in lint(gov_ir, gov_evs, gov_profile).errors),
          str(lint(gov_ir, gov_evs, gov_profile).errors[:2]))

    # ---------------------------------------------------------------- 照片
    print("\n[5] 照片位")
    no_photo = build_source(gov_ir, gov_tpl)
    check("没有照片就不画图", "#image(" not in no_photo, no_photo[:300])

    photo_file = _SANDBOX / "photo.png"
    shutil.copy(ROOT / "tests" / "fixtures" / "photo_demo.png", photo_file)
    p_ir, p_tpl, _, _ = _compose("gov_soe", photo=str(photo_file))
    with_photo = build_source(p_ir, p_tpl, photo="_photo.png")
    check("有照片就画图", "#image(" in with_photo, with_photo[:300])
    check("姓名仍然居中（两侧留了等宽空列）", "columns: (2.6cm, 1fr, 2.6cm)" in with_photo, with_photo[:300])

    out_dir = _SANDBOX / "out"
    _typ, pdf = render_pdf(p_ir, p_tpl, out_dir)
    check("带照片能编译出 PDF", pdf is not None and Path(pdf).exists(), str(pdf))
    check("照片被复制进输出目录", (out_dir / "_photo.png").exists(), str(list(out_dir.glob("*"))))

    # 照片路径写错时不能把整份简历搞崩
    bad_ir, bad_tpl, _, _ = _compose("gov_soe", photo=str(_SANDBOX / "不存在.png"))
    _t, bad_pdf = render_pdf(bad_ir, bad_tpl, _SANDBOX / "out_bad")
    check("照片路径不存在也能出 PDF", bad_pdf is not None, str(bad_pdf))
    check("坏照片不留空框", "#image(" not in (_SANDBOX / "out_bad" / "resume.typ").read_text(encoding="utf-8"),
          "typ 里还有 image")

    # ---------------------------------------------------------------- 缺字段时的降级
    print("\n[6] 没填就整段不出")
    from resume_assistant.models import Profile

    bare = Profile(name="王五", phone="13800000000", education=gov_profile.education)
    from resume_assistant.compose.ir import compose
    from resume_assistant.llm import get_llm

    _, job, report, evs = _inputs()
    bare_ir = compose(bare, job, report, evs, gov_tpl, llm=get_llm())
    btypes = [s.type for s in bare_ir.sections]
    check("没填家庭信息就不出家庭成员段", "family" not in btypes, str(btypes))
    check("没填籍贯身高就不出基本信息段", "basic_info" not in btypes, str(btypes))

    # ---------------------------------------------------------------- 论文 kind
    print("\n[7] 论文/专利类型的抽取与容错")
    from resume_assistant.kb.builder import EVIDENCE_KINDS, _to_evidence
    from resume_assistant.kb.scanner import ProjectFolder

    folder = ProjectFolder(name="2025-论文-小样本检测", path=ROOT, files=[], skipped=[])
    ev = _to_evidence({"kind": "paper", "title": "小样本检测"}, folder, 0)
    check("paper 能落成证据", ev.kind == "paper", ev.kind)
    ev2 = _to_evidence({"kind": "journal", "title": "模型瞎写的类型"}, folder, 1)
    check("非法 kind 退回 project 而不是整份作废", ev2.kind == "project", ev2.kind)
    check("白名单包含 paper/patent", {"paper", "patent"} <= EVIDENCE_KINDS, str(sorted(EVIDENCE_KINDS)))

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
