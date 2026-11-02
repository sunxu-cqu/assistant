"""界面层冒烟：不开浏览器，直接打 HTTP 接口。

为什么单独测这一层：前端出问题的方式和 CLI 完全不同——CLI 崩了有堆栈，
网页崩了只有一个白屏。这里把「页面能打开 / 数据能拿到 / 动作能执行 /
越界访问被挡住」四件事都钉住，改前端时不用靠肉眼点。

跑法：
    ./.venv/Scripts/python.exe tests/web_smoke.py
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SB = ROOT / "tests" / "sandbox" / "web"

os.environ["RESUME_ASSISTANT_MOCK"] = "1"
os.environ["RESUME_ASSISTANT_DATA"] = str(SB / "data")
os.environ["RESUME_ASSISTANT_WORKSPACE"] = str(SB / "workspace")

sys.path.insert(0, str(ROOT))

PASS: list[str] = []
FAIL: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(("  ok   " if cond else "  FAIL ") + name + (f"  -> {detail}" if detail and not cond else ""))


def seed() -> None:
    import yaml

    shutil.rmtree(SB, ignore_errors=True)
    (SB / "data" / "evidence").mkdir(parents=True)
    shutil.copy(ROOT / "data" / "profile.example.yaml", SB / "data" / "profile.yaml")
    (SB / "data" / "evidence" / "a.yaml").write_text(
        yaml.safe_dump(
            {
                "_source": {"folder": "推荐系统"},
                "items": [
                    {
                        "id": "ev_web_1",
                        "kind": "project",
                        "title": "双通道召回推荐系统",
                        "role": "负责人",
                        "claim": "冷启动点击率从 2.1% 提升到 3.4%",
                        "period": "2025.03 ~ 2025.06",
                        "raw_excerpt": "2.1% → 3.4%",
                        "raw_numbers": ["2.1", "3.4"],
                        "metrics": [{"name": "点击率", "value": "3.4", "unit": "%", "source": "实验记录"}],
                        "domain_tags": ["推荐系统"],
                        "skill_tags": ["PyTorch"],
                    }
                ],
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )


def main() -> int:
    seed()

    from resume_assistant.web.server import make_server

    srv = make_server(port=0)
    port = srv.server_address[1]
    base = f"http://127.0.0.1:{port}"
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    time.sleep(0.3)

    def get(path: str) -> tuple[int, bytes, str]:
        try:
            with urllib.request.urlopen(base + path, timeout=30) as r:
                return r.status, r.read(), r.headers.get("Content-Type", "")
        except urllib.error.HTTPError as e:
            return e.code, e.read(), e.headers.get("Content-Type", "")

    def post(path: str, body: dict | str) -> tuple[int, dict]:
        raw = body.encode("utf-8") if isinstance(body, str) else json.dumps(body).encode("utf-8")
        req = urllib.request.Request(base + path, data=raw, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    print("\n[1] 页面与首屏数据")
    code, body, ctype = get("/")
    html = body.decode("utf-8", "replace")
    check("首页 200", code == 200, str(code))
    check("首页是 HTML", "text/html" in ctype, ctype)
    check("首页含标题", "简历助手" in html, html[:80])
    check("首页不含外链 CDN（离线可用）", "http://" not in html.split("<script>")[0].replace("http://www.w3.org", ""), "有外链")

    code, body, _ = get("/api/overview")
    data = json.loads(body)
    check("overview 200", code == 200, str(code))
    check("overview 带看板", "columns" in data.get("board", {}), str(list(data.keys()))[:120])
    check("overview 带岗位", isinstance(data.get("jobs"), list), str(type(data.get("jobs"))))
    check("overview 带自检", "checks" in data.get("doctor", {}), str(list(data.get("doctor", {}))[:80]))
    check("overview 带档案", data.get("profile", {}).get("ok") is True, str(data.get("profile"))[:120])

    print("\n[2] 错误路径都要有结构化 JSON，不能白屏")
    code, body, ctype = get("/api/nope")
    check("未知接口 404", code == 404, str(code))
    check("未知接口是 JSON", "application/json" in ctype, ctype)
    check("未知接口有中文提示", "没有这个接口" in json.loads(body)["error"], body[:120])

    code, res = post("/api/jobs", "not-json")
    check("坏 JSON 400", code == 400, str(code))
    check("坏 JSON 有提示", "合法 JSON" in res.get("error", ""), str(res))

    code, res = post("/api/jobs", {"kind": "实习", "raw_text": "x"})
    check("非法 kind 不 500", code == 200, str(code))
    check("非法 kind 返回结构化错误", res.get("ok") is False and "internship" in res.get("error", ""), str(res)[:160])

    print("\n[3] 写命令的正路：录岗 → 匹配 → 生成 → 状态")
    code, res = post(
        "/api/jobs",
        {
            "kind": "internship",
            "org": "某厂",
            "title": "推荐算法实习",
            "raw_text": "岗位职责：召回排序。任职要求：熟悉 Python、PyTorch，有推荐系统项目经验。",
        },
    )
    check("录岗成功", res.get("ok") is True, str(res)[:160])
    job_id = res.get("job", {}).get("id", "")

    code, res = post(f"/api/jobs/{urllib.parse.quote(job_id)}/match", {})
    check("匹配成功", res.get("ok") is True, str(res)[:160])
    check("匹配有 hits", isinstance(res.get("report", {}).get("hits"), list), str(res)[:120])

    code, res = post(f"/api/jobs/{urllib.parse.quote(job_id)}/gen", {"template": "single_column"})
    check("生成简历成功", res.get("ok") is True, str(res)[:200])
    pdf = res.get("pdf_path") or ""
    check("产出 PDF 路径", bool(pdf) and Path(pdf).exists(), pdf)
    app_id = res.get("application_id", "")

    code, body, ctype = get("/api/file?path=" + urllib.parse.quote(pdf))
    check("PDF 预览 200", code == 200, str(code))
    check("PDF 预览类型正确", "application/pdf" in ctype, ctype)
    check("PDF 预览有内容", len(body) > 5000, str(len(body)))

    code, body, _ = get("/api/file?path=" + urllib.parse.quote("C:/Windows/win.ini"))
    check("越界文件被拒", code == 403, str(code))

    print("\n[3.5] 模板预览图：选模板前先看版式")
    code, body, _ = get("/api/templates/two_column/preview")
    prev = json.loads(body)
    check("预览接口 200", code == 200, str(code))
    check("预览返回 png 路径", bool(prev.get("png_path")) and Path(prev["png_path"]).exists(), str(prev)[:160])
    check("预览是双栏", prev.get("layout") == "two_column", str(prev.get("layout")))
    code, body, ctype = get("/api/file?path=" + urllib.parse.quote(prev.get("png_path") or ""))
    check("预览图能取到", code == 200 and "image/png" in ctype, f"{code} {ctype}")
    check("预览图不是空图", len(body) > 10_000, str(len(body)))
    code, body, _ = get("/api/templates/nope/preview")
    bad = json.loads(body)
    check("模板不存在不 500", code == 200 and bad.get("ok") is False, str(bad)[:160])
    check("模板不存在给出可用模板", "可用模板" in (bad.get("hint") or ""), str(bad.get("hint"))[:120])

    code, res = post(f"/api/apps/{urllib.parse.quote(app_id)}/note", {"note": "HR 说下周约面"})
    check("记一笔成功", res.get("ok") is True, str(res)[:160])

    code, res = post(f"/api/apps/{urllib.parse.quote(app_id)}/advance", {"to_status": "draft"})
    check("非法状态转移被拒", res.get("ok") is False, str(res)[:160])

    code, res = post(f"/api/apps/{urllib.parse.quote(app_id)}/advance", {"to_status": "sent", "note": "邮件已发"})
    check("合法推进成功", res.get("ok") is True, str(res)[:160])

    code, body, _ = get(f"/api/apps/{urllib.parse.quote(app_id)}/timeline")
    tl = json.loads(body)
    check("时间线可读", tl.get("ok") is True and len(tl.get("events", [])) >= 3, str(tl)[:160])

    print("\n[3b] 保研套磁 PPT")
    check("前端有套磁 PPT 入口", "openDeckModal" in html, "ui.html 里找不到 openDeckModal")
    check("前端有模板选择入口", "openGenModal" in html, "ui.html 里找不到 openGenModal")
    check("前端有模板选择入口", "openGenModal" in html, "ui.html 里找不到 openGenModal")

    code, res = post(
        "/api/jobs",
        {
            "kind": "baoyan",
            "org": "某课题组",
            "mentor": "张老师",
            "title": "推荐系统方向学硕",
            "raw_text": "课题组招收推免生，方向推荐系统。要求熟悉 Python、PyTorch，有推荐系统项目经验。",
        },
    )
    check("录保研岗成功", res.get("ok") is True, str(res)[:160])
    bjob = res.get("job", {}).get("id", "")

    code, res = post(f"/api/jobs/{urllib.parse.quote(bjob)}/deck", {"template": "baoyan_brief"})
    check("生成 PPT 成功", res.get("ok") is True, str(res)[:200])
    pptx = res.get("pptx_path") or ""
    check("产出 pptx 路径", bool(pptx) and Path(pptx).exists(), pptx)
    check("页数大于 0", (res.get("slides") or 0) > 0, str(res.get("slides")))
    check("第一页是封面", (res.get("outline") or [{}])[0].get("type") == "cover",
          str(res.get("outline"))[:160])

    code, body, ctype = get("/api/file?path=" + urllib.parse.quote(pptx))
    check("PPT 可下载", code == 200, str(code))
    check("下载内容非空", len(body) > 5000, str(len(body)))

    code, res = post(f"/api/jobs/{urllib.parse.quote(bjob)}/deck", {"template": "nope"})
    check("坏模板返回结构化错误", res.get("ok") is False and "模板" in res.get("error", ""), str(res)[:160])

    print("\n[4] 知识库接口")
    code, res = post("/api/kb/build", {"force": True})
    check("kb build 成功", res.get("ok") is not False, str(res)[:160])
    code, body, _ = get("/api/kb/review")
    rev = json.loads(body)
    check("kb review 有 findings 字段", "findings" in rev, str(list(rev.keys()))[:120])

    print("\n[4.5] 批量操作接口")
    # 录第二个岗位，凑够两个做批量
    code, res2 = post(
        "/api/jobs",
        {
            "kind": "internship",
            "org": "某厂2",
            "title": "后端开发实习",
            "raw_text": "负责后端 API。要求 Java、MySQL、分布式。",
        },
    )
    job_id2 = res2.get("job", {}).get("id", "")
    check("录第二个岗位", res2.get("ok") is True, str(res2)[:120])

    # batch match
    code, res = post("/api/batch/match", {"job_ids": [job_id, job_id2]})
    check("batch match 200", code == 200, str(code))
    check("batch match ok", res.get("ok") is True, str(res)[:200])
    check("batch match total=2", res.get("total") == 2, str(res.get("total")))
    check("batch match succeeded=2", res.get("succeeded") == 2, str(res.get("succeeded")))

    # rank
    code, body, _ = get("/api/rank?top=5")
    rank = json.loads(body)
    check("rank 200", code == 200, str(code))
    check("rank ok", rank.get("ok") is True, str(rank)[:200])
    check("rank 有 items", isinstance(rank.get("items"), list), str(rank)[:120])

    # compare
    code, res = post("/api/compare", {"job_ids": [job_id, job_id2]})
    check("compare 200", code == 200, str(code))
    check("compare ok", res.get("ok") is True, str(res)[:200])
    check("compare total=2", res.get("total") == 2, str(res.get("total")))
    check("compare rows 有 status", all("status" in r for r in res.get("rows", [])), str(res.get("rows"))[:200])

    # batch generate（用 mock，会比较慢但能过）
    code, res = post("/api/batch/generate", {"job_ids": [job_id2], "template": "single_column"})
    check("batch gen 200", code == 200, str(code))
    check("batch gen ok", res.get("ok") is True, str(res)[:200])
    check("batch gen total=1", res.get("total") == 1, str(res.get("total")))
    gen_item = (res.get("results") or [{}])[0]
    check("batch gen 成功项有 pdf", gen_item.get("ok") and bool(gen_item.get("pdf_path")), str(gen_item)[:200])

    print("\n[5] 并发与稳定性")
    results: list[int] = []

    def hit() -> None:
        try:
            with urllib.request.urlopen(base + "/api/overview", timeout=20) as r:
                results.append(r.status)
        except Exception:  # noqa: BLE001
            results.append(0)

    threads = [threading.Thread(target=hit) for _ in range(8)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    check("8 并发请求全 200", results.count(200) == 8, str(results))

    # ---- 前端渲染：不开浏览器，用 node 直接跑 ui.html 的内联脚本 ----
    # 截图通道在这台机器上不可用（Edge 无头产不出文件），只能退一步验证
    # 「有数据时渲染函数不崩、字段没取错」。空数据会掩盖「字段名写错」——
    # 那种错接口照样 200，页面上是 undefined。
    print("\n[9] 前端渲染探针")
    node = shutil.which("node")
    if not node:
        print("  skip 没装 node，跳过（不影响其它项）")
    else:
        snap = SB / "overview.json"
        with urllib.request.urlopen(base + "/api/overview", timeout=20) as r:
            snap.write_text(r.read().decode("utf-8"), encoding="utf-8")
        proc = subprocess.run(
            [node, str(ROOT / "tests" / "ui_render.js"), str(snap)],
            cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8",
        )
        out = proc.stdout or ""
        lines = [ln.strip() for ln in out.splitlines() if ln.strip().startswith(("ok", "FAIL"))]
        for ln in lines:
            name = ln[2:].strip() if ln.startswith("ok") else ln[4:].strip()
            check(f"渲染 · {name}", ln.startswith("ok"))
        check("渲染探针跑到最后", "失败" in out and proc.returncode == 0, (proc.stderr or "")[-400:])
        if not lines:
            check("渲染探针有输出", False, (proc.stderr or "")[-400:])

    srv.shutdown()
    srv.server_close()

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
