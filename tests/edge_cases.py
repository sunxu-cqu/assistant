"""边界测试：专门喂「不合法的输入」和「真实资料里会出现但测试资料里没有的东西」。

与 smoke.py 的分工：
- smoke.py 走正常流程，确认主链路没坏
- 这个文件只干一件事：把边界撞一遍，看会不会抛堆栈、会不会静默做错

不消耗 API 额度（全程 mock）。

运行：
    ./.venv/Scripts/python.exe tests/edge_cases.py
"""

from __future__ import annotations

import os
import shutil
import sys
import threading
from pathlib import Path

import yaml

os.environ["RESUME_ASSISTANT_MOCK"] = "1"

ROOT = Path(__file__).resolve().parent.parent
_SANDBOX = ROOT / "tests" / "sandbox" / "edge"
os.environ["RESUME_ASSISTANT_DATA"] = str(_SANDBOX / "data")
os.environ["RESUME_ASSISTANT_WORKSPACE"] = str(ROOT / "tests" / "sandbox" / "workspace")

sys.path.insert(0, str(ROOT))

PASS: list[str] = []
FAIL: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(("  ok   " if cond else "  FAIL ") + name + (f"  -> {detail}" if detail and not cond else ""))


def main() -> int:
    shutil.rmtree(_SANDBOX, ignore_errors=True)
    (_SANDBOX / "data").mkdir(parents=True, exist_ok=True)
    shutil.copy(ROOT / "data" / "profile.example.yaml", _SANDBOX / "data" / "profile.yaml")

    from resume_assistant import pipeline
    from resume_assistant.board import parse_deadline
    from resume_assistant.models import AppStatus, Evidence, Job, JobKind
    from resume_assistant.store import db

    # 先塞一条证据：generate_resume 会在「知识库为空」处提前返回，
    # 走不到模板检查那一步
    (_SANDBOX / "data" / "evidence" / "2025-推荐系统项目.yaml").parent.mkdir(parents=True, exist_ok=True)
    (_SANDBOX / "data" / "evidence" / "2025-推荐系统项目.yaml").write_text(
        yaml.safe_dump(
            {
                "_source": {"folder": "2025-推荐系统项目"},
                "items": [
                    {
                        "id": "ev_edge_1",
                        "kind": "project",
                        "title": "推荐系统项目",
                        "claim": "用双通道聚合结构缓解冷启动，点击率从 2.1% 提升到 3.4%",
                        "period": "2025.03 ~ 2025.06",
                        "raw_excerpt": "双通道聚合，冷启动点击率 2.1% → 3.4%",
                        "raw_numbers": ["2.1", "3.4"],
                        "metrics": [{"name": "点击率", "value": "3.4", "unit": "%", "source": "实验记录"}],
                        "skill_tags": ["PyTorch"],
                    }
                ],
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )

    # ------------------------------------------------------------ 1 输入校验
    print("\n[1] 输入校验（不该抛堆栈，要给人话）")
    r = pipeline.add_job(kind="实习", title="x", raw_text="y")
    check("非法 kind 返回结构化错误", r.get("ok") is False and "internship" in r.get("error", ""), str(r))
    r = pipeline.add_job(kind="internship", title="x")
    check("没有正文也没有链接时报错", r.get("ok") is False and r.get("stage") == "input", str(r))

    job = pipeline.add_job(
        kind="internship", title="后端实习生", org="某公司", raw_text="熟悉 Python 与 MySQL，做后端接口开发。"
    )["job"]
    job_id = job["id"]

    r = pipeline.advance("app_nope", "sent")
    check("推进不存在的记录", r.get("ok") is False and "不存在" in r.get("error", ""), str(r))
    r = pipeline.advance("app_nope", "已投递")
    check("非法状态名给中文提示", r.get("ok") is False and "status" not in r.get("error", "").lower()[:40], str(r))
    check("非法状态名列出可选值", "draft" in r.get("error", "") and "offer" in r.get("error", ""), str(r))

    r = pipeline.note_application("app_nope", "x")
    check("备注不存在的记录", r.get("ok") is False, str(r))

    r = pipeline.generate_resume(job_id, template_key="不存在的模板")
    check("未知模板不抛异常", r.get("ok") is False and r.get("stage") == "template", str(r))
    check("未知模板提示可用值", "single_column" in str(r.get("hint", "")), str(r.get("hint")))

    # -------------------------------------------------------- 2 截止日期解析
    print("\n[2] 截止日期解析")
    from datetime import date

    T = date(2026, 10, 28)
    cases = [
        ("2026-11-01", date(2026, 11, 1)),
        ("2026/11/1", date(2026, 11, 1)),
        ("2026.11.1", date(2026, 11, 1)),
        ("2026年11月1日", date(2026, 11, 1)),
        ("11月1日", date(2026, 11, 1)),
        ("1月5日", date(2027, 1, 5)),
        ("20261101", date(2026, 11, 1)),
        ("2026-11-01 18:00 截止", date(2026, 11, 1)),
        ("12月31日", date(2026, 12, 31)),
    ]
    for text, want in cases:
        got = parse_deadline(text, T)
        check(f"解析 {text}", got == want, f"{got} != {want}")

    for text in ("长期有效", "招满即止", "尽快", "", "2026-02-30", "2026-13-01", "面议"):
        check(f"「{text}」不硬猜", parse_deadline(text, T) is None, str(parse_deadline(text, T)))

    # ------------------------------------------------------------ 3 看板边界
    print("\n[3] 看板边界")
    empty = pipeline.board(write_html=False)
    check("空库也能出看板", empty.get("ok") is True and empty["total"] == 0, str(empty)[:100])
    check("空库文本有引导", "还没有投递记录" in empty["text"], empty["text"][:60])

    from resume_assistant import board as board_mod

    j1 = Job(id="job_edge_a", kind=JobKind.INTERNSHIP, title="过期未投", org="A", deadline="2026-10-01")
    db.save_job(j1)
    a1 = db.create_application(j1)
    j2 = Job(id="job_edge_b", kind=JobKind.INTERNSHIP, title="已投且过期", org="B", deadline="2026-10-01")
    db.save_job(j2)
    a2 = db.create_application(j2)
    for st in (AppStatus.MATCHED, AppStatus.COMPOSED, AppStatus.SENT):
        db.transition(a2.id, st)
    j3 = Job(id="job_edge_c", kind=JobKind.INTERNSHIP, title="没有截止日期", org="C")
    db.save_job(j3)
    a3 = db.create_application(j3)

    from datetime import datetime

    bd = board_mod.collect(today=T, now=datetime(2026, 11, 8, 9, 0, 0))
    flat = {it["title"]: it for it in bd["items"]}
    check("过期未投报 error", any(a["level"] == "error" for a in flat["过期未投"]["alerts"]), str(flat["过期未投"]["alerts"]))
    check("已投出的过期不报 error", not any(a["level"] == "error" for a in flat["已投且过期"]["alerts"]), str(flat["已投且过期"]["alerts"]))
    check(
        "没有截止日期不报截止类错误",
        not any(a["level"] == "error" for a in flat["没有截止日期"]["alerts"])
        and flat["没有截止日期"]["days_left"] is None,
        str(flat["没有截止日期"]["alerts"]),
    )

    html = board_mod.render_html(bd)
    check("看板页包含全部卡片", "过期未投" in html and "已投且过期" in html and "没有截止日期" in html)

    # 漏斗不能拿当前状态倒推：建了档又放弃的记录从没投出去过
    j4 = Job(id="job_edge_d", kind=JobKind.INTERNSHIP, title="建了就放弃", org="D")
    db.save_job(j4)
    a4 = db.create_application(j4)
    db.transition(a4.id, AppStatus.WITHDRAWN)

    bd2 = board_mod.collect(today=T, now=datetime(2026, 11, 8, 9, 0, 0))
    f = bd2["funnel"]["internship"]
    check("建档后放弃不算「已投出」", f["sent"] == 1, str(f))
    check("漏斗总数含已放弃", f["total"] == 4, str(f))

    # 岗位标题里带 HTML 的情况（「Java<T>」这类写法真的会出现）
    jx = Job(id="job_edge_xss", kind=JobKind.INTERNSHIP, title="<script>alert(1)</script>", org="A&B")
    db.save_job(jx)
    db.create_application(jx)
    hx = board_mod.render_html(board_mod.collect(today=T, now=datetime(2026, 11, 8, 9, 0, 0)))
    check("看板页转义 HTML", "<script>alert" not in hx and "&lt;script&gt;" in hx, "未转义")
    check("看板页转义 & 符号", "A&amp;B" in hx, "未转义")

    # -------------------------------------------------------- 4 开场白检查器
    print("\n[4] 开场白检查器")
    from resume_assistant.compose import pitch as pitch_mod
    from resume_assistant.models import MatchReport, Pitch, Profile

    prof = Profile(name="张三", phone="13800000000", email="z@example.com")
    evs = [
        Evidence(
            id="ev_1",
            kind="project",
            title="推荐系统项目",
            claim="用双通道聚合把冷启动点击率从 2.1% 提升到 3.4%",
            raw_excerpt="双通道聚合，点击率 2.1% → 3.4%",
            raw_numbers=["2.1", "3.4"],
            period="2025.03 ~ 2025.06",
        )
    ]
    rep = MatchReport(job_id="job_edge_a")
    job_obj = db.get_job("job_edge_a")

    good = Pitch(
        job_id="x",
        short="我是张三，做过推荐系统项目，把冷启动点击率从 2.1% 提升到 3.4%。",
        full=(
            "您好，我是张三，计算机科学与技术专业本科生。\n\n"
            "我最对口的经历是推荐系统项目：用双通道聚合结构缓解冷启动问题，"
            "冷启动用户点击率从 2.1% 提升到 3.4%，并在离线评估中做了完整对照实验。\n\n"
            "张三　13800000000"
        ),
    )
    w = pitch_mod._check(good, job_obj, evs, prof)
    check("正常文案不报警告", not [x for x in w if "编造" in x or "篇幅" in x], str(w))

    bad_num = Pitch(job_id="x", short="准确率 99.9%", full="您好，我是张三，准确率 99.9%。")
    check("编造数字被拦", any("找不到出处" in x for x in pitch_mod._check(bad_num, job_obj, evs, prof)))

    ok_num = Pitch(job_id="x", short="点击率从 2.1% 到 3.4%", full="您好，我是张三，点击率从 2.1% 提升到 3.4%。")
    check("资料里有的数字放行", not any("找不到出处" in x for x in pitch_mod._check(ok_num, job_obj, evs, prof)))

    phone = Pitch(job_id="x", short="电话 13800000000", full="您好，我是张三，电话 13800000000。")
    check("手机号不算编造", not any("找不到出处" in x for x in pitch_mod._check(phone, job_obj, evs, prof)))

    avail = Pitch(job_id="x", short="每周 4 天", full="您好，我是张三，每周 4 天可实习。")
    check("没填可实习时间却写了 → 拦", any("internship_available" in x for x in pitch_mod._check(avail, job_obj, evs, prof)))
    prof2 = prof.model_copy(update={"internship_available": "每周 4 天"})
    check("填了可实习时间 → 放行", not any("internship_available" in x for x in pitch_mod._check(avail, job_obj, evs, prof2)))

    empty_p = Pitch(job_id="x", short="", full="")
    check("空文案报结构问题", sum(1 for x in pitch_mod._check(empty_p, job_obj, evs, prof) if "结构" in x) == 2)

    # ------------------------------------------------------------ 5 扫描器
    print("\n[5] 扫描器边界（真实资料里会出现的东西）")
    from resume_assistant.kb.scanner import read_file, scan_folder

    proj = _SANDBOX / "workspace" / "projects" / "2026-边界"
    proj.mkdir(parents=True, exist_ok=True)
    (proj / "README.md").write_text("# 项目\n做了个后端服务，QPS 1200。", encoding="utf-8")
    (proj / "空文件.txt").write_text("", encoding="utf-8")
    (proj / "视频.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42")
    (proj / "旧报告.doc").write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1")
    (proj / "大数据集.csv").write_bytes(b"a,b\n" + b"1,2\n" * (22 * 1024 * 1024 // 4))

    text, enc, why = read_file(proj / "空文件.txt")
    check("空文件给出原因", not text and "空" in why, why)
    text, enc, why = read_file(proj / "视频.mp4")
    check("视频标为不支持", not text and "不支持" in why, why)
    text, enc, why = read_file(proj / "旧报告.doc")
    check(".doc 提示另存为 docx", "docx" in why, why)
    text, enc, why = read_file(proj / "大数据集.csv")
    check("超大文件跳过而不是读进来", not text and "MB" in why, why)

    pf = scan_folder(proj)
    check("只读到 README", pf.files == ["README.md"], str(pf.files))
    check("跳过原因逐条记录", len(pf.skipped) == 4, str([s["file"] for s in pf.skipped]))

    # ------------------------------------------------------------ 6 编号唯一
    print("\n[6] 编号唯一性")
    from resume_assistant.jobs.fetcher import make_job

    k1 = make_job(kind=JobKind.INTERNSHIP, title="同标题", raw_text="同一份", org="同公司")
    k2 = make_job(kind=JobKind.INTERNSHIP, title="同标题", raw_text="同一份", org="同公司")
    k3 = make_job(kind=JobKind.INTERNSHIP, title="同标题", raw_text="另一份", org="同公司")
    check("同内容幂等", k1.id == k2.id, f"{k1.id} vs {k2.id}")
    check("不同内容不同编号", k1.id != k3.id)
    check("编号里能看出单位", "同公司" in k1.id, k1.id)

    db.save_job(k1)
    db.save_job(k2)
    check("重复保存不产生两条", sum(1 for j in db.list_jobs() if j.id == k1.id) == 1)

    # ------------------------------------------------------------ 7 状态机
    print("\n[7] 状态机边界")
    app = db.create_application(k1)
    r = pipeline.advance(app.id, "offer")
    check("跨步转移被拒绝", r.get("ok") is False and "非法" in r.get("error", ""), str(r))
    r = pipeline.advance(app.id, "draft")
    check("原地不动视为成功", r.get("ok") is True, str(r))
    for st in ("matched", "composed", "sent", "screening", "interview", "offer"):
        pipeline.advance(app.id, st)
    r = pipeline.advance(app.id, "sent")
    check("终态不能再转", r.get("ok") is False, str(r))
    tl = db.app_timeline(app.id)
    check("时间线没有重复记同一状态", [e["status"] for e in tl].count("draft") == 1, str([e["status"] for e in tl]))

    # ------------------------------------------------------------ 8 并发
    print("\n[8] 并发写入")
    app2 = db.create_application(k3)
    errs: list[str] = []

    def note(i: int) -> None:
        try:
            db.add_note(app2.id, f"备注{i}")
        except Exception as exc:  # noqa: BLE001
            errs.append(f"{type(exc).__name__}: {exc}")

    threads = [threading.Thread(target=note, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    notes = [e for e in db.app_timeline(app2.id) if e["note"].startswith("备注")]
    check("8 线程写备注全部落库", len(notes) == 8 and not errs, f"got {len(notes)} errs={errs[:2]}")

    # 同一岗位并发建档只能有一条
    db.save_job(k1)
    ids: list[str] = []

    def build_app() -> None:
        try:
            ids.append(db.create_application(k1).id)
        except Exception as exc:  # noqa: BLE001
            errs.append(f"create: {type(exc).__name__}")

    ts = [threading.Thread(target=build_app) for _ in range(6)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    check("并发建档只有一条记录", len(set(ids)) == 1 and not [e for e in errs if "create" in e], f"ids={set(ids)}")

    print("\n[9] CLI 退出码与输入容错")
    import subprocess

    env = {
        **os.environ,
        "RESUME_ASSISTANT_MOCK": "1",
        "RESUME_ASSISTANT_DATA": str(_SANDBOX / "data"),
        "RESUME_ASSISTANT_WORKSPACE": str(ROOT / "tests" / "sandbox" / "workspace"),
        "PYTHONIOENCODING": "utf-8",
    }

    def run(*args: str) -> tuple[int, str]:
        p = subprocess.run(
            [sys.executable, "-m", "resume_assistant.cli", *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            cwd=str(ROOT),
        )
        return p.returncode, (p.stdout or "") + (p.stderr or "")

    code, out = run("job", "add", "-k", "实习", "--text", "x")
    check("非法 kind 退出码非 0", code != 0, str(code))
    check("非法 kind 不打印堆栈", "Traceback" not in out, out[-200:])

    gbk = _SANDBOX / "gbk_jd.txt"
    gbk.write_bytes("岗位职责：负责后端接口开发。任职要求：熟悉 Python、MySQL。".encode("gb18030"))
    code, out = run("job", "add", "-k", "internship", "--file", str(gbk), "--org", "GBK公司", "--title", "GBK岗位")
    check("GBK 编码的 JD 文件能读", code == 0 and "GBK岗位" in out, out[-200:])

    code, out = run("job", "add", "-k", "internship", "--file", "根本不存在.txt")
    check("JD 文件不存在时退出码非 0", code != 0 and "不存在" in out, out[-200:])

    ev_id = "ev_edge_1"
    code, out = run("kb", "fix", ev_id, "-w", "0")
    got = next((e.weight for e in pipeline.load_all_evidence() if e.id == ev_id), None)
    check("kb fix --weight 0 真的写进去", code == 0 and got == 0.0, f"weight={got}")

    code, out = run("board", "--json", "--no-html")
    check("board --json 输出可解析", code == 0 and '"total"' in out, out[-200:])

    print("\n[10] 损坏的 YAML（静默丢数据是最坏的失败）")
    from resume_assistant import doctor as doctor_mod
    from resume_assistant.config import ConfigError

    broken = _SANDBOX / "data" / "evidence" / "损坏.yaml"
    broken.write_text("items:\n  - id: ev_bad\n    kind: 根本不存在的类型\n", encoding="utf-8")
    stats = pipeline.knowledge_stats()
    check("坏证据文件被统计出来", any(b["file"] == "损坏.yaml" for b in stats["broken_files"]), str(stats["broken_files"]))
    check("统计里带提示", "不会被用上" in (stats.get("hint") or ""), str(stats.get("hint")))
    doc = doctor_mod.run()
    check("doctor 报坏证据", any("读不出来" in c["name"] and c["level"] == "fail" for c in doc["checks"]))
    broken.unlink()
    check("删掉之后不再报", not pipeline.knowledge_stats()["broken_files"])

    pf = _SANDBOX / "data" / "profile.yaml"
    backup = pf.read_text(encoding="utf-8")
    pf.write_text("name: 张三\neducation: [\n  - school: 某大学\n", encoding="utf-8")
    err = None
    try:
        pipeline.load_profile()
    except ConfigError as exc:
        err = str(exc)
    except Exception as exc:  # noqa: BLE001
        err = f"WRONG:{type(exc).__name__}"
    check("profile 写坏了给出行号而不是堆栈", bool(err) and "行" in err, str(err))

    pf.write_text("name: 张三\neducation:\n  - school: 某大学\n    gpa: [1, 2]\n", encoding="utf-8")
    err = None
    try:
        pipeline.load_profile()
    except ConfigError as exc:
        err = str(exc)
    except Exception as exc:  # noqa: BLE001
        err = f"WRONG:{type(exc).__name__}"
    check("字段类型不对时指出是哪个字段", bool(err) and "gpa" in err, str(err))
    pf.write_text(backup, encoding="utf-8")

    # -------------------------------------------------- 11 闸门与文案的边界
    print("\n[11] 数字闸门与文案兜底")
    from resume_assistant.kb.builder import load_all_evidence
    from resume_assistant.models import Bullet, IREntry, IRSection, ResumeIR
    from resume_assistant.validate.linter import lint

    evs = load_all_evidence()
    prof = pipeline.load_profile()

    def _one(text: str) -> list[str]:
        ir = ResumeIR(
            job_id="j",
            template="single_column",
            contact={"name": prof.name or "张三"},
            sections=[
                IRSection(
                    type="projects",
                    title="项目经历",
                    entries=[IREntry(heading="推荐系统项目", meta="", bullets=[Bullet(text=text, evidence_id="ev_edge_1")])],
                )
            ],
        )
        return lint(ir, evs, prof).errors

    # 小于 10 的裸数字当计数放过；带 % 的比例数字一律要出处
    check("「3 个项目」这类计数不误报", not _one("负责 3 个模块的联调"), str(_one("负责 3 个模块的联调")))
    check("编造的 8.7% 被拦", any("8.7" in e for e in _one("把点击率从 2.1% 提到 8.7%")), str(_one("把点击率从 2.1% 提到 8.7%")))
    check("编造的「3 倍」被拦", any("3" in e for e in _one("吞吐提升 3 倍")), str(_one("吞吐提升 3 倍")))
    check("有出处的 3.4% 放行", not _one("把点击率做到 3.4%"), str(_one("把点击率做到 3.4%")))

    # rerender 换模板时给可用列表，而不是把 FileNotFoundError 塞进括号
    job_id = pipeline.add_job(kind="internship", org="某厂", title="算法实习", raw_text="要求：Python，推荐系统经验。")["job"]["id"]
    gen = pipeline.generate_resume(job_id)
    check("前置：gen 成功", gen.get("ok") is True, str(gen)[:120])
    check("gen 报告页数", gen.get("pages") == 1, str(gen.get("pages")))
    check("一页简历不报页数警告", not any("页数" in w for w in gen["lint"]["warnings"]), str(gen["lint"]["warnings"]))

    from resume_assistant.validate.linter import LintResult

    fake = LintResult()
    pipeline._warn_pages(fake, 2, JobKind.INTERNSHIP)
    check("实习简历 2 页要提醒", any("页数" in w for w in fake.warnings), str(fake.warnings))
    fake2 = LintResult()
    pipeline._warn_pages(fake2, 2, JobKind.BAOYAN)
    check("保研简历 2 页不提醒", not fake2.warnings, str(fake2.warnings))
    bad = pipeline.rerender(gen["application_id"], template_key="没有这个")
    check("rerender 坏模板不报两遍错", bad.get("error", "").count("模板不存在") == 1, str(bad.get("error")))
    check("rerender 坏模板给可用列表", "single_column" in (bad.get("hint") or ""), str(bad.get("hint")))

    # 套磁信：没导师姓名要提示，mock 主题不许出现「--」
    bj = pipeline.add_job(kind="baoyan", title="推荐系统方向", raw_text="研究方向：推荐系统、冷启动。")["job"]["id"]
    letter = pipeline.generate_letter(bj)
    check("没导师姓名时给提示", any("导师姓名" in w for w in letter.get("warnings", [])), str(letter.get("warnings"))[:200])
    subj = (letter.get("letter") or {}).get("subject", "")
    check("主题不出现空段「--」", "--" not in subj, subj)

    # ---------------------------------------------------- 12 资格校验
    print("\n[12] 资格校验（在读学生不该被判届别不符）")
    from resume_assistant.models import Education, Profile
    from resume_assistant.validate.eligibility import _grad_year
    from resume_assistant.validate.eligibility import check as elig_check

    def _prof(period: str, major: str = "计算机科学与技术") -> Profile:
        return Profile(
            name="张三",
            education=[Education(school="某大学", major=major, degree="本科", period=period)],
        )

    check("「至今」判为在读", _grad_year(_prof("2023.09 ~ 至今")) == 9999, str(_grad_year(_prof("2023.09 ~ 至今"))))
    check("「2022 至今」判为在读", _grad_year(_prof("2022 至今")) == 9999, str(_grad_year(_prof("2022 至今"))))
    check("明确毕业年份照常推断", _grad_year(_prof("2022.09 ~ 2026.06")) == 2026, str(_grad_year(_prof("2022.09 ~ 2026.06"))))

    j2027 = Job(id="j2027", kind=JobKind.INTERNSHIP, title="算法", raw_text="要求：2027届毕业生，计算机相关专业")
    issues = elig_check(j2027, _prof("2023.09 ~ 至今")).to_list()
    check("在读不误报届别不符", not any(i["level"] == "fail" for i in issues), str(issues))
    issues2 = elig_check(j2027, _prof("2022.09 ~ 2026.06")).to_list()
    check("真不符才报 fail", any(i["level"] == "fail" for i in issues2), str(issues2))

    # ------------------------------------------- 13 真实资料带来的坑
    print("\n[13] 真实资料带来的坑（备份文件夹 / 大日志 / 单位重复）")
    from resume_assistant.compose.ir import _dedupe_evidence
    from resume_assistant.kb.scanner import MAX_CHARS_PER_FILE, scan_folder
    from resume_assistant.models import Metric

    # 抽取器常把「18%→9%」写进 value 又填 unit="%"，拼出来是 9%%，会被模型抄进简历
    check("value 自带 % 时不重复加单位", Metric(name="x", value="18%→9%", unit="%").rendered() == "18%→9%")
    check("value 不带单位时正常拼接", Metric(name="x", value="3.4", unit="%").rendered() == "3.4%")
    check("非百分比单位照常拼接", Metric(name="x", value="42", unit="ms").rendered() == "42ms")

    def _ev(eid: str, title: str, claim: str, period: str = "2025.03 ~ 2025.06", metrics: int = 0) -> Evidence:
        return Evidence(
            id=eid,
            kind="project",
            title=title,
            claim=claim,
            period=period,
            metrics=[Metric(name=f"m{i}", value="1", unit="个", source="x") for i in range(metrics)],
        )

    # 备份文件夹：同标题、措辞不同（LLM 两次抽取不会一字不差）
    dup = _dedupe_evidence(
        [
            _ev("a", "双通道召回推荐系统", "双通道召回将冷启动点击率从 2.1% 提升至 3.4%"),
            _ev("b", "双通道召回推荐系统", "双通道召回+热度负采样，点击率 2.1%→3.4%"),
        ]
    )
    check("同标题不同措辞判为重复", len(dup) == 1, str([e.id for e in dup]))
    keep = _dedupe_evidence(
        [
            _ev("a", "双通道召回推荐系统", "点击率 2.1%→3.4%", metrics=1),
            _ev("b", "双通道召回推荐系统", "点击率 2.1%→3.4%", metrics=3),
        ]
    )
    check("重复时留信息量大的那条", len(keep) == 1 and keep[0].id == "b", str([(e.id, len(e.metrics)) for e in keep]))
    diff = _dedupe_evidence(
        [
            _ev("a", "双通道召回推荐系统", "点击率 2.1%→3.4%"),
            _ev("b", "冷启动属性对齐", "NDCG 提升 11.8%"),
        ]
    )
    check("不同项目不被误合并", len(diff) == 2, str([e.id for e in diff]))
    same_title_diff = _dedupe_evidence(
        [
            _ev("a", "课程大作业", "用 B+ 树做前缀检索，P99 从 380ms 降到 42ms", period="2023.09 ~ 2023.12"),
            _ev("b", "课程大作业", "用 XGBoost 做房价预测，RMSE 从 0.31 降到 0.22", period="2024.09 ~ 2024.12"),
        ]
    )
    check("同名但不同项目不合并", len(same_title_diff) == 2, str([e.id for e in same_title_diff]))

    # 一个 6MB 的训练日志不该把整个项目的字符预算吃光
    big = _SANDBOX / "workspace" / "projects" / "大日志项目"
    shutil.rmtree(big, ignore_errors=True)
    big.mkdir(parents=True)
    (big / "train.log").write_text("epoch 1 loss 0.9\n" * 60_000, encoding="utf-8")
    (big / "说明.md").write_text("把召回率从 0.62 提到 0.78，用双塔模型。", encoding="utf-8")
    pf = scan_folder(big)
    check("单文件超过上限被截断", f"超过 {MAX_CHARS_PER_FILE} 字" in pf.text, str(pf.chars))
    check("大日志没挤掉真正有用的文件", "0.78" in pf.text, pf.text[-200:])
    shutil.rmtree(big, ignore_errors=True)

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
