"""向量召回：bge-m3 可选增强。

为什么是「可选」而不是默认：sentence-transformers + torch 装下来两个多 G，
而 BM25 对「推荐系统 / 冷启动 / 召回」这种词面高度重合的中文技术文本已经够用。
真正的短板是同义不同词——「个性化排序」和「推荐系统」、「多目标」和「CTR 预估」，
BM25 一个都匹配不上，这时候才需要向量。

所以这里的约定是：
- 没装 → `available()` 返回 (False, 原因)，召回自动只用 BM25，不报错、不降速
- 装了 → 首次使用时加载模型（几秒），向量按「文本 sha1」缓存在 data/.cache/，
  证据没改就不重算；模型换版本时缓存按模型名分开，不会串味
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Callable

from ..config import DATA

MODEL_NAME = os.environ.get("RESUME_ASSISTANT_EMBED_MODEL", "BAAI/bge-m3")
CACHE_DIR = DATA / ".cache"

_MODEL: Any = None
_ERR: str = ""
_TRIED = False


def _cache_path() -> Path:
    tag = hashlib.sha1(MODEL_NAME.encode("utf-8")).hexdigest()[:10]
    return CACHE_DIR / f"embed_{tag}.npz"


def _key(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", "ignore")).hexdigest()[:24]


def _load_model() -> Any:
    global _MODEL, _ERR, _TRIED
    if _MODEL is not None or _TRIED:
        return _MODEL
    _TRIED = True
    try:
        from sentence_transformers import SentenceTransformer  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        _ERR = f"未安装 sentence-transformers（{type(exc).__name__}）"
        return None
    try:
        from sentence_transformers import SentenceTransformer

        device = os.environ.get("RESUME_ASSISTANT_EMBED_DEVICE", "cpu")
        _MODEL = SentenceTransformer(MODEL_NAME, device=device)
    except Exception as exc:  # noqa: BLE001
        # 模型没下载、显存不够、路径写错都会走到这里。不要让它把匹配整个搞挂
        _ERR = f"模型加载失败：{type(exc).__name__}: {exc}"
        _MODEL = None
    return _MODEL


def available() -> tuple[bool, str]:
    """(是否可用, 说明)。说明在不可用时是原因，可用时是模型名。"""
    model = _load_model()
    if model is None:
        return False, _ERR or "未启用"
    return True, MODEL_NAME


# ---------------------------------------------------------------- 向量缓存


def _load_cache() -> dict[str, Any]:
    path = _cache_path()
    if not path.exists():
        return {}
    try:
        import numpy as np

        with np.load(path, allow_pickle=False) as z:
            return {k: z[k] for k in z.files}
    except Exception:  # noqa: BLE001
        # 缓存坏了就当没有——重算一次比报错强
        return {}


def _save_cache(cache: dict[str, Any]) -> None:
    try:
        import numpy as np

        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path = _cache_path()
        tmp = path.with_suffix(".tmp")
        np.savez_compressed(tmp, **cache)
        tmp.replace(path)
    except Exception:  # noqa: BLE001
        pass  # 缓存写不进去不影响本次结果


def get_encoder() -> Callable[[list[str]], list[list[float]]] | None:
    """返回 encode_fn，不可用时返回 None。

    签名与 recall() 的 encode_fn 一致：一批文本 → 一批向量（已归一化，
    余弦相似度就是点积，recall 里的 cos() 不用改）。
    """
    if _load_model() is None:
        return None

    cache = _load_cache()

    def encode(texts: list[str]) -> list[list[float]]:
        model = _MODEL
        if model is None:
            return [[0.0] for _ in texts]
        keys = [_key(t) for t in texts]
        todo = [(k, t) for k, t in zip(keys, texts) if k not in cache]
        if todo:
            vecs = model.encode(
                [t for _, t in todo],
                batch_size=int(os.environ.get("RESUME_ASSISTANT_EMBED_BATCH", "16")),
                normalize_embeddings=True,
                show_progress_bar=False,
            )
            for (k, _), v in zip(todo, vecs):
                cache[k] = v
            _save_cache(cache)
        return [cache[k].tolist() for k in keys]

    return encode


def reset() -> None:
    """清掉内存里的模型与探测结果（测试用）。"""
    global _MODEL, _ERR, _TRIED
    _MODEL, _ERR, _TRIED = None, "", False


def info() -> dict[str, Any]:
    ok, why = available()
    cache = _cache_path()
    n = 0
    if cache.exists():
        try:
            import numpy as np

            with np.load(cache, allow_pickle=False) as z:
                n = len(z.files)
        except Exception:  # noqa: BLE001
            n = 0
    return {
        "enabled": ok,
        "model": MODEL_NAME,
        "reason": why,
        "cache_file": str(cache) if cache.exists() else "",
        "cache_vectors": n,
        "hint": (
            "已启用语义召回，按 BM25 0.5 + 向量 0.5 融合"
            if ok
            else "只用 BM25。装 sentence-transformers 后可启用 bge-m3 语义召回："
            "pip install sentence-transformers"
        ),
    }


def to_json() -> str:
    return json.dumps(info(), ensure_ascii=False)
