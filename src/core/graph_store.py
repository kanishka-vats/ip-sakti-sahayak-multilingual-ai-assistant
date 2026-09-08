"""Relational knowledge-graph store (Phase 1), alongside the flat chunk table.

Tables (see db/migrations/003_graph.sql — applied idempotently here):
  entities(id, type, name, description, jurisdiction, source_chunk_id,
           embedding JSON, confidence, created_at)
  edges(id, source_id, target_id, relation_type, created_at)

Non-breaking by construction: only CREATE TABLE / INDEX IF NOT EXISTS;
`document_chunks` and all existing tables are never altered.

Recommended vocabularies (open TEXT columns, not enforced):
  entity types: act, section, rule, herb, formulation, doctrine, authority,
    treaty, form, case, concept
  relation types: defines, requires, defeats, exempts, amends, references,
    protects, governs, penalizes, subject_to

Two deliberate additions beyond the bare (id/type/name/description) spec,
both required by the rest of Phase 1:
  - `embedding` on entities: task 3 needs vector search over entities.
  - `jurisdiction` on entities: the codebase-wide isolation invariant
    (india/international never mix) must hold for graph traversal too.
  - `source_chunk_id`: provenance + the bridge used when merging graph
    context back into flat-chunk results.
"""
from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from src.core.vector_store import VectorStore  # shared cosine mapping

MIGRATION = Path(__file__).resolve().parents[2] / "db" / "migrations" / "003_graph.sql"


@dataclass
class Entity:
    id: int
    type: str
    name: str
    description: str
    jurisdiction: str
    source_chunk_id: int
    confidence: float


@dataclass
class Edge:
    id: int
    source_id: int
    target_id: int
    relation_type: str


@dataclass
class ScoredEntity:
    entity: Entity
    score: float


@dataclass
class Neighbor:
    entity: Entity
    edge: Edge
    outgoing: bool  # True if edge runs entity --relation--> neighbor


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


