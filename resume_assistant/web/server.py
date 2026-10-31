"""本地 Web 界面：给「简历助手」做一个能点得动的壳。

为什么不引 FastAPI / Flask：这个工具的所有能力都在 pipeline.py 里，界面只是
一层壳。为一个本地单人工具引入 web 框架和它的依赖树（uvicorn、starlette…），
换来的是多一个装不上的可能。标准库 http.server 足够，而且零安装。

约定：
- 只绑 127.0.0.1，不对外监听（简历资料是隐私）
- 静态资源只有一个 HTML，内联 CSS/JS，离线可用
- 所有动作都同步执行，前端显示 loading；gen 慢是模型慢，不是服务端慢
"""

from __future__ import annotations

import json
import mimetypes
import re
import threading
import traceback
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, unquote, urlparse

from ..config import OUTPUT_DIR, ROOT
from .. import pipeline

UI_PATH = Path(__file__).resolve().parent / "ui.html"



# ------------------------------------------------------------------ 数据组装


def overview() -> dict[str, Any]:
    """首屏要的全部数据，一次拿完。"""
    from .. import doctor as doctor_mod
    from ..match import embed

    board = pipeline.board(write_html=False)
    kb = pipeline.knowledge_stats()
    jobs = pipeline.list_jobs()
    apps = pipeline.list_applications()

    profile: dict[str, Any] = {"ok": False}
    try:
        p = pipeline.load_profile()
        profile = {
            "ok": True,
            "name": p.name,
            "school": p.education[0].school if p.education else "",
            "major": p.education[0].major if p.education else "",
            "degree": p.education[0].degree if p.education else "",
            "gpa": p.education[0].gpa if p.education else "",
            "rank": p.education[0].gpa_rank if p.education else "",
            "internship_available": p.internship_available,
            "example": (p.name or "") in ("张三", "李四", "王五"),
        }
    except Exception as exc:  # noqa: BLE001
        profile = {"ok": False, "error": str(exc)[:200]}

    return {
        "ok": True,
        "board": board,
        "kb": kb,
        "jobs": jobs,
        "apps": apps,
        "profile": profile,
        "templates": pipeline.all_templates(),
        "doctor": doctor_mod.run(),
        "embed": embed.info(),
    }


# ------------------------------------------------------------------ 路由表

Route = tuple[str, str, Callable[..., dict[str, Any]]]

ROUTES: list[Route] = [
    ("GET", r"^/api/overview$", lambda req, m: overview()),
    ("GET", r"^/api/board$", lambda req, m: pipeline.board(write_html=True)),
    ("POST", r"^/api/board$", lambda req, m: pipeline.board(write_html=True)),
    ("POST", r"^/api/kb/build$", lambda req, m: pipeline.build_knowledge_base(force=bool(req.body.get("force")))),
    ("GET", r"^/api/kb/review$", lambda req, m: pipeline.review_knowledge_base(write_html=True)),
    ("POST", r"^/api/kb/fix$", lambda req, m: pipeline.fix_evidence(
        req.body.get("evidence_id", ""),
        kind=req.body.get("kind"),
        weight=req.body.get("weight"),
        title=req.body.get("title"),
        period=req.body.get("period"),
    )),
    ("POST", r"^/api/jobs$", lambda req, m: pipeline.add_job(
        kind=req.body.get("kind", "internship"),
        title=req.body.get("title", ""),
        org=req.body.get("org", ""),
        mentor=req.body.get("mentor", ""),
        url=req.body.get("url", ""),
        raw_text=req.body.get("raw_text", ""),
        deadline=req.body.get("deadline", ""),
        location=req.body.get("location", ""),
    )),
    ("POST", r"^/api/jobs/(?P<id>[^/]+)/match$", lambda req, m: pipeline.run_match(m["id"])),
    ("POST", r"^/api/jobs/(?P<id>[^/]+)/gen$", lambda req, m: pipeline.generate_resume(
        m["id"], template_key=req.body.get("template", "single_column")
    )),
    ("POST", r"^/api/jobs/(?P<id>[^/]+)/letter$", lambda req, m: pipeline.generate_letter(m["id"])),
    ("POST", r"^/api/jobs/(?P<id>[^/]+)/pitch$", lambda req, m: pipeline.generate_pitch(m["id"])),
    ("POST", r"^/api/apps/(?P<id>[^/]+)/advance$", lambda req, m: pipeline.advance(
        m["id"], req.body.get("to_status", ""), req.body.get("note", "")
    )),
    ("POST", r"^/api/apps/(?P<id>[^/]+)/note$", lambda req, m: pipeline.note_application(
        m["id"], req.body.get("note", "")
    )),
    ("GET", r"^/api/apps/(?P<id>[^/]+)/timeline$", lambda req, m: pipeline.application_timeline(m["id"])),
]

