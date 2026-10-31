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

    code, res = post(f"/api/apps/{urllib.parse.quote(app_id)}/note", {"note": "HR 说下周约面"})
    check("记一笔成功", res.get("ok") is True, str(res)[:160])

    code, res = post(f"/api/apps/{urllib.parse.quote(app_id)}/advance", {"to_status": "draft"})
    check("非法状态转移被拒", res.get("ok") is False, str(res)[:160])

    code, res = post(f"/api/apps/{urllib.parse.quote(app_id)}/advance", {"to_status": "sent", "note": "邮件已发"})
    check("合法推进成功", res.get("ok") is True, str(res)[:160])

    code, body, _ = get(f"/api/apps/{urllib.parse.quote(app_id)}/timeline")
    tl = json.loads(body)
    check("时间线可读", tl.get("ok") is True and len(tl.get("events", [])) >= 3, str(tl)[:160])

    print("\n[4] 知识库接口")
    code, res = post("/api/kb/build", {"force": True})
    check("kb build 成功", res.get("ok") is not False, str(res)[:160])
    code, body, _ = get("/api/kb/review")
    rev = json.loads(body)
    check("kb review 有 findings 字段", "findings" in rev, str(list(rev.keys()))[:120])

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
