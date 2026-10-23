"""MCP 握手测试：确认 server 能被任何 MCP 客户端正常拉起并响应。

直接走 JSON-RPC over stdio，不依赖客户端库，验证的是最底层的可用性。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = ROOT / ".venv" / "Scripts" / "python.exe"


def send(proc: subprocess.Popen, payload: dict) -> None:
    assert proc.stdin is not None
    proc.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
    proc.stdin.flush()


def read_one(proc: subprocess.Popen) -> dict:
    assert proc.stdout is not None
    line = proc.stdout.readline()
    if not line:
        return {"error": "no response"}
    return json.loads(line)


def main() -> int:
    env = dict(os.environ)
    env["RESUME_ASSISTANT_ROOT"] = str(ROOT)
    env["RESUME_ASSISTANT_MOCK"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"

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

    try:
        send(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "smoke", "version": "0.1"},
                },
            },
        )
        init = read_one(proc)
        ok_init = "result" in init
        print(("  ok   " if ok_init else "  FAIL ") + "initialize")
        if ok_init:
            print("        server:", init["result"]["serverInfo"]["name"])

        send(proc, {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})

        send(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        tl = read_one(proc)
        tools = tl.get("result", {}).get("tools", [])
        print(("  ok   " if len(tools) >= 13 else "  FAIL ") + f"tools/list -> {len(tools)} 个工具")

        send(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "template_list", "arguments": {}},
            },
        )
        call = read_one(proc)
        content = call.get("result", {}).get("content", [])
        ok_call = bool(content)
        print(("  ok   " if ok_call else "  FAIL ") + "tools/call template_list")
        if ok_call:
            print("        ", content[0].get("text", "")[:120].replace("\n", " "))

        return 0 if (ok_init and len(tools) >= 13 and ok_call) else 1
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass
    raise SystemExit(main())