class GraphStore:
    """Graph CRUD + 1-hop traversal + entity vector search."""

    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)

    def init(self) -> None:
        with _connect(self.db_path) as conn:
            conn.executescript(MIGRATION.read_text(encoding="utf-8"))
            conn.commit()

    # ---------- writes ----------

    def upsert_entity(self, type: str, name: str, description: str = "",
                      jurisdiction: str = "india", source_chunk_id: int = 0,
                      embedding: list[float] | None = None,
                      confidence: float = 1.0) -> int:
        """Dedupe on (type, name, jurisdiction); keep the longer description."""
        name = " ".join(name.split())
        with _connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT id, description FROM entities "
                "WHERE type = ? AND name = ? AND jurisdiction = ?",
                (type.strip().lower(), name, jurisdiction)).fetchone()
            if row:
                if len(description) > len(row["description"] or ""):
                    conn.execute("UPDATE entities SET description = ? WHERE id = ?",
                                 (description, row["id"]))
                    conn.commit()
                return int(row["id"])
            cur = conn.execute(
                """INSERT INTO entities
                   (type, name, description, jurisdiction, source_chunk_id,
                    embedding, confidence, created_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (type.strip().lower(), name, description, jurisdiction,
                 source_chunk_id, json.dumps(embedding or []), confidence,
                 time.time()))
            conn.commit()
            return int(cur.lastrowid)

    def add_edge(self, source_id: int, target_id: int, relation_type: str) -> int:
        """Idempotent on (source, target, relation)."""
        rel = " ".join(relation_type.split()).lower() or "references"
        with _connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT id FROM edges WHERE source_id = ? AND target_id = ? "
                "AND relation_type = ?", (source_id, target_id, rel)).fetchone()
            if row:
                return int(row["id"])
            cur = conn.execute(
                """INSERT INTO edges (source_id, target_id, relation_type, created_at)
                   VALUES (?,?,?,?)""",
                (source_id, target_id, rel, time.time()))
            conn.commit()
            return int(cur.lastrowid)

    # ---------- reads ----------

    @staticmethod
    def _row_to_entity(r: sqlite3.Row) -> Entity:
        return Entity(id=int(r["id"]), type=r["type"], name=r["name"],
                      description=r["description"] or "",
                      jurisdiction=r["jurisdiction"],
                      source_chunk_id=int(r["source_chunk_id"] or 0),
                      confidence=float(r["confidence"] or 0.0))

    def get_entity(self, entity_id: int) -> Optional[Entity]:
        with _connect(self.db_path) as conn:
            row = conn.execute("SELECT * FROM entities WHERE id = ?",
                               (entity_id,)).fetchone()
            return self._row_to_entity(row) if row else None

    def neighbors(self, entity_id: int,
                  relation_types: list[str] | None = None,
                  depth: int = 1) -> list[Neighbor]:
        """BFS traversal (default 1-hop, both directions), cycle-safe."""
        rel_filter = {r.lower() for r in (relation_types or [])}
        seen_entities = {entity_id}
        frontier = [entity_id]
        out: list[Neighbor] = []
        with _connect(self.db_path) as conn:
            for _ in range(max(1, depth)):
                next_frontier: list[int] = []
                for eid in frontier:
                    rows = conn.execute(
                        """SELECT e.*, g.id AS edge_id, g.source_id, g.target_id,
                                  g.relation_type
                           FROM edges g JOIN entities e
                             ON e.id = CASE WHEN g.source_id = ? THEN g.target_id
                                           ELSE g.source_id END
                           WHERE g.source_id = ? OR g.target_id = ?""",
                        (eid, eid, eid)).fetchall()
                    for r in rows:
                        if rel_filter and r["relation_type"] not in rel_filter:
                            continue
                        nid = int(r["id"])
                        if nid in seen_entities:
                            continue
                        seen_entities.add(nid)
                        next_frontier.append(nid)
                        out.append(Neighbor(
                            entity=self._row_to_entity(r),
                            edge=Edge(id=int(r["edge_id"]),
                                      source_id=int(r["source_id"]),
                                      target_id=int(r["target_id"]),
                                      relation_type=r["relation_type"]),
                            outgoing=int(r["source_id"]) == eid))
                frontier = next_frontier
                if not frontier:
                    break
        return out

    def search_entities(self, query_vec: list[float], jurisdiction: str,
                        top_k: int = 5) -> list[ScoredEntity]:
        """Cosine search over entity embeddings, jurisdiction-isolated.

        Uses the identical [-1,1] -> [0,1] mapping as the chunk store so
        graph and flat scores stay comparable for later merging.
        """
        with _connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM entities WHERE jurisdiction = ?",
                (jurisdiction,)).fetchall()
        scored: list[ScoredEntity] = []
        for r in rows:
            try:
                emb = [float(x) for x in json.loads(r["embedding"])]
            except Exception:
                continue
            if not emb:
                continue
            scored.append(ScoredEntity(entity=self._row_to_entity(r),
                                       score=VectorStore.cosine(query_vec, emb)))
        scored.sort(key=lambda s: s.score, reverse=True)
        return scored[:top_k]

    def chunk_ids_for_entities(self, entity_ids: list[int]) -> list[int]:
        """Distinct source chunk ids backing the given entities (merge bridge)."""
        if not entity_ids:
            return []
        placeholders = ",".join("?" for _ in entity_ids)
        with _connect(self.db_path) as conn:
            rows = conn.execute(
                f"SELECT DISTINCT source_chunk_id FROM entities "
                f"WHERE id IN ({placeholders}) AND source_chunk_id > 0",
                entity_ids).fetchall()
        return [int(r["source_chunk_id"]) for r in rows]

    def stats(self) -> dict[str, Any]:
        with _connect(self.db_path) as conn:
            n_e = conn.execute("SELECT COUNT(*) c FROM entities").fetchone()["c"]
            n_g = conn.execute("SELECT COUNT(*) c FROM edges").fetchone()["c"]
            by_type = {r["type"]: r["c"] for r in conn.execute(
                "SELECT type, COUNT(*) c FROM entities GROUP BY type")}
        return {"entities": int(n_e), "edges": int(n_g), "by_type": by_type}

    def covered_chunk_ids(self, jurisdiction: Optional[str] = None) -> set[int]:
        """Chunk ids already processed by the ingester (resume support)."""
        q = "SELECT DISTINCT source_chunk_id c FROM entities WHERE source_chunk_id > 0"
        params: list[Any] = []
        if jurisdiction:
            q += " AND jurisdiction = ?"
            params.append(jurisdiction)
        with _connect(self.db_path) as conn:
            return {int(r["c"]) for r in conn.execute(q, params).fetchall()}
