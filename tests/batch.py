"""第三轮 · 批量操作测试：batch_match / rank_jobs / batch_generate / compare。

运行：
    ./.venv/Scripts/python.exe tests/batch.py
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

os.environ["RESUME_ASSISTANT_MOCK"] = "1"

ROOT = Path(__file__).resolve().parent.parent

# 沙箱隔离：会写岗位和匹配报告到 SQLite
_SANDBOX = ROOT / "tests" / "sandbox" / "batch"
os.environ["RESUME_ASSISTANT_DATA"] = str(_SANDBOX / "data")
os.environ["RESUME_ASSISTANT_WORKSPACE"] = str(ROOT / "tests" / "sandbox" / "workspace")

sys.path.insert(0, str(ROOT))

PASS, FAIL = [], []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(("  ok   " if cond else "  FAIL ") + name + (f"  -> {detail}" if detail and not cond else ""))


def main() -> int:
    shutil.rmtree(_SANDBOX, ignore_errors=True)
    (_SANDBOX / "data").mkdir(parents=True, exist_ok=True)
    shutil.copy(ROOT / "data" / "profile.example.yaml", _SANDBOX / "data" / "profile.yaml")

    from resume_assistant import pipeline
    from resume_assistant.store import db

    # ── 准备：造 3 个岗位 + 1 个知识库 ──

    print("\n[1] 准备岗位与知识库")
    jds = [
        ("internship", "推荐算法实习", "某科技", "负责推荐系统召回与排序。要求 Python、PyTorch、机器学习。"),
        ("internship", "后端开发实习", "某互联网", "负责后端 API 开发。要求 Java、MySQL、分布式。"),
        ("baoyan", "保研联系导师", "某大学", "研究方向：图神经网络、推荐系统。要求有科研经历。"),
    ]
    job_ids: list[str] = []
    for kind, title, org, text in jds:
        res = pipeline.add_job(kind=kind, title=title, org=org, raw_text=text)
        assert res["ok"], f"add_job 失败：{res}"
        job_ids.append(res["job"]["id"])
    check("造了 3 个岗位", len(job_ids) == 3, str(job_ids))

    # 建知识库（用 tests/sandbox/workspace 里的示例项目）
    kb_res = pipeline.build_knowledge_base(force=True)
    stats = pipeline.knowledge_stats()
    check("知识库非空", stats["evidence_count"] > 0, str(stats.get("evidence_count")))

    # ── 3.1 batch_match ──

    print("\n[2] batch_match")
    res = pipeline.batch_match(job_ids)
    check("返回 ok", res["ok"])
    check("total=3", res["total"] == 3, str(res["total"]))
    check("全部成功", res["succeeded"] == 3, str(res["succeeded"]))
    check("failed=0", res["failed"] == 0)
    for r in res["results"]:
        check(f"{r['job_id']} 有 hits_count", r.get("hits_count") is not None, str(r))
        check(f"{r['job_id']} 有 top_score", r.get("top_score") is not None, str(r))

    # 再跑一次应该命中缓存
    res2 = pipeline.batch_match(job_ids)
    cached_count = sum(1 for r in res2["results"] if r.get("cached"))
    check("第二次跑命中缓存", cached_count == 3, f"cached={cached_count}")

    # refresh 强制重算
    res3 = pipeline.batch_match(job_ids, refresh=True)
    fresh_count = sum(1 for r in res3["results"] if not r.get("cached"))
    check("refresh 后不缓存", fresh_count == 3, f"fresh={fresh_count}")

    # 混入不存在的 job_id
    res4 = pipeline.batch_match([job_ids[0], "job_不存在"])
    check("混入不存在的也能跑", res4["succeeded"] == 1 and res4["failed"] == 1)
    failed_item = [r for r in res4["results"] if not r["ok"]][0]
    check("失败项有 error 信息", bool(failed_item.get("error")))

    # 空列表
    res5 = pipeline.batch_match([])
    check("空列表返回 ok", res5["ok"] and res5["total"] == 0)

    # ── 3.2 rank_jobs ──

    print("\n[3] rank_jobs")
    res = pipeline.rank_jobs()
    check("返回 ok", res["ok"])
    check("有排序结果", len(res["items"]) > 0, str(res.get("items")))
    # 排序应该是降序
    scores = [it["adjusted_score"] for it in res["items"]]
    check("按 adjusted_score 降序", scores == sorted(scores, reverse=True), str(scores))
    for it in res["items"]:
        check(f"{it['job_id']} 有 title", bool(it.get("title")))
        check(f"{it['job_id']} 有 org", "org" in it)
        check(f"{it['job_id']} avg ≤ adjusted", it["avg_score"] >= it["adjusted_score"] - 0.1)

    # top_n 限制
    res2 = pipeline.rank_jobs(top_n=2)
    check("top_n=2 只返回 2 个", len(res2["items"]) <= 2, str(len(res2["items"])))

    # 传不存在的 job_ids
    res3 = pipeline.rank_jobs(job_ids=["job_不存在"])
    check("不存在的 job_ids 返回空", res3["ok"] and len(res3["items"]) == 0)

    # 按 kind 过滤
    intern_ids = [jid for jid, (k, *_ ) in zip(job_ids, jds) if k == "internship"]
    res4 = pipeline.rank_jobs(job_ids=intern_ids)
    check("只看实习岗", all(it["kind"] == "internship" for it in res4["items"]), str([it["kind"] for it in res4["items"]]))

    # ── 3.3 batch_generate ──

    print("\n[4] batch_generate")
    res = pipeline.batch_generate(job_ids[:2], template_key="single_column")
    check("返回 ok", res["ok"])
    check("total=2", res["total"] == 2)
    for r in res["results"]:
        if r["ok"]:
            check(f"{r['job_id']} 生成了 PDF", bool(r.get("pdf_path")), str(r))
            check(f"{r['job_id']} 有 application_id", bool(r.get("application_id")))
            check(f"{r['job_id']} 有 pages", r.get("pages") is not None)
        else:
            # mock 模式下应该能成功；如果失败了，报出来
            check(f"{r['job_id']} 生成成功", False, r.get("error", ""))

    # 混入不存在的
    res2 = pipeline.batch_generate([job_ids[0], "job_不存在"])
    check("混入不存在仍跑", res2["succeeded"] == 1 and res2["failed"] == 1)

    # ── 3.4 compare ──

    print("\n[5] compare")
    res = pipeline.compare(job_ids)
    check("返回 ok", res["ok"])
    check("total=3", res["total"] == 3)
    check("rows 数量对", len(res["rows"]) == 3)

    # 每行都应该有 title 和 status
    for row in res["rows"]:
        check(f"{row['job_id']} 有 title", bool(row.get("title")))
        check(f"{row['job_id']} 有 status", bool(row.get("status")))

    # gen 过的岗位应该 status=generated
    gen_rows = [r for r in res["rows"] if r.get("status") == "generated"]
    check("前两个岗位已生成", len(gen_rows) >= 2, str([r.get("status") for r in res["rows"]]))
    for r in gen_rows:
        check(f"{r['job_id']} 有 pages", r.get("pages") is not None)

    # 只匹配没 gen 的应该 status=matched
    # job_ids[2] 是保研岗位，没 gen 过
    matched_row = [r for r in res["rows"] if r["job_id"] == job_ids[2]]
    if matched_row:
        check("第三个岗位是 matched", matched_row[0].get("status") == "matched", str(matched_row[0].get("status")))

    # 混入不存在的
    res2 = pipeline.compare([job_ids[0], "job_不存在"])
    check("混入不存在", res2["total"] == 2)
    not_found = [r for r in res2["rows"] if r.get("status") == "job_not_found"]
    check("不存在的标 job_not_found", len(not_found) == 1)

    # 混入没匹配的岗位（先不 match，直接 compare）
    res3 = pipeline.add_job(kind="internship", title="未匹配岗位", org="某公司", raw_text="测试")
    unmatch_jid = res3["job"]["id"]
    res4 = pipeline.compare([job_ids[0], unmatch_jid])
    no_match = [r for r in res4["rows"] if r.get("status") == "no_match"]
    check("没匹配的标 no_match", len(no_match) == 1)

    # ── 集成：batch_match → rank → batch_generate → compare 全链路 ──

    print("\n[6] 全链路：match → rank → gen → compare")
    # rank 结果应该和 compare 的分数一致
    rank_res = pipeline.rank_jobs()
    comp_res = pipeline.compare(job_ids)
    rank_map = {it["job_id"]: it["avg_score"] for it in rank_res["items"]}
    for row in comp_res["rows"]:
        if row.get("status") in ("matched", "generated") and row["job_id"] in rank_map:
            # compare 的 top_score 是最高分，rank 的 avg_score 是 top8 均分
            # top_score 应该 >= avg_score
            check(
                f"{row['job_id']} top≥avg",
                row.get("top_score", 0) >= rank_map[row["job_id"]] - 0.1,
                f"top={row.get('top_score')} avg={rank_map[row['job_id']]}",
            )

    print(f"\n通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    if FAIL:
        print("失败项：" + "、".join(FAIL))
        return 1
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass
    raise SystemExit(main())