_COMPILED = [(method, re.compile(pat), fn) for method, pat, fn in ROUTES]


# ------------------------------------------------------------------ 请求处理


class Handler(BaseHTTPRequestHandler):
    server_version = "ResumeAssistant/1.0"

    # 别把访问日志刷满终端
    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
        if "/api/" in (args[0] if args else ""):
            return

    # ---- 基础输出

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass  # 浏览器提前关掉了连接，不值得记一条错误

    def _json(self, data: dict[str, Any], code: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
        self._send(code, body, "application/json; charset=utf-8")

    def _html(self, text: str, code: int = 200) -> None:
        self._send(code, text.encode("utf-8"), "text/html; charset=utf-8")

    # ---- GET

    def do_GET(self) -> None:  # noqa: N802
        url = urlparse(self.path)
        path = unquote(url.path)

        if path in ("/", "/index.html"):
            if not UI_PATH.exists():
                self._html("<h1>ui.html 缺失</h1>", 500)
                return
            self._html(UI_PATH.read_text(encoding="utf-8"))
            return

        if path == "/api/file":
            self._serve_file(parse_qs(url.query).get("path", [""])[0])
            return

        req = _Request(body={}, query=parse_qs(url.query))
        self._dispatch("GET", path, req)

    # ---- POST

    def do_POST(self) -> None:  # noqa: N802
        url = urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw.decode("utf-8") or "{}")
            if not isinstance(body, dict):
                body = {}
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._json({"ok": False, "error": "请求体不是合法 JSON"}, 400)
            return
        self._dispatch("POST", unquote(url.path), _Request(body=body, query=parse_qs(url.query)))

    # ---- 分发

    def _dispatch(self, method: str, path: str, req: "_Request") -> None:
        for m, pat, fn in _COMPILED:
            if m != method:
                continue
            hit = pat.match(path)
            if not hit:
                continue
            try:
                res = fn(req, hit.groupdict())
            except Exception as exc:  # noqa: BLE001
                # 界面不能因为一个动作炸掉整页：把异常变成一条结构化错误
                res = {
                    "ok": False,
                    "error": f"{type(exc).__name__}: {exc}",
                    "trace": traceback.format_exc()[-1200:],
                }
            self._json(res if isinstance(res, dict) else {"ok": True, "data": res})
            return
        self._json({"ok": False, "error": f"没有这个接口：{method} {path}"}, 404)

    # ---- PDF / 图片预览

    def _serve_file(self, raw: str) -> None:
        """只服务 data/output 下的文件。

        多个投递的 PDF 都叫 resume.pdf，按文件名找会拿到别人的简历——
        所以这里收的是完整路径，用 resolve 之后的前缀判断挡住 ../ 穿越。
        """
        if not raw:
            self._json({"ok": False, "error": "缺少 path 参数"}, 400)
            return
        target = Path(raw).resolve()
        try:
            target.relative_to(OUTPUT_DIR.resolve())
        except ValueError:
            self._json({"ok": False, "error": "只能预览 data/output 下的文件"}, 403)
            return
        if not target.exists() or not target.is_file():
            self._json({"ok": False, "error": "文件不存在"}, 404)
            return
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        self._send(200, target.read_bytes(), ctype)


class _Request:
    __slots__ = ("body", "query")

    def __init__(self, body: dict[str, Any], query: dict[str, list[str]]) -> None:
        self.body = body
        self.query = query


# ------------------------------------------------------------------ 启动


def make_server(port: int = 8765, host: str = "127.0.0.1") -> ThreadingHTTPServer:
    srv = ThreadingHTTPServer((host, port), Handler)
    srv.daemon_threads = True
    return srv


def serve(port: int = 8765, host: str = "127.0.0.1", open_browser: bool = True) -> None:
    srv = make_server(port, host)
    url = f"http://{host}:{port}/"
    print(f"简历助手 · 界面已启动 {url}")
    print("  按 Ctrl+C 停止")
    if open_browser:
        import webbrowser

        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        srv.server_close()
