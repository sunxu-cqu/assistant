"""环境自检：在真正开始用之前，把「能不能跑」这件事一次性确认清楚。

这个工具的价值不在功能，而在**把隐性失败变成显性提示**：

- 少装一个 python-pptx，表现是「PPT 里的资料没进知识库」，而不是报错
- profile.yaml 还是示例数据，表现是「简历上写着张三」
- Typst 缺中文字体，表现是 PDF 里全是方框
- .env 没填 key，要等到第一次抽取才报 401

这些都不是崩溃，而是「悄悄做错」。`doctor` 把它们逐条摆出来。
"""

from __future__ import annotations

import os
import platform
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .config import DATA, EVIDENCE_DIR, PROFILE_PATH, PROJECTS_DIR, ROOT, settings

REQUIRED = [
    ("mcp", "mcp"),
    ("pydantic", "pydantic"),
    ("yaml", "PyYAML"),
    ("httpx", "httpx"),
    ("pdfplumber", "pdfplumber"),
    ("docx", "python-docx"),
    ("pptx", "python-pptx"),
    ("openpyxl", "openpyxl"),
    ("jieba", "jieba"),
    ("rank_bm25", "rank-bm25"),
    ("typer", "typer"),
    ("bs4", "beautifulsoup4"),
    ("typst", "typst"),
]

# 示例数据里的值，用来判断用户是不是还没改
PLACEHOLDERS = {
    "张三",
    "13800000000",
    "zhangsan@example.com",
    "某某大学",
    "某某公司",
}


@dataclass
class Check:
    level: str  # ok | warn | fail
    name: str
    detail: str = ""
    fix: str = ""

    def line(self) -> str:
        icon = {"ok": "✓", "warn": "!", "fail": "✗"}[self.level]
        out = f"  {icon} {self.name}"
        if self.detail:
            out += f" — {self.detail}"
        return out


def _check_python() -> Check:
    v = sys.version_info
    ok = (v.major, v.minor) >= (3, 10)
    return Check("ok" if ok else "fail", f"Python {platform.python_version()}", "" if ok else "需要 3.10+")


def _check_deps() -> list[Check]:
    import importlib

    missing: list[str] = []
    for mod, pkg in REQUIRED:
        try:
            importlib.import_module(mod)
        except Exception:  # noqa: BLE001
            missing.append(pkg)
    if not missing:
        return [Check("ok", f"依赖 {len(REQUIRED)}/{len(REQUIRED)}")]
    return [
        Check(
            "fail",
            f"缺少依赖 {len(missing)}/{len(REQUIRED)}",
            "、".join(missing),
            "pip install -r requirements.txt",
        )
    ]


def _check_api_key() -> Check:
    key = settings.api_key or ""
    if settings.mock:
        return Check("warn", "DEEPSEEK_API_KEY", "当前是 mock 模式，不会真的调用模型")
    if not key:
        return Check(
            "fail",
            "DEEPSEEK_API_KEY 未配置",
            "第一次抽取就会 401",
            f"在 {ROOT / '.env'} 里写 DEEPSEEK_API_KEY=sk-...",
        )
    if not key.startswith("sk-"):
        return Check("warn", "DEEPSEEK_API_KEY 格式可疑", f"开头是 {key[:4]}…，DeepSeek 的 key 一般以 sk- 开头")
    return Check("ok", "DEEPSEEK_API_KEY", f"已配置（{key[:6]}…，共 {len(key)} 位）")


def _check_profile() -> list[Check]:
    from .models import Profile

    if not PROFILE_PATH.exists():
        return [
            Check(
                "fail",
                "data/profile.yaml 不存在",
                "没有它无法生成简历",
                f"copy {ROOT / 'data' / 'profile.example.yaml'} {PROFILE_PATH}",
            )
        ]

    try:
        import yaml

        profile = Profile(**(yaml.safe_load(PROFILE_PATH.read_text(encoding="utf-8")) or {}))
    except Exception as exc:  # noqa: BLE001
        return [Check("fail", "profile.yaml 解析失败", str(exc)[:120], "检查 YAML 缩进")]

    out: list[Check] = []
    if not profile.name or profile.name in PLACEHOLDERS:
        out.append(
            Check("fail", "profile 姓名未填写", f"当前是「{profile.name or '(空)'}」", "改成你自己的名字")
        )
    else:
        out.append(Check("ok", "profile 姓名", profile.name))

    if not profile.education:
        out.append(Check("fail", "profile 缺少教育经历", "", "至少填一条 education"))
    else:
        edu = profile.education[0]
        if edu.school in PLACEHOLDERS:
            out.append(Check("warn", "教育经历仍是示例数据", edu.school, "改成你的学校/专业"))
        else:
            out.append(Check("ok", "教育经历", f"{edu.school} {edu.major} {edu.degree}"))
        if not edu.gpa and not edu.gpa_rank:
            out.append(Check("warn", "绩点/排名未填", "简历上会缺这一行", "有就填，没有可忽略"))

    if not (profile.phone or profile.email):
        out.append(Check("warn", "联系方式未填", "简历上不会出现电话/邮箱"))
    return out


