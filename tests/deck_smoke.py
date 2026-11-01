"""保研套磁 PPT 的测试：组装、渲染、模板、错误路径。

全程 mock，不调模型也不读真实资料——PPT 的正文直接由证据的 star 和 metrics 拼成，
组装阶段根本不需要 LLM。这样测出来的问题都是真问题，不会混进「模型这次没写好」。

运行：
    ./.venv/Scripts/python.exe tests/deck_smoke.py
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import yaml

os.environ["RESUME_ASSISTANT_MOCK"] = "1"

ROOT = Path(__file__).resolve().parent.parent
_SANDBOX = ROOT / "tests" / "sandbox" / "deck"
os.environ["RESUME_ASSISTANT_DATA"] = str(_SANDBOX / "data")
os.environ["RESUME_ASSISTANT_WORKSPACE"] = str(_SANDBOX / "workspace")

sys.path.insert(0, str(ROOT))

PASS: list[str] = []
FAIL: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(("  ok   " if cond else "  FAIL ") + name + (f"  -> {detail}" if detail and not cond else ""))


def _setup() -> None:
    shutil.rmtree(_SANDBOX, ignore_errors=True)
    data = _SANDBOX / "data"
    (data / "evidence").mkdir(parents=True, exist_ok=True)
    (_SANDBOX / "workspace" / "projects").mkdir(parents=True, exist_ok=True)

    (data / "profile.yaml").write_text(
        yaml.safe_dump(
            {
                "name": "李四",
                "email": "lisi@example.com",
                "phone": "13900000000",
                "education": [
                    {
                        "school": "某某大学",
                        "degree": "本科",
                        "major": "计算机科学与技术",
                        "period": "2022-09 ~ 2026-06",
                        "gpa": "3.90",
                        "gpa_rank": "3/150",
                        "gpa_rank_pct": "2%",
                        "honors": ["国家奖学金（2025）"],
                    }
                ],
                "skills": {
                    "languages": ["Python", "C++"],
                    "frameworks": ["PyTorch"],
                    "tools": ["Git", "Docker"],
                    "english": "CET-6 580",
                },
                "summary": "希望在图神经网络与推荐系统的交叉方向继续深入研究。关注大规模图上的表示学习与冷启动问题。",
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    ev1 = {
        "id": "ev_graph_00",
        "kind": "research",
        "title": "图神经网络推荐算法研究",
        "role": "负责人",
        "period": "2024-03 ~ 2024-12",
        "domain_tags": ["图神经网络", "推荐系统"],
        "skill_tags": ["PyTorch", "LightGCN"],
        "claim": "用 LightGCN 在公开数据集上复现并改进图推荐基线",
        "star": {
            "situation": "协同过滤在冷启动用户上效果差",
            "task": "提升冷启动场景的召回",
            "action": ["复现 LightGCN 基线并统一评测口径", "引入用户侧辅助特征做冷启动增强"],
            "result": "冷启动用户召回率从 0.21 提升到 0.29",
        },
        "metrics": [
            {"name": "召回率", "value": "0.29", "unit": "", "source": "实验记录.md"},
            {"name": "基线召回率", "value": "0.21", "unit": "", "source": "实验记录.md"},
        ],
        "raw_numbers": ["0.29", "0.21"],
        "source_dir": "2024-图神经网络推荐",
        "weight": 0.9,
    }
    ev2 = {
        "id": "ev_nlp_00",
        "kind": "project",
        "title": "中文文本分类系统",
        "role": "独立完成",
        "period": "2023-09 ~ 2024-01",
        "domain_tags": ["自然语言处理"],
        "skill_tags": ["BERT"],
        "claim": "基于 BERT 的中文新闻分类系统",
        "star": {
            "situation": "课程设计要求端到端分类系统",
            "task": "",
            "action": ["用 BERT 微调做 10 分类", "加了 FGM 对抗训练"],
            "result": "测试集准确率 0.913",
        },
        "metrics": [{"name": "准确率", "value": "0.913", "unit": "", "source": "结果.csv"}],
        "raw_numbers": ["0.913"],
        "source_dir": "2023-中文文本分类",
        "weight": 0.6,
    }
    for name, ev in (("graph.yaml", ev1), ("nlp.yaml", ev2)):
        (data / "evidence" / name).write_text(
            yaml.safe_dump({"items": [ev]}, allow_unicode=True, sort_keys=False), encoding="utf-8"
        )


def _job(job_id: str, *, kind: str, mentor: str = "", topics: list[str] | None = None) -> None:
    from resume_assistant.models import Job, JobKind
    from resume_assistant.store import db

    db.save_job(
        Job(
            id=job_id,
            kind=JobKind(kind),
            title="图神经网络方向" if kind == "baoyan" else "推荐算法实习生",
            org="某某大学计算机学院" if kind == "baoyan" else "某互联网公司",
            mentor=mentor,
            mentor_topics=topics or [],
        )
    )


def _match(job_id: str, ids: list[str]) -> None:
    from resume_assistant.models import MatchHit, MatchReport
    from resume_assistant.store import db

    db.save_match(
        MatchReport(
            job_id=job_id,
            hits=[
                MatchHit(evidence_id=eid, title=eid, score=0.9 - i * 0.1, recall_rank=i + 1)
                for i, eid in enumerate(ids)
            ],
        )
    )


def main() -> int:
    _setup()

    from resume_assistant import pipeline
    from resume_assistant.compose.deck import list_deck_templates, load_deck_template

    # ---------------------------------------------------------------- 模板
    print("\n[1] 模板")
    tpls = list_deck_templates()
    check("有两个 PPT 模板", len(tpls) == 2, str([t["key"] for t in tpls]))
    check("模板 key 正确", {t["key"] for t in tpls} == {"baoyan_academic", "baoyan_brief"}, str(tpls))
    check("模板都带描述", all(t["description"] for t in tpls), str(tpls))

    academic = load_deck_template("baoyan_academic")
    brief = load_deck_template("baoyan_brief")
    check("学术型模板有页面定义", len(academic.slides) >= 5, str(len(academic.slides)))
    check("精简型页面更少", len(brief.slides) < len(academic.slides), f"{len(brief.slides)} vs {len(academic.slides)}")

    # ---------------------------------------------------------------- 保研正路
    print("\n[2] 保研岗位：完整生成")
    _job("job_by", kind="baoyan", mentor="王老师", topics=["图神经网络", "推荐系统"])
    _match("job_by", ["ev_graph_00", "ev_nlp_00"])

    res = pipeline.generate_deck("job_by")
    check("生成成功", res.get("ok") is True, str(res)[:300])
    check("保研岗位没有警告", not res.get("warnings"), str(res.get("warnings")))

    outline = res.get("outline") or []
    types = [s["type"] for s in outline]
    check("页数 ≥6", len(outline) >= 6, str(len(outline)))
    check("第一页是封面", types and types[0] == "cover", str(types))
    check("最后一页是结尾", types and types[-1] == "end", str(types))
    check("有个人概况页", "profile" in types, str(types))
    check("有项目页", types.count("project") >= 1, str(types))
    check("有「为什么」页", "why" in types, str(types))
    check("有研究设想页", "plan" in types, str(types))

    pptx_path = Path(res["pptx_path"])
    check("pptx 文件存在", pptx_path.exists(), str(pptx_path))
    check("pptx 有内容", pptx_path.stat().st_size > 8000, f"{pptx_path.stat().st_size} 字节")

    # ---------------------------------------------------------------- 读回来验内容
    print("\n[3] pptx 内容")
    from pptx import Presentation

    prs = Presentation(str(pptx_path))
    check("pptx 页数与 outline 一致", len(prs.slides) == len(outline), f"{len(prs.slides)} vs {len(outline)}")
    check("画布是 16:9", abs(prs.slide_width / prs.slide_height - 16 / 9) < 0.02,
          f"{prs.slide_width / prs.slide_height:.3f}")

    def slide_text(slide) -> str:
        parts = []
        for sh in slide.shapes:
            if getattr(sh, "has_text_frame", False):
                parts.append(sh.text_frame.text)
        return "\n".join(parts)

    cover = slide_text(prs.slides[0])
    check("封面有姓名", "李四" in cover, cover[:120])
    check("封面有申请方向", "图神经网络" in cover, cover[:160])

    all_text = "\n".join(slide_text(s) for s in prs.slides)
    check("正文有 GPA", "3.90" in all_text, all_text[:200])
    check("正文有排名", "3/150" in all_text, all_text[:200])
    check("正文有项目名", "图神经网络推荐算法研究" in all_text, all_text[:200])
    check("正文有指标值", "0.29" in all_text, all_text[:200])
    check("正文有「为什么」内容", "导师方向" in all_text, all_text[:300])

    notes = [s.notes_slide.notes_text_frame.text for s in prs.slides if s.has_notes_slide]
    check("项目页写了演讲备注", any("LightGCN" in n for n in notes), str(notes)[:200])

    # ---------------------------------------------------------------- 数字溯源
    print("\n[4] 指标都有出处")
    deck_yaml = yaml.safe_load(Path(res["ir_path"]).read_text(encoding="utf-8"))
    from resume_assistant.kb.builder import load_all_evidence

    evs = load_all_evidence()
    by_id = {e.id: e for e in evs}
    bad: list[str] = []
    for slide in deck_yaml["slides"]:
        eid = slide.get("evidence_id")
        if not eid:
            continue
        ev = by_id.get(eid)
        if ev is None:
            bad.append(f"{eid} 不存在")
            continue
        allowed = {m.value for m in ev.metrics}
        for item in slide.get("metrics") or []:
            value = item.split("|")[0]
            # 卡片上是 rendered() 的结果，可能带单位（0.29 / 96名）
            ok = any(value.startswith(v) for v in allowed)
            if not ok:
                bad.append(f"{eid}: {value} 不在 {sorted(allowed)}")
    check("每个指标值都能在证据里找到", not bad, str(bad[:4]))

    # ---------------------------------------------------------------- 缺内容时的降级
    print("\n[5] 内容不足时不硬凑")
    _job("job_nomentor", kind="internship")
    _match("job_nomentor", ["ev_graph_00"])
    res2 = pipeline.generate_deck("job_nomentor")
    types2 = [s["type"] for s in res2["outline"]]
    check("实习岗位能生成", res2.get("ok") is True, str(res2)[:200])
    check("实习岗位给了提醒", any("internship" in w or "保研" in w for w in res2["warnings"]), str(res2["warnings"]))
    check("没填导师方向就不出「为什么」页", "why" not in types2, str(types2))

    # 把 summary 去掉，plan 页应该整页消失
    profile_path = _SANDBOX / "data" / "profile.yaml"
    raw = yaml.safe_load(profile_path.read_text(encoding="utf-8"))
    raw.pop("summary", None)
    profile_path.write_text(yaml.safe_dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8")
    res3 = pipeline.generate_deck("job_by")
    types3 = [s["type"] for s in res3["outline"]]
    check("没有自我陈述就不出研究设想页", "plan" not in types3, str(types3))

    # ---------------------------------------------------------------- 精简模板
    print("\n[6] 精简模板")
    res4 = pipeline.generate_deck("job_by", template_key="baoyan_brief")
    check("精简模板生成成功", res4.get("ok") is True, str(res4)[:200])
    check("精简模板页数更少", res4["slides"] < res["slides"], f"{res4['slides']} vs {res['slides']}")
    projects_brief = [s for s in res4["outline"] if s["type"] == "project"]
    check("精简模板最多 2 个项目页", len(projects_brief) <= 2, str(len(projects_brief)))

    # ---------------------------------------------------------------- 错误路径
    print("\n[7] 错误路径")
    res5 = pipeline.generate_deck("job_by", template_key="不存在")
    check("坏模板返回失败", res5.get("ok") is False, str(res5)[:200])
    check("坏模板给出可用列表", "baoyan_academic" in (res5.get("hint") or ""), str(res5.get("hint")))

    res6 = pipeline.generate_deck("job_不存在")
    check("岗位不存在返回失败", res6.get("ok") is False, str(res6)[:200])
    check("岗位不存在说清原因", "不存在" in (res6.get("error") or ""), str(res6.get("error")))

    # 同一项目不该在 PPT 里出现两次
    print("\n[8] 不重复")
    _job("job_dup", kind="baoyan", mentor="张老师", topics=["推荐系统"])
    _match("job_dup", ["ev_graph_00", "ev_graph_00", "ev_nlp_00"])
    res7 = pipeline.generate_deck("job_dup")
    titles = [s["title"] for s in res7["outline"] if s["type"] == "project"]
    check("同一项目只占一页", len(titles) == len(set(titles)), str(titles))

    print("\n[9] 手改 deck.yaml 后重渲染")
    res8 = pipeline.generate_deck("job_by")
    ir_path = Path(res8["ir_path"])
    raw = yaml.safe_load(ir_path.read_text(encoding="utf-8"))
    raw["slides"][0]["title"] = "张三丰"  # 故意改个显眼的字，方便断言
    ir_path.write_text(yaml.safe_dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8")

    rr = pipeline.rerender_deck(res8["application_id"])
    check("重渲染成功", rr.get("ok") is True, str(rr)[:200])
    prs2 = Presentation(str(rr["pptx_path"]))
    check("改后的字进了新片", "张三丰" in slide_text(prs2.slides[0]), slide_text(prs2.slides[0])[:120])
    check("页数没变", len(prs2.slides) == res8["slides"], f"{len(prs2.slides)} vs {res8['slides']}")

    rr2 = pipeline.rerender_deck("app_不存在")
    check("投递记录不存在时返回失败", rr2.get("ok") is False, str(rr2)[:120])

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
