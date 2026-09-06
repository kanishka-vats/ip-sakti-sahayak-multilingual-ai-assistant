# Report Draft — SIH 26045 Hackathon Evaluation

## 1. Problem statement
Ayurveda innovators, MSMEs and students face a fragmented IP maze: Indian statutes
(Patents Act Sec 3(d)/3(p), BD Act ABS), IPO manuals, AYUSH regulations — versus PCT/TRIPS/
WIPO treaties. Generic chatbots hallucinate section numbers and ignore ABS/TKDL traps,
risking invalid filings and biopiracy exposure.

## 2. Solution: IP-SAKTI Sahayak
Split-jurisdiction RAG: India / International / Dual-split panels, every claim bound to
`[^N]` citations with act + section + verbatim quote + confidence gauge. ABS auto-flags
NBA Sec 3/4/6 obligations; TKDL pointer warns on traditional-formulation novelty risk;
deterministic abstention below 0.65 similarity (calibrated: relevant 0.81+, unrelated ~0.53); one-click escalation to human IP facilitator.

## 3. Innovation index
- **Jurisdiction isolation** (dual = two sealed searches + comparison table, never blended).
- **Zero-extrapolation guardrails** with density+overlap confidence scoring.
- **ABS/TKDL domain modules** — no generic RAG has these.
- **Decoupled model orchestration** (`models.yaml` swap without code changes).
- **Turso-portable vector layer** (JSON embeddings, no native extension lock-in).
- **SSE streaming + citation pills + confidence gauges** Perplexity-grade UX in vanilla HTML.

## 4. System pipeline (summary)
PDFs → section-aware parse → clause chunks (400–800 tok, 10% overlap) → OpenRouter 384-d
embeddings → Turso `document_chunks` → hybrid dense+keyword retrieval (jurisdiction-sealed)
→ guardrail gate → Groq Qwen strict-citation generation → SSE to obsidian UI.

## 5. DPDP compliance framework
- Mandatory disclaimer header on every answer.
- PII scrub (Aadhaar/PAN/phone/email) on both retrieval display and generation.
- `queries_audit` (query text + scores + latency, no PII); `escalations` contact minimal + modal consent.
- No training on user data; no cross-user reads.

## 6. Reproduction (uv)
```bash
uv sync
uv run uvicorn src.api.main:app --port 8000
# open http://localhost:8000, click "Ingest raw_data", then query.
```

## 7. Benchmarks (to fill after ingest)
| Query set | Grounded % | Abstain % | Avg conf | Latency p50 |
|---|---|---|---|---|
| India patents (20) | — | — | — | — |
| ABS/TKDL (15) | — | — | — | — |
| PCT/TRIPS (15) | — | — | — | — |

## 8. Risks & mitigations
Offline hash embeddings (no key) → lower recall; mitigated by hybrid keyword re-rank.
Groq rate limits → fallback models + extractive mode. Large PDFs → section chunking
bounds memory. Turso cloud optional; local SQLite file is the default single-node store.

## 9. Roadmap
Multilingual queries (Hindi/Sanskrit transliteration), evaluator harness with citation
precision/recall, sqlite-vec acceleration, facilitator dashboard over `escalations`.
