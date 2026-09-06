"""Hallucination detector, abstention triggers, DPDP filters."""
from __future__ import annotations

import re
from dataclasses import dataclass

from config.settings import get_settings
from src.core.vector_store import ScoredChunk

PII_PATTERNS = [
    re.compile(r"\b\d{12}\b"),  # Aadhaar-like
    re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b"),  # PAN-like
    re.compile(r"\b\d{10}\b"),  # phone-like
    re.compile(r"[\w.%-]+@[\w.-]+\.[A-Za-z]{2,}"),  # email
]

ABSTAIN = None  # resolved lazily from settings


@dataclass
class GateDecision:
    abstain: bool
    confidence: float
    top_score: float
    reason: str


def token_overlap(query: str, chunks: list[ScoredChunk]) -> float:
    qtokens = set(re.findall(r"[a-z0-9]{3,}", query.lower()))
    if not qtokens or not chunks:
        return 0.0
    best = 0.0
    for c in chunks:
        ct = set(re.findall(r"[a-z0-9]{3,}", c.text.lower()[:4000]))
        overlap = len(qtokens & ct) / len(qtokens)
        best = max(best, overlap)
    return best


def confidence_score(chunks: list[ScoredChunk], query: str) -> float:
    """0.0-1.0 from retrieval density + token overlap."""
    if not chunks:
        return 0.0
    top = chunks[0].score
    mean_top3 = sum(c.score for c in chunks[:3]) / min(3, len(chunks))
    density = sum(1 for c in chunks if c.score >= 0.6) / len(chunks)
    overlap = token_overlap(query, chunks)
    conf = 0.45 * top + 0.25 * mean_top3 + 0.15 * density + 0.15 * overlap
    return round(max(0.0, min(1.0, conf)), 3)


def gate(query: str, chunks: list[ScoredChunk], threshold: float | None = None) -> GateDecision:
    s = get_settings()
    thr = threshold if threshold is not None else s.confidence_threshold
    top = max((c.score for c in chunks), default=0.0)
    conf = confidence_score(chunks, query)
    if not chunks or top < thr:
        return GateDecision(abstain=True, confidence=conf, top_score=top,
                            reason=f"max_similarity {top:.3f} < threshold {thr}")
    # Lexical-overlap veto: dense embeddings can rate pure-semantic neighbors
    # ~0.65+ even when the query shares ZERO vocabulary with the corpus
    # (e.g. slang/memes). Such queries are out-of-context by construction.
    # Exact-phrase matches (0.81+) are exempt.
    ov = token_overlap(query, chunks)
    if ov < 0.1 and top < 0.80:
        return GateDecision(abstain=True, confidence=conf, top_score=top,
                            reason=f"zero lexical overlap ({ov:.2f}) with top {top:.3f}")
    return GateDecision(abstain=False, confidence=conf, top_score=top, reason="grounded")


def scrub_pii(text: str) -> str:
    out = text
    for pat in PII_PATTERNS:
        out = pat.sub("[REDACTED]", out)
    return out


def verify_citations(answer: str, n_citations: int) -> list[str]:
    """Return list of problems: every [^N] must satisfy 1<=N<=n_citations."""
    problems: list[str] = []
    refs = [int(m) for m in re.findall(r"\[\^(\d+)\]", answer)]
    for r in refs:
        if r < 1 or r > max(1, n_citations):
            problems.append(f"citation [^{r}] out of range (1..{n_citations})")
    return problems


def _snip(raw: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", raw).strip()
    if len(text) <= limit:
        return text
    quote = text[:limit]
    cut = max(quote.rfind(". "), quote.rfind("; "), quote.rfind(" — "))
    return (quote[: cut + 1] if cut > limit * 0.5 else quote).rstrip() + " …"


def citation_payload(c: ScoredChunk, max_quote: int = 200) -> dict:
    """Compact display quote (2-3 lines) + full excerpt kept server-side for
    generation, so shortening display text never starves the model."""
    raw = re.sub(r"\s+", " ", c.text).strip()
    return {
        "chunk_id": c.id,
        "act_name": c.doc_name,
        "section": c.section_id,
        "doc_type": c.doc_type,
        "source_file": c.source_file,
        "confidence": round(c.score, 3),
        "quote": _snip(raw, max_quote),
        "excerpt": raw[:1200],
    }
