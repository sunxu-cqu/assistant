"""召回层：BM25 + 标签重叠，纯本地、零 API 成本。

向量检索留了接口（encode_fn），P0 不引入 torch，避免几个 G 的依赖；
接 bge-m3 时只需传入 encoder，融合逻辑无需改动。
"""

from __future__ import annotations

import re
from typing import Callable

import jieba
from rank_bm25 import BM25Okapi

from ..models import Evidence, Job

STOP = set(
    "的 了 和 与 及 或 在 是 有 我 我们 你 他 她 它 这 那 一个 一种 进行 通过 基于 使用 利用 实现 相关 以及 等 中 上 下 对 为 以 之 其 并 而 也 都 就 还 会 能 可以 需要 要求 具备 熟悉 掌握 了解 优先 加分 良好 较强 一定 工作 能力".split()
)

_ASCII = re.compile(r"[a-zA-Z][a-zA-Z0-9+#.\-]*")


def tokenize(text: str) -> list[str]:
    text = text.lower()
    tokens: list[str] = []
    for w in jieba.lcut(text):
        w = w.strip()
        if not w or w in STOP:
            continue
        if len(w) > 1 or w.isdigit() or _ASCII.fullmatch(w):
            tokens.append(w)
    return tokens


def _job_query(job: Job) -> str:
    parts = [job.title, job.org, job.mentor, " ".join(job.mentor_topics), " ".join(job.keywords)]
    parts += [r.text for r in job.requirements]
    if not job.keywords and not job.requirements:
        parts.append(job.raw_text[:4000])
    return "\n".join(p for p in parts if p)


def _job_tag_set(job: Job) -> set[str]:
    tags: set[str] = set()
    for k in job.keywords:
        tags.add(k.lower())
    for t in job.mentor_topics:
        tags.add(t.lower())
    for r in job.requirements:
        for w in tokenize(r.text):
            tags.add(w)
    return tags


def recall(
    job: Job,
    evidences: list[Evidence],
    top_k: int = 30,
    encode_fn: Callable[[list[str]], list[list[float]]] | None = None,
) -> list[tuple[Evidence, float, list[str]]]:
    if not evidences:
        return []

    corpus_texts = [e.search_text() for e in evidences]
    corpus_tokens = [tokenize(t) for t in corpus_texts]
    query_tokens = tokenize(_job_query(job))

    bm25_scores = [0.0] * len(evidences)
    if query_tokens and any(corpus_tokens):
        bm25 = BM25Okapi(corpus_tokens)
        raw = bm25.get_scores(query_tokens)
        peak = max(raw) if max(raw) > 0 else 1.0
        bm25_scores = [float(s) / peak for s in raw]

    job_tags = _job_tag_set(job)
    results: list[tuple[Evidence, float, list[str]]] = []
    for ev, bm in zip(evidences, bm25_scores):
        ev_tags = {t.lower() for t in [*ev.domain_tags, *ev.skill_tags]}
        hit = sorted(job_tags & ev_tags)
        tag_score = min(1.0, len(hit) / 4.0)
        score = 0.62 * bm + 0.28 * tag_score + 0.10 * ev.weight
        results.append((ev, score, hit))

    if encode_fn is not None:
        try:
            import math

            qv = encode_fn([_job_query(job)])[0]
            dvs = encode_fn(corpus_texts)
            def cos(a, b):
                num = sum(x * y for x, y in zip(a, b))
                da = math.sqrt(sum(x * x for x in a)) or 1.0
                db = math.sqrt(sum(y * y for y in b)) or 1.0
                return num / (da * db)
            merged = []
            for (ev, score, hit), dv in zip(results, dvs):
                merged.append((ev, 0.5 * score + 0.5 * cos(qv, dv), hit))
            results = merged
        except Exception:  # noqa: BLE001
            pass

    results.sort(key=lambda x: x[1], reverse=True)
    return results[:top_k]
