"""CLI 全命令冒烟：每个入口都要「说人话」，不许糊堆栈。

为什么单独一个文件：smoke.py 测的是 pipeline 的函数级行为，edge_cases.py
测的是数据边界。而用户真正敲的是命令——typer 的参数声明、默认值、异常
透传任何一处出错，用户看到的就是一屏 Python 堆栈，而不是「请用 internship」。

这一层只断言两件事，但对每个命令都断言：
1. 不出现 Python 堆栈
2. 失败时退出码非 0，且输出里有中文提示（而不是裸异常）

跑法：
    ./.venv/Scripts/python.exe tests/cli_smoke.py
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
SANDBOX = ROOT / "tests" / "sandbox" / "cli"

PASS: list[str] = []
FAIL: list[str] = []

# 堆栈特征。只认「真堆栈」的形态，避免把正常输出里的英文误判
_STACK = re.compile(r'Traceback \(most recent call last\)|\n\s+File ".*?", line \d+', re.S)
_CJK = re.compile(r"[\u4e00-\u9fff]")


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(("  ok   " if cond else "  FAIL ") + name + (f"  -> {detail}" if detail and not cond else ""))


def make_env() -> dict[str, str]:
    return {
        **os.environ,
        "RESUME_ASSISTANT_ROOT": str(ROOT),
        "RESUME_ASSISTANT_DATA": str(SANDBOX / "data"),
        "RESUME_ASSISTANT_WORKSPACE": str(SANDBOX / "workspace"),
        "RESUME_ASSISTANT_MOCK": "1",
        "PYTHONIOENCODING": "utf-8",
        "COLUMNS": "100",
    }


def run(args: list[str], env: dict[str, str]) -> tuple[int, str]:
    p = subprocess.run(
        [sys.executable, "-m", "resume_assistant.cli", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        cwd=str(ROOT),
        timeout=180,
    )
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def seed() -> None:
    """一份最小可用数据：1 条证据 + 1 个实习岗 + 1 个保研岗。

    没有数据的话，一半命令会在「知识库为空」处提前 return，
    测不到后面真正会出错的路径。
    """
    data = SANDBOX / "data"
    (data / "evidence").mkdir(parents=True, exist_ok=True)
    shutil.copy(ROOT / "data" / "profile.example.yaml", data / "profile.yaml")
    (data / "evidence" / "2025-推荐系统项目.yaml").write_text(
        yaml.safe_dump(
            {
                "_source": {"folder": "2025-推荐系统项目"},
                "items": [
                    {
                        "id": "ev_cli_1",
                        "kind": "project",
                        "title": "双通道召回推荐系统",
                        "role": "负责人",
                        "claim": "用双通道聚合缓解冷启动，点击率从 2.1% 提升到 3.4%",
                        "period": "2025.03 ~ 2025.06",
                        "raw_excerpt": "双通道聚合，冷启动点击率 2.1% → 3.4%",
                        "raw_numbers": ["2.1", "3.4"],
                        "metrics": [{"name": "点击率", "value": "3.4", "unit": "%", "source": "实验记录"}],
                        "domain_tags": ["推荐系统"],
                        "skill_tags": ["PyTorch", "Python"],
                    }
                ],
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )


# (命令, 期望退出码集合, 说明)
READONLY = [
    (["doctor", "--json"], {0, 1}, "doctor 示例 profile 下 exit=1 是对的"),
    (["kb", "scan"], {0}, "无 projects 文件夹时给提示"),
    (["kb", "stats"], {0}, ""),
    (["kb", "list"], {0}, ""),
    (["kb", "review"], {0, 1}, "全绿 exit=0 / 有问题 exit=1"),
    (["kb", "prune"], {0}, ""),
    (["job", "list"], {0}, ""),
    (["app", "list"], {0}, ""),
    (["templates"], {0}, ""),
    (["board", "--json", "--no-html"], {0}, ""),
    (["board", "--no-html"], {0}, "纯文本看板"),
]

BAD_INPUT = [
    (["job", "add", "-k", "实习", "--text", "x"], "中文 kind"),
    (["job", "add", "-k", "internship", "--file", "不存在.txt"], "JD 文件不存在"),
    (["match", "job_不存在"], "岗位不存在"),
    (["gen", "job_不存在"], "岗位不存在"),
    (["gen", "job_不存在", "-T", "没有这个模板"], "模板不存在"),
    (["rerender", "app_不存在"], "投递不存在"),
    (["letter", "job_不存在"], "岗位不存在"),
    (["pitch", "job_不存在"], "岗位不存在"),
    (["app", "show", "app_不存在"], "投递不存在"),
    (["app", "advance", "app_不存在", "sent"], "投递不存在"),
    (["app", "advance", "app_不存在", "已投递"], "中文状态名"),
    (["app", "note", "app_不存在", "hello"], "投递不存在"),
    (["kb", "fix", "ev_不存在", "-k", "course"], "证据不存在"),
    (["kb", "fix", "ev_cli_1", "-w", "99"], "weight 越界"),
    (["kb", "fix", "ev_cli_1", "-k", "根本不存在的类型"], "非法 kind"),
]

HELP = [
    [], ["doctor"], ["kb"], ["kb", "build"], ["kb", "scan"], ["kb", "prune"],
    ["kb", "stats"], ["kb", "list"], ["kb", "review"], ["kb", "fix"],
    ["job"], ["job", "add"], ["job", "list"],
    ["app"], ["app", "list"], ["app", "advance"], ["app", "note"], ["app", "show"],
    ["match"], ["gen"], ["rerender"], ["templates"], ["letter"], ["pitch"], ["board"],
]


def main() -> int:
    shutil.rmtree(SANDBOX, ignore_errors=True)
    (SANDBOX / "data").mkdir(parents=True, exist_ok=True)
    env = make_env()

    print("\n[1] 每个子命令的 --help 都能跑")
    for parts in HELP:
        code, out = run([*parts, "--help"], env)
        label = " ".join(parts) or "(root)"
        ok = code == 0 and not _STACK.search(out) and _CJK.search(out)
        check(f"help · {label}", ok, f"exit={code} {out[-160:]}")

    print("\n[2] 空库：所有只读命令不崩")
    for args, allowed, why in READONLY:
        code, out = run(args, env)
        ok = code in allowed and not _STACK.search(out)
        check(f"{' '.join(args)}", ok, f"exit={code} {out[-200:]}")
        if why and code not in allowed:
            print(f"        （{why}）")

    print("\n[3] 有数据：只读命令给出内容")
    seed()
    for args, allowed, _ in READONLY:
        code, out = run(args, env)
        ok = code in allowed and not _STACK.search(out) and bool(out.strip())
        check(f"{' '.join(args)} 有输出", ok, f"exit={code} 空输出" if not out.strip() else f"exit={code}")

    code, out = run(["kb", "list"], env)
    check("kb list 能看到证据", "双通道召回推荐系统" in out, out[-200:])

    code, out = run(["doctor", "--json"], env)
    try:
        doc = json.loads(out)
        ok_json = isinstance(doc.get("checks"), list) and "ok" in doc
    except json.JSONDecodeError:
        ok_json = False
    check("doctor --json 可解析", ok_json, out[-200:])

    code, out = run(["kb", "stats"], env)
    check("kb stats 报证据条数", "1" in out, out[-200:])

    print("\n[4] 错误输入：退出码非 0 + 中文提示 + 无堆栈")
    for args, why in BAD_INPUT:
        code, out = run(args, env)
        no_stack = not _STACK.search(out)
        cjk = bool(_CJK.search(out))
        check(f"{' '.join(args)} · {why}", code != 0 and no_stack and cjk, f"exit={code} cjk={cjk} {out[-200:]}")

    print("\n[5] 写命令的正路：录岗 → 匹配 → 看板 → 状态机")
    code, out = run(
        ["job", "add", "-k", "internship", "--org", "某科技公司", "--title", "推荐算法实习生",
         "--text", "岗位职责：负责召回与排序优化。任职要求：熟悉 Python、PyTorch，有推荐系统项目经验。"],
        env,
    )
    check("job add 成功", code == 0 and "推荐算法实习生" in out, f"exit={code} {out[-300:]}")

    # 岗位编号里带中文（job_某科技公司_cf493）：可读性换来的代价是正则不能只认 ASCII
    m = re.search(r'"(job_[^"]+)"', out)
    job_id = m.group(1) if m else ""
    check("job add 打印出 job_id", bool(job_id), out[:300])

    code, out = run(["match", job_id], env)
    check("match 跑通", code == 0 and "双通道" in out, f"exit={code} {out[-300:]}")

    code, out = run(["gen", job_id], env)
    check("gen 产出 PDF", code == 0 and ".pdf" in out, f"exit={code} {out[-300:]}")

    code, out = run(["app", "list"], env)
    check("gen 之后有投递记录", code == 0 and "app_" in out, out[-300:])

    # app list 是「[app_xxx] status ...」的表格形态，不是 JSON
    m = re.search(r"\[(app_[^\]\s]+)\]", out)
    app_id = m.group(1) if m else ""
    if app_id:
        code, out = run(["app", "advance", app_id, "sent", "-n", "邮件已发"], env)
        check("advance 到 sent", code == 0, f"exit={code} {out[-200:]}")

        code, out = run(["app", "advance", app_id, "draft"], env)
        check("非法回退被拒", code != 0 and ("不能" in out or "非法" in out), f"exit={code} {out[-200:]}")

        # 断言「已记录 + 条数」而不是「输出里有 1」：一条投递走到这里已经有
        # draft→matched→composed→sent 四条事件，第 5 条备注里根本没有 "1"。
        code, out = run(["app", "note", app_id, "HR 说下周约面"], env)
        m = re.search(r"当前共 (\d+) 条", out)
        n_note = int(m.group(1)) if m else 0
        check("note 记一笔", code == 0 and "已记录" in out and n_note >= 5, f"exit={code} n={n_note} {out[-200:]}")

        code, out = run(["app", "show", app_id], env)
        check("show 带时间线", code == 0 and "sent" in out, f"exit={code} {out[-300:]}")
    else:
        check("（跳过状态机：没拿到 app_id）", False, "app list 没输出 app_id")

    code, out = run(["board", "--json", "--no-html"], env)
    try:
        b = json.loads(out)
        ok_board = b.get("total", 0) >= 1 and "funnel" in b
    except json.JSONDecodeError:
        ok_board = False
    check("看板统计到这条投递", ok_board, out[-300:])

    print("\n[6] 环境坏了也只说人话")
    pf = SANDBOX / "data" / "profile.yaml"
    backup = pf.read_text(encoding="utf-8")
    pf.write_text("name: 张三\neducation: [\n  - school: 某大学\n", encoding="utf-8")
    code, out = run(["doctor"], env)
    check("profile 写坏时无堆栈", not _STACK.search(out), out[-300:])
    check("profile 写坏时指出行号", "行" in out or "profile.yaml" in out, out[-300:])
    pf.write_text(backup, encoding="utf-8")

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
