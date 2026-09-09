"""Turso-compatible vector store over SQLite (libsql dialect).

Tables:
  document_chunks(id, jurisdiction, source_file, doc_name, doc_type,
                  section_id, chunk_index, text, embedding JSON, created_at)
  queries_audit(id, query, jurisdiction, model, top_score, confidence,
                abstained, latency_ms, created_at)
  escalations(id, query, jurisdiction, contact, reason, context_json, created_at)

Cosine search is brute-force in Python (portable, no sqlite-vec needed)
partitioned strictly by jurisdiction.
"""
from __future__ import annotations

import json
import math
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS document_chunks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  jurisdiction TEXT NOT NULL CHECK (jurisdiction IN ('india','international')),
  source_file TEXT NOT NULL,
  doc_name TEXT NOT NULL DEFAULT '',
  doc_type TEXT NOT NULL DEFAULT '',
  section_id TEXT NOT NULL DEFAULT '',
  chunk_index INTEGER NOT NULL DEFAULT 0,
  text TEXT NOT NULL,
  embedding TEXT NOT NULL DEFAULT '[]',
  created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chunks_jurisdiction ON document_chunks(jurisdiction);
CREATE INDEX IF NOT EXISTS idx_chunks_source ON document_chunks(source_file);
CREATE TABLE IF NOT EXISTS queries_audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  query TEXT NOT NULL,
  jurisdiction TEXT NOT NULL DEFAULT 'dual',
  model TEXT NOT NULL DEFAULT '',
  top_score REAL NOT NULL DEFAULT 0,
  confidence REAL NOT NULL DEFAULT 0,
  abstained INTEGER NOT NULL DEFAULT 0,
  latency_ms REAL NOT NULL DEFAULT 0,
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS escalations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  query TEXT NOT NULL,
  jurisdiction TEXT NOT NULL DEFAULT 'dual',
  contact TEXT NOT NULL DEFAULT '',
  reason TEXT NOT NULL DEFAULT '',
  context_json TEXT NOT NULL DEFAULT '{}',
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS feedback (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL DEFAULT '',
  query TEXT NOT NULL,
  rating TEXT NOT NULL CHECK (rating IN ('up','down')),
  model TEXT NOT NULL DEFAULT '',
  created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_feedback_rating ON feedback(rating);
"""


@dataclass
class ScoredChunk:
    id: int
    score: float
    jurisdiction: str
    source_file: str
    doc_name: str
    doc_type: str
    section_id: str
    chunk_index: int
    text: str


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


class VectorStore:
    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)

    def init(self) -> None:
        with _connect(self.db_path) as conn:
            conn.executescript(SCHEMA)
            conn.commit()

    def clear_jurisdiction(self, jurisdiction: str) -> int:
        with _connect(self.db_path) as conn:
            cur = conn.execute("DELETE FROM document_chunks WHERE jurisdiction = ?", (jurisdiction,))
            conn.commit()
            return cur.rowcount

    def insert_chunks(self, rows: list[dict[str, Any]]) -> int:
        now = time.time()
        payload = [(
            r["jurisdiction"], r["source_file"], r.get("doc_name", ""),
            r.get("doc_type", ""), r.get("section_id", ""), int(r.get("chunk_index", 0)),
            r["text"], json.dumps(r["embedding"]), now,
        ) for r in rows]
        with _connect(self.db_path) as conn:
            conn.executemany(
                """INSERT INTO document_chunks
                   (jurisdiction, source_file, doc_name, doc_type, section_id,
                    chunk_index, text, embedding, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""", payload)
            conn.commit()
        return len(payload)

    def count(self, jurisdiction: Optional[str] = None) -> int:
        with _connect(self.db_path) as conn:
            if jurisdiction:
                row = conn.execute(
                    "SELECT COUNT(*) c FROM document_chunks WHERE jurisdiction=?",
                    (jurisdiction,)).fetchone()
            else:
                row = conn.execute("SELECT COUNT(*) c FROM document_chunks").fetchone()
            return int(row["c"])

    @staticmethod
    def cosine(a: list[float], b: list[float]) -> float:
        n = min(len(a), len(b))
        if n == 0:
            return 0.0
        dot = sum(x * y for x, y in zip(a[:n], b[:n]))
        na = math.sqrt(sum(x * x for x in a[:n])) or 1.0
        nb = math.sqrt(sum(y * y for y in b[:n])) or 1.0
        # map cosine [-1,1] -> [0,1] for stable thresholding
        return (dot / (na * nb) + 1.0) / 2.0

    def search(self, query_vec: list[float], jurisdiction: str,
               top_k: int = 5) -> list[ScoredChunk]:
        with _connect(self.db_path) as conn:
            rows = conn.execute(
                """SELECT id, jurisdiction, source_file, doc_name, doc_type,
                          section_id, chunk_index, text, embedding
                   FROM document_chunks WHERE jurisdiction = ?""",
                (jurisdiction,)).fetchall()
        scored: list[ScoredChunk] = []
        for r in rows:
            try:
                emb = json.loads(r["embedding"])
            except Exception:
                continue
            if not emb:
                continue
            scored.append(ScoredChunk(
                id=int(r["id"]), score=self.cosine(query_vec, [float(x) for x in emb]),
                jurisdiction=r["jurisdiction"], source_file=r["source_file"],
                doc_name=r["doc_name"], doc_type=r["doc_type"],
                section_id=r["section_id"], chunk_index=int(r["chunk_index"]),
                text=r["text"]))
        scored.sort(key=lambda s: s.score, reverse=True)
        return scored[:top_k]

    @staticmethod
    def _row_to_chunk(r: sqlite3.Row, score: float = 0.0) -> ScoredChunk:
        return ScoredChunk(
            id=int(r["id"]), score=score, jurisdiction=r["jurisdiction"],
            source_file=r["source_file"], doc_name=r["doc_name"],
            doc_type=r["doc_type"], section_id=r["section_id"],
            chunk_index=int(r["chunk_index"]), text=r["text"])

    def list_chunks(self, jurisdiction: Optional[str] = None,
                    limit: Optional[int] = None,
                    offset: int = 0) -> list[dict[str, Any]]:
        """Raw chunk rows (no embeddings) for batch jobs like graph ingestion."""
        q = ("SELECT id, jurisdiction, source_file, doc_name, doc_type, "
             "section_id, chunk_index, text FROM document_chunks")
        params: list[Any] = []
        if jurisdiction:
            q += " WHERE jurisdiction = ?"
            params.append(jurisdiction)
        q += " ORDER BY id"
        if limit is not None:
            q += " LIMIT ? OFFSET ?"
            params += [limit, offset]
        with _connect(self.db_path) as conn:
            return [dict(r) for r in conn.execute(q, params).fetchall()]

    def get_chunks_by_ids(self, ids: list[int]) -> list[ScoredChunk]:
        """Fetch chunks by id (score 0.0; caller assigns graph relevance)."""
        if not ids:
            return []
        placeholders = ",".join("?" for _ in ids)
        with _connect(self.db_path) as conn:
            rows = conn.execute(
                """SELECT id, jurisdiction, source_file, doc_name, doc_type,
                          section_id, chunk_index, text
                   FROM document_chunks WHERE id IN ({placeholders})"""
                .format(placeholders=placeholders), ids).fetchall()
        order = {i: n for n, i in enumerate(ids)}
        out = [self._row_to_chunk(r) for r in rows]
        out.sort(key=lambda c: order.get(c.id, 0))
        return out

    def log_query(self, query: str, jurisdiction: str, model: str,
                  top_score: float, confidence: float, abstained: bool,
                  latency_ms: float) -> int:
        with _connect(self.db_path) as conn:
            cur = conn.execute(
                """INSERT INTO queries_audit
                   (query, jurisdiction, model, top_score, confidence, abstained,
                    latency_ms, created_at) VALUES (?,?,?,?,?,?,?,?)""",
                (query[:4000], jurisdiction, model, top_score, confidence,
                 1 if abstained else 0, latency_ms, time.time()))
            conn.commit()
            return int(cur.lastrowid)

    def log_escalation(self, query: str, jurisdiction: str, contact: str,
                       reason: str, context: dict[str, Any]) -> int:
        with _connect(self.db_path) as conn:
            cur = conn.execute(
                """INSERT INTO escalations
                   (query, jurisdiction, contact, reason, context_json, created_at)
                   VALUES (?,?,?,?,?,?)""",
                (query[:4000], jurisdiction, contact[:200], reason[:2000],
                 json.dumps(context)[:8000], time.time()))
            conn.commit()
            return int(cur.lastrowid)

    def log_feedback(self, session_id: str, query: str, rating: str,
                     model: str = "") -> int:
        assert rating in ("up", "down")
        with _connect(self.db_path) as conn:
            cur = conn.execute(
                """INSERT INTO feedback
                   (session_id, query, rating, model, created_at)
                   VALUES (?,?,?,?,?)""",
                (session_id[:64], query[:2000], rating, model[:120], time.time()))
            conn.commit()
            return int(cur.lastrowid)
