"""Hybrid search with jurisdiction-isolated filtering.

Dense cosine over stored embeddings + lightweight BM25-ish keyword score.
Dual mode runs two isolated searches (never merges corpora pre-scoring).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from config.settings import get_settings
from src.core.vector_store import ScoredChunk, VectorStore
from src.ingestion.embedder import Embedder

_TOKEN = re.compile(r"[a-z0-9]{3,}")


def _keywords(text: str) -> set[str]:
    return set(_TOKEN.findall(text.lower()))


def keyword_score(query: str, doc: str) -> float:
    q = _keywords(query)
    if not q:
        return 0.0
    d = _keywords(doc[:4000])
    hit = len(q & d)
    return hit / max(1, len(q))


@dataclass
class RetrievalResult:
    chunks: list[ScoredChunk]
    top_score: float
    jurisdiction: str


class Retriever:
    def __init__(self, store: VectorStore | None = None, embedder: Embedder | None = None):
        s = get_settings()
        from pathlib import Path  # local to avoid circulars
        self.store = store or VectorStore(s.db_path_abs)
        self.embedder = embedder or Embedder()
        self.top_k = s.top_k
        self.dual_top_k = s.dual_top_k

    async def retrieve(self, query: str, jurisdiction: str = "dual",
                       top_k: int | None = None) -> dict[str, RetrievalResult]:
        """Returns per-jurisdiction results. Keys: 'india' and/or 'international'."""
        modes = ["india", "international"] if jurisdiction == "dual" else [jurisdiction]
        k = top_k or (self.dual_top_k if jurisdiction == "dual" else self.top_k)
        qvec = await self.embedder.embed_query(query)
        out: dict[str, RetrievalResult] = {}
        for mode in modes:
            dense = self.store.search(qvec, mode, top_k=max(k * 3, k))
            # hybrid re-rank: 0.8 dense + 0.2 keyword
            reranked = sorted(
                dense,
                key=lambda c: 0.8 * c.score + 0.2 * keyword_score(query, c.text),
                reverse=True,
            )[:k]
            # refresh scores to blended for thresholding consistency
            blended: list[ScoredChunk] = []
            for c in reranked:
                s = 0.8 * c.score + 0.2 * keyword_score(query, c.text)
                blended.append(ScoredChunk(id=c.id, score=round(s, 4),
                                           jurisdiction=c.jurisdiction,
                                           source_file=c.source_file, doc_name=c.doc_name,
                                           doc_type=c.doc_type, section_id=c.section_id,
                                           chunk_index=c.chunk_index, text=c.text))
            top = max((c.score for c in blended), default=0.0)
            out[mode] = RetrievalResult(chunks=blended, top_score=top, jurisdiction=mode)
        return out