def _check_projects() -> Check:
    if not PROJECTS_DIR.exists():
        return Check("fail", "workspace/projects 不存在", "", "建这个目录，一个项目一个文件夹")
    folders = [d for d in PROJECTS_DIR.iterdir() if d.is_dir() and not d.name.startswith(".")]
    if not folders:
        return Check(
            "fail",
            "workspace/projects 是空的",
            "",
            "把项目资料文件夹放进去（一个项目一个文件夹）；想先试跑，把 workspace/samples/ 里的示例复制过来",
        )
    empty = [d.name for d in folders if not any(p.is_file() for p in d.rglob("*"))]
    if empty:
        return Check("warn", f"项目文件夹 {len(folders)} 个", f"其中 {len(empty)} 个是空的：{', '.join(empty[:3])}")
    return Check("ok", f"项目文件夹 {len(folders)} 个")


def _check_typst() -> Check:
    """真的编一个含中文的文档——缺字体时 typst 不报错，但 PDF 里是方框。"""
    try:
        import typst
    except ImportError:
        return Check("fail", "Typst 未安装", "", "pip install typst")

    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "t.typ"
        pdf = Path(tmp) / "t.pdf"
        src.write_text(
            '#set page(paper: "a4")\n'
            '#set text(font: ("Microsoft YaHei", "SimHei", "Source Han Sans SC", "Noto Sans CJK SC"), size: 12pt)\n'
            "中文渲染测试：简历助手\n",
            encoding="utf-8",
        )
        try:
            typst.compile(str(src), output=str(pdf), root=tmp)
        except Exception as exc:  # noqa: BLE001
            return Check("fail", "Typst 编译失败", str(exc)[:120])
        if not pdf.exists() or pdf.stat().st_size == 0:
            return Check("fail", "Typst 没有产出 PDF")

        try:
            import pypdfium2 as pdfium

            text = "".join(p.get_textpage().get_text_range() for p in pdfium.PdfDocument(str(pdf)))
        except Exception:  # noqa: BLE001
            return Check("warn", "Typst 可用", "无法校验中文渲染（缺 pypdfium2）")
        if "中文" not in text:
            return Check(
                "fail",
                "PDF 里没有中文",
                "多半是缺中文字体，导出会是一堆方框",
                "装一个中文字体（微软雅黑/思源黑体），或改 render/typst.py 的 FONT_STACK",
            )
    return Check("ok", "Typst 中文渲染")


def _check_writable() -> Check:
    try:
        DATA.mkdir(parents=True, exist_ok=True)
        probe = DATA / ".write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except Exception as exc:  # noqa: BLE001
        return Check("fail", "data 目录不可写", str(exc)[:120])
    return Check("ok", "data 目录可写")


def _check_evidence() -> Check:
    if not EVIDENCE_DIR.exists():
        return Check("warn", "知识库为空", "", "跑 kb build 抽取")
    n = len(list(EVIDENCE_DIR.glob("*.yaml")))
    if n == 0:
        return Check("warn", "知识库为空", "", "跑 kb build 抽取")
    return Check("ok", f"已有 {n} 个项目的证据")


def _check_templates() -> Check:
    try:
        from .compose.templates import list_templates

        items = list_templates()
    except Exception as exc:  # noqa: BLE001
        return Check("fail", "模板加载失败", str(exc)[:120])
    return Check("ok", f"模板 {len(items)} 套", "、".join(t["key"] for t in items))


def run() -> dict:
    """跑完所有检查，返回结构化结果。"""
    checks: list[Check] = [_check_python()]
    checks += _check_deps()
    checks.append(_check_api_key())
    checks += _check_profile()
    checks.append(_check_projects())
    checks.append(_check_typst())
    checks.append(_check_writable())
    checks.append(_check_evidence())
    checks.append(_check_templates())

    fails = [c for c in checks if c.level == "fail"]
    warns = [c for c in checks if c.level == "warn"]
    return {
        "ok": not fails,
        "checks": [{"level": c.level, "name": c.name, "detail": c.detail, "fix": c.fix} for c in checks],
        "failed": len(fails),
        "warned": len(warns),
        "root": str(ROOT),
    }


def to_text(res: dict) -> str:
    lines = [f"环境自检 · {res['root']}", "─" * 56]
    checks = res["checks"]
    for c in checks:
        icon = {"ok": "✓", "warn": "!", "fail": "✗"}[c["level"]]
        line = f"  {icon} {c['name']}"
        if c["detail"]:
            line += f" — {c['detail']}"
        lines.append(line)
        if c["fix"] and c["level"] != "ok":
            lines.append(f"      → {c['fix']}")

    lines.append("─" * 56)
    if res["failed"]:
        lines.append(f"{res['failed']} 项必须处理，{res['warned']} 项建议处理。")
    elif res["warned"]:
        lines.append(f"可以跑。{res['warned']} 项建议处理。")
    else:
        lines.append("全部通过。")
    return "\n".join(lines)
