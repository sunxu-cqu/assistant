"""路径与运行配置。所有路径以项目根为基准，支持环境变量覆盖。"""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(os.environ.get("RESUME_ASSISTANT_ROOT", Path(__file__).resolve().parent.parent))

# 数据目录可单独覆盖，便于用沙箱数据做端到端测试而不污染真实资料
DATA = Path(os.environ.get("RESUME_ASSISTANT_DATA", ROOT / "data"))
EVIDENCE_DIR = DATA / "evidence"
JOBS_DIR = DATA / "jobs"
APPS_DIR = DATA / "applications"
TEMPLATES_DIR = DATA / "templates"
OUTPUT_DIR = DATA / "output"
DB_PATH = DATA / "resume.db"
PROFILE_PATH = DATA / "profile.yaml"

WORKSPACE = Path(os.environ.get("RESUME_ASSISTANT_WORKSPACE", ROOT / "workspace"))
PROJECTS_DIR = WORKSPACE / "projects"

TEMPLATE_DIR = Path(__file__).resolve().parent / "render" / "templates"


class ConfigError(RuntimeError):
    """用户可修复的配置问题（YAML 写坏了、字段类型不对）。

    与普通异常分开：CLI 顶层捕获它只打印一行人话，不打堆栈——
    堆栈对「profile.yaml 第 3 行少了缩进」没有任何帮助。
    """


def ensure_dirs() -> None:
    for d in (DATA, EVIDENCE_DIR, JOBS_DIR, APPS_DIR, TEMPLATES_DIR, OUTPUT_DIR, PROJECTS_DIR):
        d.mkdir(parents=True, exist_ok=True)


def _load_dotenv() -> None:
    for candidate in (ROOT / ".env", Path.cwd() / ".env"):
        if not candidate.exists():
            continue
        for line in candidate.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip().strip('"').strip("'")
            os.environ.setdefault(k, v)


_load_dotenv()


class Settings:
    api_key: str = os.environ.get("DEEPSEEK_API_KEY", "")
    base_url: str = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    model: str = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")
    temperature: float = float(os.environ.get("RESUME_ASSISTANT_TEMPERATURE", "0.2"))
    mock: bool = os.environ.get("RESUME_ASSISTANT_MOCK", "0") == "1"
    timeout: float = float(os.environ.get("RESUME_ASSISTANT_TIMEOUT", "120"))

    # 知识库抽取时单个项目送入模型的字符上限
    max_project_chars: int = int(os.environ.get("RESUME_ASSISTANT_MAX_CHARS", "24000"))

    @classmethod
    def validate(cls) -> None:
        if not cls.mock and not cls.api_key:
            raise RuntimeError(
                "未配置 DEEPSEEK_API_KEY。请在 简历助手/.env 中填写，"
                "或设置 RESUME_ASSISTANT_MOCK=1 使用离线假数据跑通链路。"
            )


settings = Settings()
