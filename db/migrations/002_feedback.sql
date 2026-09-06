-- 002_feedback.sql — answer usefulness ratings (idempotent).
CREATE TABLE IF NOT EXISTS feedback (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL DEFAULT '',
  query TEXT NOT NULL,
  rating TEXT NOT NULL CHECK (rating IN ('up','down')),
  model TEXT NOT NULL DEFAULT '',
  created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_feedback_rating ON feedback(rating);
