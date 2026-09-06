"""Clause-level semantic chunker: 400-800 tokens, 10% overlap, section-aware."""
from __future__ import annotations

import re
from dataclasses import dataclass

from .parser import Document

MIN_TOKENS = 400
MAX_TOKENS = 800
OVERLAP_RATIO = 0.10

# Clause boundaries typical in legal text
CLAUSE_SPLIT = re.compile(
    r"(?<=\.)\s+(?=(?:Section|Article|Rule|Chapter|Provided that|Explanation|Whereas)\b)|"
    r"\n\s*\n|"
    r"(?<=[;:.])\s+(?=\(\w+\)|\d+\.\s+[A-Z])"
)


def approx_tokens(text: str) -> int:
    # ~4 chars per token heuristic for English legal text
    return max(1, len(text) // 4)


@dataclass
class Chunk:
    text: str
    jurisdiction: str
    source_file: str
    doc_type: str
    section_id: str
    doc_name: str
    chunk_index: int


def _split_clauses(text: str) -> list[str]:
    parts = [p.strip() for p in CLAUSE_SPLIT.split(text) if p and p.strip()]
    # merge tiny fragments
    merged: list[str] = []
    for p in parts:
        if merged and len(merged[-1]) < 120:
            merged[-1] = merged[-1] + " " + p
        else:
            merged.append(p)
    return merged or [text]


def chunk_document(doc: Document, start_index: int = 0) -> list[Chunk]:
    clauses = _split_clauses(doc.text)
    chunks: list[Chunk] = []
    buf: list[str] = []
    buf_tokens = 0
    idx = start_index
    for clause in clauses:
        ct = approx_tokens(clause)
        if buf_tokens + ct > MAX_TOKENS and buf_tokens >= MIN_TOKENS:
            text = " ".join(buf).strip()
            chunks.append(Chunk(text=text, jurisdiction=doc.jurisdiction,
                                source_file=doc.source_file, doc_type=doc.doc_type,
                                section_id=doc.section_id, doc_name=doc.doc_name,
                                chunk_index=idx))
            idx += 1
            # 10% overlap: carry trailing chars
            overlap_chars = int(len(text) * OVERLAP_RATIO)
            carry = text[-overlap_chars:] if overlap_chars > 0 else ""
            buf = [carry] if carry else []
            buf_tokens = approx_tokens(carry)
        buf.append(clause)
        buf_tokens += ct
    if buf:
        text = " ".join(buf).strip()
        if approx_tokens(text) >= 40:
            chunks.append(Chunk(text=text, jurisdiction=doc.jurisdiction,
                                source_file=doc.source_file, doc_type=doc.doc_type,
                                section_id=doc.section_id, doc_name=doc.doc_name,
                                chunk_index=idx))
    return chunks


def chunk_documents(docs: list[Document]) -> list[Chunk]:
    out: list[Chunk] = []
    counters: dict[str, int] = {}
    for doc in docs:
        key = doc.source_file
        start = counters.get(key, 0)
        made = chunk_document(doc, start)
        counters[key] = start + len(made)
        out.extend(made)
    return out
