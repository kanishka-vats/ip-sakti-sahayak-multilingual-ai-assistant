# API Spec

Base: `http://localhost:8000` · Frontend served at `/` · OpenAPI at `/docs`.

## POST /api/query — single-shot grounded answer
Req: `{query: string(2..4000), jurisdiction: "india"|"international"|"dual"|"auto" (default "auto" — server keyword-routes), top_k?: 1..20, context_query?: string, context_answer?: string}`
Terse follow-ups ("explain it") are expanded server-side with `context_query` for
retrieval, and the prior turn is passed to the generator as history.
Res `QueryResponse`: `{answer, citations[{index, chunk_id, act_name, section, doc_type, source_file, confidence, quote (≤320 chars, sentence-cut), source_label, verify_url, verify_label}], confidence, top_score, jurisdiction, abstained, abs_flag, tkdl_flag, model, clarification: bool, suggestions: string[]}`
Answers carry NO disclaimer header — the frontend shows a persistent small-print footer.
Legal-term typos are auto-corrected (disclosed inline). Vague-but-in-scope queries
receive an empathetic clarification (`clarification: true`) with quick-reply
`suggestions` (e.g. "Yes, proceed"); confirming adopts the prior question.

## POST /api/query/stream — SSE (primary UI path)
Same request body. `Content-Type: text/event-stream` frames:
- `event: meta` `{jurisdiction, model, confidence, disclaimer}`
- `event: token` `{delta}` × N (absent on abstention)
- `event: abstain` `{message}` (only when gated)
- `event: citations` `{citations[], abs_flag, tkdl_flag, escalation_hint}`
- `event: done` `{}`

Example:
```bash
curl -N -X POST localhost:8000/api/query/stream \
 -H 'Content-Type: application/json' \
 -d '{"query":"What is Sec 3(d) of the Patents Act?","jurisdiction":"india"}'
```

## POST /api/ingest — batch embed raw_data
Query param `limit_files` (default 200). Res: `{chunks_indexed, india, international, files_scanned, offline_embeddings}`.

## GET /api/models — active model config
Resolves `config/models.yaml`: `{llm{provider,name,temperature,max_tokens,fallbacks}, embedding{provider,name,dimension}, retrieval{top_k,confidence_threshold}}`.

## POST /api/escalate — human facilitator handoff
Req: `{query, jurisdiction, contact, reason, context{}}`. Res: `{escalation_id, summary}`. Logged to `escalations`.

## POST /api/feedback — answer usefulness rating
Req: `{session_id, query, rating: "up"|"down"}`. Res: `{feedback_id}`. Logged to `feedback`.

## GET /health
`{status, models, chunks{india, international}}`.

## Errors
- 422 validation (short query / bad jurisdiction).
- 500 only on DB failure; LLM/embedding failures degrade to offline mode, never 500.
