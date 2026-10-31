"""首屏数据组装：界面壳子要什么，这里一次给全。

为什么单独一层：现在有三个客户端要同一份数据 —— 本地网页（web/server.py）、
VSCode 插件（走 MCP 的 ui_overview）、以及任意 harness。如果各自拼一遍，
字段名迟早会分叉，然后出现「网页上有、插件里 undefined」这种最难查的 bug。

所以：组装逻辑只写一份，三个客户端都调它。
"""

from __future__ import annotations

from typing import Any

from . import pipeline


def overview() -> dict[str, Any]:
    """首屏要的全部数据，一次拿完。

    刻意保持同步：这些查询都是本地 sqlite + 文件读取，毫秒级，
    为它引入 async 只会让调用方多一层 await，没有收益。
    """
    from . import doctor as doctor_mod
    from .match import embed

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
            # 示例档案要显眼提醒，否则用户会拿「张三」的简历去投
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
