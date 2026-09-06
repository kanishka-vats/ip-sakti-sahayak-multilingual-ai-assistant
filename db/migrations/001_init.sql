-- 001_init.sql — Turso / libsql compatible schema (idempotent).
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
