"""Async OpenRouter embedding client with exponential backoff.

Model resolved from config/models.yaml (never hardcoded at call sites).
Falls back to deterministic hash embeddings when no API key is configured
so ingestion / tests work offline.
"""
from __future__ import annotations

import asyncio
import hashlib
import math
import random
import re
from typing import Sequence

import httpx

from config.settings import get_settings

OPENROUTER_URL = "https://openrouter.ai/api/v1/embeddings"


def hash_embedding(text: str, dim: int = 384) -> list[float]:
    """Deterministic offline fallback: hashed bag-of-tokens, L2-normalized."""
    vec = [0.0] * dim
    for tok in text.lower().split():
        h = int(hashlib.sha256(tok.encode()).hexdigest(), 16)
        vec[h % dim] += 1.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def sanitize(text: str) -> str:
    """Strip control/garbage chars from PDF extraction that poison APIs."""
    return "".join(ch for ch in text if ch in ("\n", "\t") or ord(ch) >= 32).strip() or " "


class Embedder:
    def __init__(self, api_key: str | None = None, model: str | None = None,
                 dim: int | None = None, batch_size: int = 32):
        s = get_settings()
        self.api_key = api_key or s.openrouter_key
        self.model = model or s.embedding_model
        self.dim = dim or s.embedding_dim
        self.batch_size = batch_size or s.embedding_batch_size
        self.dimensions_param = s.embedding_dimensions_param
        self.max_input_tokens = s.embedding_max_input_tokens

    @property
    def offline(self) -> bool:
        return not self.api_key

    async def _embed_batch(self, client: httpx.AsyncClient, texts: list[str]) -> list[list[float]]:
        """Embed one batch; on HTTP 400 (batch too big for free tier) split
        recursively so a single oversized chunk can't kill the run."""
        try:
            return await self._post_embeddings(client, texts)
        except RuntimeError as exc:
            if "400" in str(exc) and len(texts) > 1:
                mid = len(texts) // 2
                first = await self._embed_batch(client, texts[:mid])
                second = await self._embed_batch(client, texts[mid:])
                return first + second
            raise

    async def _post_embeddings(self, client: httpx.AsyncClient, texts: list[str]) -> list[list[float]]:
        max_retries = 5
        delay = 1.0
        last_err: Exception | None = None
        body: dict = {"model": self.model, "input": texts}
        if self.dimensions_param:
            body["dimensions"] = self.dimensions_param
        for attempt in range(max_retries):
            try:
                resp = await client.post(
                    OPENROUTER_URL,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                        "HTTP-Referer": "https://sih-project-26045.local",
                        "X-Title": "SIH IP-RAG",
                    },
                    json=body,
                    timeout=60.0,
                )
                if resp.status_code == 429 or resp.status_code >= 500:
                    raise RuntimeError(f"OpenRouter {resp.status_code}: {resp.text[:300]}")
                resp.raise_for_status()
                data = resp.json()
                items = data.get("data", [])
                vecs: list[list[float]] = []
                for item in items:
                    emb = item.get("embedding", [])
                    vecs.append(_fit_dim([float(x) for x in emb], self.dim))
                if len(vecs) != len(texts):
                    raise RuntimeError(f"Embedding count mismatch: {len(vecs)} vs {len(texts)}")
                return vecs
            except Exception as exc:  # backoff + jitter
                last_err = exc
                if attempt == max_retries - 1:
                    break
                await asyncio.sleep(delay + random.uniform(0, 0.5))
                delay *= 2
        raise RuntimeError(f"OpenRouter embedding failed after retries: {last_err}")

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        texts = [sanitize(t or " ") for t in texts]
        if self.offline:
            return [hash_embedding(t, self.dim) for t in texts]
        # Split only when the text exceeds the model's per-input token cap
        # (e.g. 512 for LFM; 8191 for text-embedding-3-small fits chunks whole).
        budget = 400
        async with httpx.AsyncClient() as client:
            while True:
                pieces: list[str] = []
                owner: list[int] = []
                for idx, t in enumerate(texts):
                    if len(t) // 4 <= self.max_input_tokens:
                        pieces.append(t)
                        owner.append(idx)
                    else:
                        for p in _split_for_embedding(t, max_tokens=budget):
                            pieces.append(p)
                            owner.append(idx)
                try:
                    piece_vecs = await self._embed_pieces(client, pieces)
                    return _mean_pool(piece_vecs, owner, len(texts))
                except RuntimeError as exc:
                    msg = str(exc)
                    if "400" in msg and budget > 25:
                        print(f"[embedder] 400 rejected @budget {budget} "
                              f"(max piece {max(len(p) for p in pieces)} chars); "
                              f"halving to {budget // 2}.")
                        budget //= 2
                        continue
                    if "FIRST-BATCH" in msg:
                        # Nothing embedded yet: safe to commit the WHOLE run
                        # to offline hash embeddings (no mixed vector spaces).
                        print(f"[embedder] OpenRouter unreachable ({msg}); "
                              "using offline hash embeddings for entire run.")
                        self.api_key = ""
                        return [hash_embedding(t, self.dim) for t in texts]
                    raise

    async def _embed_pieces(self, client: httpx.AsyncClient,
                            pieces: list[str]) -> list[list[float]]:
        piece_vecs: list[list[float]] = []
        for i in range(0, len(pieces), self.batch_size):
            batch = pieces[i:i + self.batch_size]
            try:
                piece_vecs.extend(await self._embed_batch(client, batch))
            except Exception as exc:
                if not piece_vecs:
                    raise RuntimeError(f"FIRST-BATCH {exc}")
                raise RuntimeError(
                    f"OpenRouter failed mid-run after {len(piece_vecs)} vectors: {exc}")
        return piece_vecs

    async def embed_query(self, text: str) -> list[float]:
        return (await self.embed([text]))[0]


