"""模板加载。模板只描述版式与槽位，不含任何内容。"""

from __future__ import annotations

from pathlib import Path

import yaml

from ..config import TEMPLATE_DIR


class SectionSpec:
    def __init__(self, raw: dict) -> None:
        self.type: str = raw.get("type", "")
        self.title: str = raw.get("title", "")
        self.max_entries: int = int(raw.get("max_entries", 4))
        self.bullets_per_entry: int = int(raw.get("bullets_per_entry", 3))
        self.variant: str = raw.get("variant", "short")
        self.optional: bool = bool(raw.get("optional", False))


class Template:
    def __init__(self, key: str, raw: dict) -> None:
        self.key = key
        self.name: str = raw.get("name", key)
        self.description: str = raw.get("description", "")
        self.page: dict = raw.get("page", {})
        self.sections: list[SectionSpec] = [SectionSpec(s) for s in raw.get("sections", [])]

    def section(self, type_: str) -> SectionSpec | None:
        return next((s for s in self.sections if s.type == type_), None)


def load_template(key: str = "single_column") -> Template:
    path: Path = TEMPLATE_DIR / f"{key}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"模板不存在：{path}")
    return Template(key, yaml.safe_load(path.read_text(encoding="utf-8")) or {})


def list_templates() -> list[dict[str, str]]:
    out = []
    for p in sorted(TEMPLATE_DIR.glob("*.yaml")):
        try:
            raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        except Exception:  # noqa: BLE001
            continue
        out.append({"key": p.stem, "name": raw.get("name", p.stem), "description": raw.get("description", "")})
    return out
