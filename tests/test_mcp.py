"""MCP 层测试：握手 + 工具清单 + 错误路径。

走的是最底层的 JSON-RPC over stdio，不依赖任何客户端库——VSCode 插件、
agent harness、脚本客户端看到的都是这些字节，所以这一层必须是稳的。

重点不在「正常调用能跑通」（smoke.py 已经覆盖），而在：
**传错了参数，server 是返回结构化错误，还是把堆栈糊到 stdout 上把会话搞死。**
前者客户端能读、能提示用户；后者整个 MCP 连接就断了，用户只看到「工具挂了」。

运行：
    ./.venv/Scripts/python.exe tests/test_mcp.py
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = ROOT / ".venv" / "Scripts" / "python.exe"

# 沙箱隔离：这批调用会真的写库（job_add 成功那条）。指向真实 data/ 就会
# 往用户的岗位池里塞一条「某科技公司 · 推荐算法实习生」。
SANDBOX = ROOT / "tests" / "sandbox" / "mcp"

EXPECTED_TOOLS = {
    "kb_build", "kb_prune", "kb_stats", "kb_list_evidence", "doctor",
    "kb_scan", "kb_review", "kb_fix",
    "job_add", "job_list", "job_match",
    "resume_generate", "resume_preview", "resume_rerender",
    "cover_letter_generate", "pitch_generate", "template_list",
    "app_list", "app_detail", "app_advance", "app_timeline", "app_note",
    "app_board",
    "ui_overview",
}

PASS: list[str] = []
FAIL: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(("  ok   " if cond else "  FAIL ") + name + (f"  -> {detail}" if detail and not cond else ""))


# ------------------------------------------------------------------ JSON-RPC

class Client:
    def __init__(self, proc: subprocess.Popen) -> None:
        self.proc = proc
        self._id = 0

    def _send(self, payload: dict) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()

    def _read_for(self, rid: int) -> dict:
        """读到 id 对得上的那条为止。

        server 会在响应之间插 notifications（日志、进度），一律跳过——
        按行硬读一次，插一条通知就全错位，测试会变成「随机失败」。
        """
        assert self.proc.stdout is not None
        for _ in range(50):
            line = self.proc.stdout.readline()
            if not line:
                return {"error": "eof"}
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if msg.get("id") == rid:
                return msg
        return {"error": "no response"}

    def request(self, method: str, params: dict) -> dict:
        self._id += 1
        self._send({"jsonrpc": "2.0", "id": self._id, "method": method, "params": params})
        return self._read_for(self._id)

    def notify(self, method: str, params: dict) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def call(self, name: str, **arguments) -> dict:
        return self.request("tools/call", {"name": name, "arguments": arguments})


def text_of(resp: dict) -> str:
    content = (resp.get("result") or {}).get("content") or []
    if not content:
        return ""
    return content[0].get("text", "") or ""


def payload_of(resp: dict) -> dict:
    raw = text_of(resp)
    try:
        got = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {"_raw": raw}
    return got if isinstance(got, dict) else {"_raw": got}


def is_structured_error(resp: dict) -> tuple[bool, str]:
    """工具调用「有结构地失败」：JSON-RPC 层没炸，content 里带 ok=false。

    两种情况都不合格：
    - 顶层出现 "error"：协议层失败，客户端拿到的是 -32603 之类的错误对象，
      看不到我们精心写的 hint
    - content 解析不出 JSON：说明返回的是裸字符串/堆栈
    """
    if "error" in resp:
        return False, f"协议层错误 {resp['error']}"
    if "result" not in resp:
        return False, f"没有 result: {resp}"
    body = payload_of(resp)
    if "_raw" in body:
        return False, f"content 不是 JSON: {text_of(resp)[:120]}"
    if body.get("ok") is not False:
        return False, f"期望 ok=false，实际 {body}"
    return True, ""


def main() -> int:
    shutil.rmtree(SANDBOX, ignore_errors=True)
    (SANDBOX / "data").mkdir(parents=True, exist_ok=True)
    shutil.copy(ROOT / "data" / "profile.example.yaml", SANDBOX / "data" / "profile.yaml")

    env = dict(os.environ)
    env["RESUME_ASSISTANT_ROOT"] = str(ROOT)
    env["RESUME_ASSISTANT_DATA"] = str(SANDBOX / "data")
    env["RESUME_ASSISTANT_WORKSPACE"] = str(SANDBOX / "workspace")
    env["RESUME_ASSISTANT_MOCK"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    # 万一有人把真实 key 留在环境里，也别让它真的发请求
    env.pop("DEEPSEEK_API_KEY", None)

    proc = subprocess.Popen(
        [str(PY), "-m", "resume_assistant.mcp_server"],
        cwd=str(ROOT),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        env=env,
    )
    cli = Client(proc)

    try:
        print("\n[1] 握手")
        init = cli.request(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "test_mcp", "version": "0.2"},
            },
        )
        check("initialize 有 result", "result" in init, str(init)[:200])
        info = (init.get("result") or {}).get("serverInfo") or {}
        check("serverInfo.name = 简历助手", info.get("name") == "简历助手", str(info))
        check("返回 protocolVersion", bool((init.get("result") or {}).get("protocolVersion")))

        cli.notify("notifications/initialized", {})

        print("\n[2] 工具清单")
        tl = cli.request("tools/list", {})
        tools = (tl.get("result") or {}).get("tools") or []
        names = {t.get("name") for t in tools}
        check(f"工具数量 = {len(EXPECTED_TOOLS)}", len(tools) == len(EXPECTED_TOOLS), f"实际 {len(tools)}: {sorted(names)}")
        check("工具名齐全", names == EXPECTED_TOOLS, f"缺 {EXPECTED_TOOLS - names} / 多 {names - EXPECTED_TOOLS}")

        no_desc = [t.get("name") for t in tools if not (t.get("description") or "").strip()]
        check("每个工具都有 description", not no_desc, str(no_desc))

        no_schema = [t.get("name") for t in tools if not isinstance(t.get("inputSchema"), dict)]
        check("每个工具都有 inputSchema", not no_schema, str(no_schema))

        # 模型靠 description 决定调哪个工具，太短等于没写
        short = [t["name"] for t in tools if len(t.get("description") or "") < 20]
        check("description 不是一句话占位", not short, str(short))

        print("\n[3] 正常调用")
        resp = cli.call("template_list")
        body = payload_of(resp)
        check("template_list ok", body.get("ok") is True, str(body)[:200])
        items = body.get("items") or []
        check("模板非空且带 key", bool(items) and all("key" in i for i in items), str(items)[:200])

        resp = cli.call("doctor")
        body = payload_of(resp)
        check("doctor 有结构（不是裸文本）", "ok" in body, str(body)[:200])

        resp = cli.call(
            "job_add",
            kind="internship",
            title="推荐算法实习生",
            org="某科技公司",
            raw_text="岗位职责：负责推荐系统召回与排序优化。任职要求：熟悉 Python、PyTorch。",
        )
        body = payload_of(resp)
        check("job_add 成功", body.get("ok") is True, str(body)[:300])
        job_id = ((body.get("job") or {}).get("id")) or ""
        check("job_add 返回 job.id", bool(job_id), str(body)[:200])

        resp = cli.call("job_list")
        body = payload_of(resp)
        ids = [j.get("id") for j in (body.get("items") or [])]
        check("job_list 能查到刚录的岗位", job_id in ids, str(ids))

        resp = cli.call("job_list", kind="实习")
        check("job_list 非法 kind 不崩", "result" in resp, str(resp)[:200])

        resp = cli.call("app_board", html=False)
        body = payload_of(resp)
        check("app_board 空库也能出看板", body.get("ok") is True and "text" in body, str(body)[:200])

        resp = cli.call("kb_list_evidence", limit=0)
        check("kb_list_evidence limit=0 不报错", payload_of(resp).get("ok") is True, text_of(resp)[:200])

        print("\n[4] 错误路径：非法参数必须结构化失败")
        cases: list[tuple[str, dict, str]] = [
            ("job_add", {"kind": "实习"}, "中文 kind 应提示用 internship"),
            ("job_add", {"kind": "intern", "raw_text": "x"}, "近义拼写应被拒"),
            ("app_advance", {"app_id": "app_不存在", "to_status": "sent"}, "不存在的投递"),
            ("app_detail", {"app_id": "app_不存在"}, "不存在的投递详情"),
            ("app_note", {"app_id": "app_不存在", "note": "x"}, "不存在的投递记事"),
            ("app_timeline", {"app_id": "app_不存在"}, "不存在的投递时间线"),
            ("kb_fix", {"evidence_id": "ev_不存在", "kind": "course"}, "不存在的证据"),
            ("job_match", {"job_id": "job_不存在"}, "不存在的岗位"),
            ("resume_generate", {"job_id": "job_不存在"}, "不存在的岗位生成简历"),
            ("resume_preview", {"job_id": "job_不存在"}, "不存在的岗位预览"),
            ("cover_letter_generate", {"job_id": "job_不存在"}, "不存在的岗位套磁"),
            ("pitch_generate", {"job_id": "job_不存在"}, "不存在的岗位开场白"),
            ("resume_rerender", {"app_id": "app_不存在"}, "不存在的产物重渲染"),
        ]
        for name, args, why in cases:
            resp = cli.call(name, **args)
            ok, detail = is_structured_error(resp)
            check(f"{name} · {why}", ok, detail)

        # 有 job_id 的情况下，状态机本身要把非法状态名挡住
        resp = cli.call("app_advance", app_id="app_不存在", to_status="已投递")
        body = payload_of(resp)
        joined = json.dumps(body, ensure_ascii=False)
        check(
            "app_advance 中文状态名给出可选值",
            body.get("ok") is False and ("sent" in joined or "合法" in joined or "可选" in joined),
            joined[:300],
        )

        print("\n[5] 协议层：客户端传错东西时不能把会话搞死")
        resp = cli.request("tools/call", {"name": "no_such_tool", "arguments": {}})
        check("未知工具名返回协议错误", "error" in resp or (resp.get("result") or {}).get("isError"), str(resp)[:200])

        resp = cli.request("tools/call", {"name": "job_add", "arguments": {}})
        check("缺必填参数返回错误", "error" in resp or (resp.get("result") or {}).get("isError"), str(resp)[:200])

        resp = cli.request("tools/call", {"name": "kb_list_evidence", "arguments": {"limit": "abc"}})
        check("参数类型不对返回错误", "error" in resp or (resp.get("result") or {}).get("isError"), str(resp)[:200])

        resp = cli.request("bogus/method", {})
        check("未知方法返回协议错误", "error" in resp, str(resp)[:200])

        print("\n[6] 出错之后 server 还活着")
        resp = cli.call("template_list")
        check("连续错误后仍能正常调用", payload_of(resp).get("ok") is True, text_of(resp)[:200])

        tl2 = cli.request("tools/list", {})
        check("仍能列工具", len((tl2.get("result") or {}).get("tools") or []) == len(EXPECTED_TOOLS))

    finally:
        proc.terminate()
        try:
            _, err = proc.communicate(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill()
            _, err = proc.communicate()

    print("\n[7] stderr 干净度")
    err = err or ""
    check("stderr 无 Traceback", "Traceback" not in err, err.strip().splitlines()[-1][:200] if err.strip() else "")
    # mcp 库自己会打启动横幅，只揪我们自己漏出去的日志
    noisy = [ln for ln in err.splitlines() if ln.startswith(("ERROR", "CRITICAL"))]
    check("stderr 无 ERROR 级日志", not noisy, str(noisy[:3]))

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
