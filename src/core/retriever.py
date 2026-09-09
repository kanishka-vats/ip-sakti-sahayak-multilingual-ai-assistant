"""Hybrid search with jurisdiction-isolated filtering.

Dense cosine over stored embeddings + lightweight BM25-ish keyword score.
Dual mode runs two isolated searches (never merges corpora pre-scoring).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from config.settings import get_settings
from src.core.graph_store import Entity, GraphStore
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


@dataclass
class GraphResult:
    """1-hop graph expansion for one jurisdiction."""
    chunks: list[ScoredChunk]  # graph-sourced chunks (scored)
    entities: list[Entity]  # seed + neighbor entities
    statements: list[str]  # "A -[relation]-> B" facts for prompts
    top_score: float
    jurisdiction: str


class Retriever:
    def __init__(self, store: VectorStore | None = None, embedder: Embedder | None = None,
                 graph: GraphStore | None = None):
        s = get_settings()
        from pathlib import Path  # local to avoid circulars
        self.store = store or VectorStore(s.db_path_abs)
        self.embedder = embedder or Embedder()
        self._graph = graph
        self.top_k = s.top_k
        self.dual_top_k = s.dual_top_k

    @property
    def graph(self) -> GraphStore:
        if self._graph is None:
            self._graph = GraphStore(get_settings().db_path_abs)
        return self._graph

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

    async def graph_search(self, query: str, jurisdiction: str = "dual",
                           entity_top_k: int = 4,
                           chunk_top_k: int = 5) -> dict[str, GraphResult]:
        """1-hop expansion: embed -> seed entities -> neighbors -> chunks.

        Returns per-jurisdiction graph context. Empty graph degrades to
        empty results (never raises), so flat search always still works.
        """
        try:
            self.graph.init()
            qvec = await self.embedder.embed_query(query)
        except Exception:
            return {}
        modes = ["india", "international"] if jurisdiction == "dual" else [jurisdiction]
        out: dict[str, GraphResult] = {}
        for mode in modes:
            try:
                seeds = self.graph.search_entities(qvec, mode, top_k=entity_top_k)
            except Exception:
                seeds = []
            ent_by_id: dict[int, Entity] = {}
            ent_score: dict[int, float] = {}
            statements: list[str] = []
            for s in seeds:
                ent_by_id[s.entity.id] = s.entity
                ent_score[s.entity.id] = max(ent_score.get(s.entity.id, 0.0), s.score)
                try:
                    nbs = self.graph.neighbors(s.entity.id)
                except Exception:
                    nbs = []
                for nb in nbs:
                    ent_by_id[nb.entity.id] = nb.entity
                    # 1-hop neighbors inherit a discounted seed score so their
                    # chunks stay competitive in the merge.
                    ent_score[nb.entity.id] = max(
                        ent_score.get(nb.entity.id, 0.0), s.score * 0.7)
                    a, b = (s.entity.name, nb.entity.name) if nb.outgoing else (
                        nb.entity.name, s.entity.name)
                    statements.append(f"{a} -[{nb.edge.relation_type}]-> {b}")
            try:
                chunk_ids = self.graph.chunk_ids_for_entities(list(ent_by_id))
            except Exception:
                chunk_ids = []
            gchunks = self.store.get_chunks_by_ids(chunk_ids[:chunk_top_k * 3])
            scored: list[ScoredChunk] = []
            for c in gchunks[:chunk_top_k]:
                # chunk score = best contributing entity score, slightly
                # discounted so direct flat hits win ties in the merge.
                best = 0.0
                for eid, escore in ent_score.items():
                    e = ent_by_id.get(eid)
                    if e is not None and e.source_chunk_id == c.id and escore > best:
                        best = escore
                scored.append(ScoredChunk(
                    id=c.id, score=round(best * 0.95, 4),
                    jurisdiction=c.jurisdiction, source_file=c.source_file,
                    doc_name=c.doc_name, doc_type=c.doc_type,
                    section_id=c.section_id, chunk_index=c.chunk_index,
                    text=c.text))
            scored.sort(key=lambda c: c.score, reverse=True)
            top = max((c.score for c in scored), default=0.0)
            out[mode] = GraphResult(chunks=scored, entities=list(ent_by_id.values()),
                                    statements=list(dict.fromkeys(statements))[:12],
                                    top_score=top, jurisdiction=mode)
        return out

    @staticmethod
    def merge_results(flat: RetrievalResult, gchunks: list[ScoredChunk],
                      top_k: int | None = None) -> RetrievalResult:
        """Union flat + graph chunks, dedupe by id keeping max score."""
        best: dict[int, ScoredChunk] = {}
        for c in list(flat.chunks) + list(gchunks):
            if c.id not in best or c.score > best[c.id].score:
                best[c.id] = c
        merged = sorted(best.values(), key=lambda c: c.score, reverse=True)
        if top_k is not None:
            merged = merged[:top_k]
        return RetrievalResult(
            chunks=merged,
            top_score=max((c.score for c in merged), default=0.0),
            jurisdiction=flat.jurisdiction)
