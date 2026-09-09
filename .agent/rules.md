# Agent Rules — IP RAG Platform (SIH 26045)

## Architectural invariants
1. **Never hardcode model names** in `src/` or `backend/`. All model IDs come from
   `config/models.yaml` via `config/settings.py::get_settings()`.
2. **Jurisdiction isolation is mandatory.** Every chunk carries
   `jurisdiction ∈ {india, international}`. Retrieval MUST filter by jurisdiction;
   `dual` mode runs two isolated searches and reasons separately — never merges corpora.
3. **Strict citation grounding.** Every factual legal claim in an answer must carry an
   inline bracketed reference `[^N]` that maps 1:1 to a retrieved chunk id.
   Format for human-readable fallback: `[Source: <DocName>, Sec. <X>]`.
   Procedural answers cite EVERY step. `Generator.complete()` enforces this: a draft
   with zero refs triggers one same-model rewrite demanding per-step citations;
   persistent failure falls through to the (always-cited) extractive fallback.
4. **Zero extrapolation + empathy.** Greetings (no legal substance; app-name
   mentions ignored, context never consulted) get a warm time-aware reply.
   Multilingual (`language.py`, first pipeline step): Hindi/Hinglish detected by
   regex; a deterministic offline glossary bridge translates first (free, immune
   to LLM throttling); Groq translation only for low-coverage queries. Answers
    come back in the user's language via reply instruction; the scope verifier
    sees both original and translation so slips can't cause refusal.
    Devanagari output is enforced by a script-check retry in `Generator.complete`;
    explicit markers ("in hindi", "hindi mein") override the reply language even
    for English queries and are stripped before retrieval.
   Simple "what is X" questions with one dominant chunk answer directly AND
   briefly (3-5 sentences, 500-token cap); detail requests stay long-form.
   Typo fixer (`typo_fixer.py`) corrects legal-term
   typos transposition-aware ("tdkl" → TKDL) and always discloses the correction.
   Scope gate (`scope_gate.py`) runs before any retrieval: lexicon fast-path, LLM
   verifier on misses, out-of-scope queries get a scope refusal with zero citations.
   Vague-but-in-scope near-misses (top ≥ 0.50, overlap ≥ 0.1) get an empathetic
   clarification with "Yes, proceed" suggestions — never a bare wall; a confirming
   reply adopts the previous question and answers it best-effort grounded.
   Below the band, ABSTAIN with the canonical message. Zero lexical overlap with
   top < 0.80 also abstains (overlap veto).
5. **Disclaimer lives in the UI footer** ("Information only; not legal advice." in
   small print below the composer). NEVER prepend disclaimer headers to answer text.
6. **DPDP compliance.** Never persist raw user PII beyond the audit log's hashed
   contact field. `queries_audit` stores query text + jurisdiction + latency for audit;
   contact details on escalation are stored minimally and never echoed to other users.
7. **Async everywhere** on I/O paths (FastAPI routes, httpx clients). No blocking
   `requests`, no sync file reads inside request handlers.
8. **Chunking standard:** 400–800 tokens, 10% overlap, clause/section aware.
   Preserve `section_id`, `doc_type`, `source_file`. Never split inside a
   `Section <n>` header match.
9. **Embeddings are 384-d** (default `openrouter/openai/text-embedding-3-small`
   with `dimensions: 384`; free fallback `liquid/lfm-2.5-embedding-350m:free`
   caps inputs at 512 tokens — the embedder sub-splits + mean-pools for it).
   Store as JSON array in SQLite for Turso/libsql compatibility (no native vec extension required).
10. **SSE contract:** `POST /api/query` streams `text/event-stream` frames:
    `meta` → `token`* → `citations` → `done` (or `abstain`). Always JSON per frame.
11. **Agentic layer (additive).** `entities`/`edges` tables live alongside
    `document_chunks` (never altered). `src/core/agent.py` holds the tool registry
    (search_case_law, graph_expand, check_tkdl, check_abs), the ReAct loop, and
    QueryPlanner with sequential hop injection. `mode: auto|agentic|fast` selects
    the pipeline; guardrails, abstention and response shape are identical either way.

## API constraints
- Groq: `https://api.groq.com/openai/v1/chat/completions`, OpenAI-compatible body.
  Primary `qwen/qwen3.8-27b` (verified live), fallbacks from `models.yaml`.
  Retry fallbacks on 4xx model errors.
- OpenRouter embeddings: `https://openrouter.ai/api/v1/embeddings`, with
  `Authorization: Bearer <key>`, exponential backoff (5 retries), batch ≤ 32.
- CORS: `allow_origins=["*"]` for hackathon; restrict to frontend origin in production.

## What autonomous agents may / may not do
- MAY add PDFs under `raw_data/{national,international}/` and run `/api/ingest`.
- MAY add migrations under `db/migrations/` (must be idempotent `CREATE TABLE IF NOT EXISTS`).
- MUST NOT invent statutes, section numbers, or case holdings.
- MUST NOT lower `confidence_threshold` to force answers.