def _fit_dim(vec: list[float], dim: int) -> list[float]:
    if len(vec) == dim:
        return vec
    if len(vec) > dim:
        out = vec[:dim]
    else:
        out = vec + [0.0] * (dim - len(vec))
    n = math.sqrt(sum(v * v for v in out)) or 1.0
    return [v / n for v in out]


_SENT_SPLIT = re.compile(r"(?<=[.!?;])\s+|\n+")


def _split_for_embedding(text: str, max_tokens: int = 400) -> list[str]:
    """Split text into <=max_tokens pieces on sentence boundaries.

    The OpenRouter embedding model rejects inputs over 512 tokens, while
    legal chunks run 400-800 tokens — so chunk text must be sub-split.
    Uses a conservative ~4 chars/token estimate.
    """
    if len(text) // 4 <= max_tokens:
        return [text]
    sents = [s.strip() for s in _SENT_SPLIT.split(text) if s and s.strip()]
    parts: list[str] = []
    buf: list[str] = []
    buf_chars = 0
    budget = max_tokens * 4
    for s in sents:
        if buf_chars + len(s) > budget and buf:
            parts.append(" ".join(buf))
            buf, buf_chars = [], 0
        # A single pathological sentence over budget: hard-slice it.
        while len(s) > budget:
            parts.append(s[:budget])
            s = s[budget:]
        buf.append(s)
        buf_chars += len(s)
    if buf:
        parts.append(" ".join(buf))
    return parts or [text]


def _mean_pool(vecs: list[list[float]], owner: list[int], n_texts: int) -> list[list[float]]:
    """Average piece vectors per original text, then L2-normalize."""
    dim = len(vecs[0])
    sums = [[0.0] * dim for _ in range(n_texts)]
    counts = [0] * n_texts
    for v, idx in zip(vecs, owner):
        for j, x in enumerate(v):
            sums[idx][j] += x
        counts[idx] += 1
    out: list[list[float]] = []
    for s, c in zip(sums, counts):
        avg = [x / max(1, c) for x in s]
        n = math.sqrt(sum(x * x for x in avg)) or 1.0
        out.append([x / n for x in avg])
    return out
