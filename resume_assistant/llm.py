"""DeepSeek API 客户端。

约束：
- 只做「结构化抽取」，一律要求 JSON 输出，拒绝自由文本
- mock 模式返回固定结构，用于无 key 环境下跑通链路与 CI
"""

from __future__ import annotations

import json
import re
import time
from typing import Any

import httpx

from .config import settings


class LLMError(RuntimeError):
    pass


def _extract_json(text: str) -> Any:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start = min([i for i in (text.find("{"), text.find("[")) if i >= 0], default=-1)
    end = max(text.rfind("}"), text.rfind("]"))
    if start >= 0 and end > start:
        return json.loads(text[start : end + 1])
    raise LLMError(f"模型返回无法解析为 JSON：{text[:200]}")


class DeepSeek:
    def __init__(self, mock: bool | None = None) -> None:
        self.mock = settings.mock if mock is None else mock
        if not self.mock:
            settings.validate()

    # ------------------------------------------------------------ 底层调用
    def chat_json(
        self,
        system: str,
        user: str,
        *,
        mock_payload: Any | None = None,
        retries: int = 2,
        max_tokens: int = 4096,
    ) -> Any:
        if self.mock:
            if mock_payload is None:
                raise LLMError("mock 模式缺少 mock_payload")
            return mock_payload

        payload = {
            "model": settings.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": settings.temperature,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
        }
        headers = {
            "Authorization": f"Bearer {settings.api_key}",
            "Content-Type": "application/json",
        }
        last: Exception | None = None
        for attempt in range(retries + 1):
            try:
                with httpx.Client(timeout=settings.timeout) as client:
                    resp = client.post(
                        f"{settings.base_url.rstrip('/')}/chat/completions",
                        headers=headers,
                        json=payload,
                    )
                if resp.status_code != 200:
                    raise LLMError(f"HTTP {resp.status_code}: {resp.text[:300]}")
                content = resp.json()["choices"][0]["message"]["content"]
                return _extract_json(content)
            except Exception as exc:  # noqa: BLE001
                last = exc
                if attempt < retries:
                    time.sleep(1.5 * (attempt + 1))
        raise LLMError(f"调用 DeepSeek 失败：{last}")


_shared: DeepSeek | None = None


def get_llm() -> DeepSeek:
    global _shared
    if _shared is None:
        _shared = DeepSeek()
    return _shared
