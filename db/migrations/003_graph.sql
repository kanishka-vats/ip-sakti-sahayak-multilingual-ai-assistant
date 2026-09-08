-- 003_graph.sql — relational knowledge-graph tables (Phase 1, additive).
-- Idempotent; existing tables (document_chunks, queries_audit, escalations,
-- feedback) are untouched. Jurisdiction isolation mirrors the chunk store.
CREATE TABLE IF NOT EXISTS entities (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  type TEXT NOT NULL DEFAULT 'concept',
  name TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  jurisdiction TEXT NOT NULL DEFAULT 'india'
    CHECK (jurisdiction IN ('india','international')),
  source_chunk_id INTEGER NOT NULL DEFAULT 0,
  embedding TEXT NOT NULL DEFAULT '[]',
  confidence REAL NOT NULL DEFAULT 1.0,
  created_at REAL NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_entities_unique
  ON entities(type, name, jurisdiction);
CREATE INDEX IF NOT EXISTS idx_entities_type ON entities(type);
CREATE INDEX IF NOT EXISTS idx_entities_jurisdiction ON entities(jurisdiction);
CREATE TABLE IF NOT EXISTS edges (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_id INTEGER NOT NULL REFERENCES entities(id),
  target_id INTEGER NOT NULL REFERENCES entities(id),
  relation_type TEXT NOT NULL DEFAULT 'references',
  created_at REAL NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_edges_unique
  ON edges(source_id, target_id, relation_type);
CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(source_id);
CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(target_id);
