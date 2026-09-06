# IP-SAKTI Sahayak — Multilingual Legal/IP RAG (SIH 26045)

Perplexity-style split-jurisdiction RAG over curated Indian + International IP corpus.
Strict citation grounding, zero extrapolation, ABS/TKDL aware, DPDP compliant.

## Quickstart (uv)

```powershell
uv sync
uv run uvicorn src.api.main:app --port 8000
# open http://localhost:8000 and just ask. Corpus is pre-ingested
# (db/sih-projdb.db). To re-ingest: POST /api/ingest
```

## How to use
1. **Just chat** — jurisdiction is auto-routed server-side (India / International / Dual).
2. **Ask**, then ask follow-ups (`explain it`, `tell me more`) — the previous turn is sent as context.
3. **Edit any query** via the pencil on its bubble — resending branches the chat (later turns are dropped).
4. **Read citations**: click `[^1]`-style pills → source excerpt + act + section + confidence + official verify link.
5. **Watch badges**: ABS (BD Act Sec 3/4/6) and TKDL prior-art cautions appear under answers.
6. Sessions persist in the browser; rate answers with 👍/👎 (stored via `/api/feedback`).
7. Small print below the query box: *Information only; not legal advice.*

## Endpoints
| Method | Path | Purpose |
|---|---|---|
| POST | `/api/query` | single-shot grounded answer (supports `context_query`/`context_answer`, `clarification` + `suggestions` in reply) |
| POST | `/api/query/stream` | SSE `meta → token* → citations → done` |
| POST | `/api/ingest?limit_files=200` | embed `raw_data/` into DB |
| GET | `/api/models` | active LLM/embedding config |
| POST | `/api/escalate` | facilitator handoff ticket |
| POST | `/api/feedback` | answer usefulness rating (`up`/`down`) |
| GET | `/health` | status + chunk counts |

## Configuration
- `config/models.yaml` — LLM (`groq`, `qwen/qwen3.8-27b`; fallbacks `qwen/qwen3.6-27b`,
  `openai/gpt-oss-20b`; verified live via Groq `/models`), embeddings (OpenRouter
  `openai/text-embedding-3-small`, native 384-d), retrieval `top_k: 5`, threshold `0.65`
  (calibrated: relevant 0.81+, unrelated ~0.53).
- `.env` — `GROQ_API_KEY`, `OPENROUTER_API_KEY` (both optional: app runs offline with
  hash embeddings + extractive answers), `TURSO_DATABASE_URL` / `TURSO_AUTH_TOKEN`
  (optional: point at Turso Cloud; default local `db/sih-projdb.db`).
- Never hardcode model names — use `config/settings.py::get_settings()`.

## Turso MCP — is it running? Do I need it?
**Short answer: no daemon needed.** This project uses a Turso-compatible store:
plain SQLite/libsql file at `db/sih-projdb.db` (schema in `db/migrations/001_init.sql`),
so queries, ingest, audit and escalations all work locally with zero config.

Check:
```powershell
# 1. DB file + tables exist?
uv run python -c "import sqlite3; c=sqlite3.connect('db/sih-projdb.db'); print([r[0] for r in c.execute(\"SELECT name FROM sqlite_master WHERE type='table'\")])"
# expect: document_chunks, queries_audit, escalations, feedback

# 2. Remote Turso Cloud configured? (optional)
#    Only needed for multi-device sync. Set in .env:
#    TURSO_DATABASE_URL=libsql://<db>.turso.io  TURSO_AUTH_TOKEN=...
```

If you *want* the Turso MCP server for agent tooling (optional), the npm package
`@turso/mcp` does not exist on the public registry; use the official libsql client
instead (`npm i @libsql/client`) or the Turso CLI (`turso db shell sih-projdb`).
Nothing in this codebase depends on an MCP daemon — `VectorStore` falls back to the
local file automatically.

## Layout
`config/` models+settings · `src/ingestion` parser/chunker/embedder ·
`src/core` vector_store/retriever/generator/guardrails ·
`src/services` jurisdiction_router/abs_compliance/tkdl_checker/scope_gate/typo_fixer/citation_links ·
`src/api` main/routes/schemas · `frontend/` obsidian UI (sessions, edit-branching, feedback) ·
`docs/` architecture/api/data dict/report.
