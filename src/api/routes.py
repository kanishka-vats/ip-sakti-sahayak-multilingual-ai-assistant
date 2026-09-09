"""Query, stream, ingest, models, escalation endpoints."""
from __future__ import annotations

import asyncio
import json
import re
import time

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from config.settings import get_settings
from src.api.schemas import (
    EscalateRequest,
    EscalateResponse,
    FeedbackRequest,
    FeedbackResponse,
    IngestResponse,
    QueryRequest,
    QueryResponse,
)
from src.core import guardrails
from src.core.agent import AgentExecutor, needs_agent, to_scored
from src.core.generator import Generator, build_messages
from src.core.retriever import Retriever
from src.core.vector_store import VectorStore
from src.services.abs_compliance import analyze_abs
from src.services.citation_links import clean_label, verify_link
from src.services.greetings import greeting_for, is_greeting, strip_appname
from src.services.jurisdiction_router import jurisdiction_frame, route_jurisdiction
from src.services.language import detect_language, keyword_bridge, split_reply_override, to_english
from src.services.scope_gate import OUT_OF_SCOPE_MESSAGE, check_scope, lexicon_hit
from src.services.tkdl_checker import check_tkdl
from src.services.typo_fixer import correct_typos, correction_note

router = APIRouter()

ESCALATION_TRIGGERS = (
    "infringement", "litigat", "opposition", "revocation", "compulsory licen",
    "sue", "court", "tribunal", "penalt", "criminal", "high-risk", "high risk",
)


# Terse follow-ups that only make sense with conversational context.
FOLLOWUP_PAT = re.compile(
    r"^(explain|elaborate|tell me more|more|why|how|details?|examples?|"
    r"what about|what do you mean|clarify|simplify|expand|go on|continue|"
    r"and then|so what|really\??).{0,80}$", re.IGNORECASE)


def _expand_followup(query: str, ctx_q: str, ctx_a: str) -> tuple[str, str]:
    """Return (retrieval_query, history_block).

    Terse follow-ups ('explain it') are expanded with the previous user query
    for retrieval; the prior turn is also passed to the generator as history.
    """
    q = query.strip()
    history = ""
    if ctx_q:
        history = f"Previous question: {ctx_q}"
        if ctx_a:
            history += f"\nPrevious answer (grounded summary): {ctx_a[:800]}"
    if ctx_q and (len(q) < 50 or FOLLOWUP_PAT.search(q)):
        retrieval_query = f"{ctx_q} — {q}. Explain in full detail."
    else:
        retrieval_query = q
    return retrieval_query, history


# Confirmation replies that accept the assistant's stated interpretation.
CONFIRM_PAT = re.compile(
    r"^(yes|yeah|yep|yup|correct|right|proceed|go ahead|sure|ok|okay|confirm|"
    r"please proceed|do it|haan|han|ji haan|aage badho|jaari rakho)"
    r"[\s,!.]*(proceed|please|go ahead|do it|continue)?[\s,!.]*$",
    re.IGNORECASE)

# Near-miss band: gate failed but retrieval is close enough that a
# clarification (not a wall) is the empathetic response.
CLARIFY_FLOOR = 0.50


def _template_clarification(query: str, citations: list[dict]) -> tuple[str, list[str]]:
    """Deterministic clarification when the LLM clarifier is unusable."""
    docs = [f"{c['act_name']} — {c['section']}" for c in citations[:3]]
    guess = f"this relates to {docs[0]}" + (f" and {docs[1]}" if len(docs) > 1 else "") \
        if docs else "this is about IP/regulatory compliance"
    lines = [
        "To point you to the right provisions, may I know in what context you're asking?",
        "",
        f"My best reading is: {guess}.",
        "",
    ]
    if docs:
        lines.append("The closest provisions I found:")
        lines += [f"- {d}" for d in docs]
        lines.append("")
    lines.append("If that's right, reply **Yes, proceed** and I'll lay out the full "
                 "answer — or tell me which track you mean.")
    return "\n".join(lines), ["Yes, proceed"]


def _needs_escalation(query: str, abs_flag, tkdl_flag, confidence: float) -> str | None:
    q = query.lower()
    for t in ESCALATION_TRIGGERS:
        if t in q:
            return f"High-risk litigation cue detected ({t}); recommend IP facilitator review."
    if abs_flag and abs_flag.get("risk") == "high":
        return "High-risk ABS/IPR overlap (foreign access + patent filing); facilitator review advised."
    if tkdl_flag and tkdl_flag.get("novelty_risk"):
        return "TKDL prior-art novelty risk; facilitator should vet claims before filing."
    if confidence < 0.5:
        return "Low retrieval confidence; human verification recommended."
    return None


async def _answer_agentic(req: QueryRequest, query: str, ctx_q: str,
                        ctx_a: str, jurisdiction: str, correction_md: str,
                        t0: float, reply_lang: str = "en") -> dict:
    """Agentic path: planner + tools + synthesis, same guardrails as fast.

    Citations, abstention message, confidence math and response shape are
    identical to the sequential pipeline; only the reasoning is agentic.
    """
    from src.services.abs_compliance import analyze_abs
    from src.services.tkdl_checker import check_tkdl

    s = get_settings()
    history = f"Previous question: {ctx_q}\nPrevious answer: {ctx_a[:800]}" if ctx_q else ""
    try:
        res = await AgentExecutor().run(query, jurisdiction, history,
                                          reply_lang=reply_lang)
    except Exception as exc:
        # Agent stack must never 500 a query: fall back to abstention.
        print(f"[agent] executor failed: {exc}")
        res = None
    store = VectorStore(s.db_path_abs)
    store.init()
    latency_ms = (time.perf_counter() - t0) * 1000
    if res is None or not res.citations:
        store.log_query(req.query, jurisdiction, s.llm_model, 0.0, 0.0,
                        True, latency_ms)
        return {
            "answer": correction_md + s.abstention_message,
            "citations": [], "confidence": 0.0,
            "top_score": 0.0, "jurisdiction": jurisdiction,
            "abstained": True, "abs_flag": analyze_abs(query),
            "tkdl_flag": check_tkdl(query), "model": s.llm_model,
            "escalation_hint": "Agentic run found no citable basis.",
            "suggestions": [], "clarification": False,
        }
    scored = to_scored(res.citations)
    gate = guardrails.gate(query, scored)
    citations = []
    for i, c in enumerate(res.citations, 1):
        d = dict(c)
        d["index"] = i
        citations.append(d)
    if gate.abstain or res.abstained:
        store.log_query(req.query, jurisdiction, s.llm_model, gate.top_score,
                        gate.confidence, True, latency_ms)
        return {
            "answer": correction_md + s.abstention_message,
            "citations": citations, "confidence": gate.confidence,
            "top_score": gate.top_score, "jurisdiction": jurisdiction,
            "abstained": True,
            "abs_flag": res.abs_flag or analyze_abs(query),
            "tkdl_flag": res.tkdl_flag or check_tkdl(query),
            "model": s.llm_model,
            "escalation_hint": "Agentic run found no citable basis.",
            "suggestions": [], "clarification": False,
        }
    answer = guardrails.scrub_pii(res.answer)
    if guardrails.verify_citations(answer, len(citations)) or not answer.strip():
        # Final citation-compliance net: never ship uncited agent prose.
        gen = Generator()
        answer = gen._offline_answer(query, [{
            "act_name": c.get("act_name", ""), "section": c.get("section", ""),
            "source_file": c.get("source_file", ""),
            "text": c.get("excerpt") or c.get("quote") or "",
        } for c in citations[:5]])
    store.log_query(req.query, jurisdiction, s.llm_model, gate.top_score,
                    gate.confidence, False, latency_ms)
    return {
        "answer": correction_md + answer, "citations": citations,
        "confidence": gate.confidence, "top_score": gate.top_score,
        "jurisdiction": jurisdiction, "abstained": False,
        "abs_flag": res.abs_flag or analyze_abs(query),
        "tkdl_flag": res.tkdl_flag or check_tkdl(query),
        "model": s.llm_model,
        "escalation_hint": _needs_escalation(req.query, res.abs_flag,
                                             res.tkdl_flag, gate.confidence),
        "suggestions": [], "clarification": False,
    }


async def _answer_pipeline(req: QueryRequest) -> dict:
    s = get_settings()
    t0 = time.perf_counter()
    ctx_q = (req.context_query or "").strip()
    # -2. Multilingual layer: detect Hindi/Hinglish once, translate for the
    # whole pipeline, answer back in the user's language. English costs nothing.
    # An explicit marker ("in hindi", "hindi mein") overrides the reply language
    # even for English queries, and is stripped before retrieval.
    query_nolang, override = split_reply_override(req.query)
    lang = detect_language(query_nolang)
    work_query = query_nolang
    if lang in ("hi", "hinglish"):
        # Glossary bridge first: free, deterministic, retrieval-optimal, and
        # immune to LLM throttling. Groq translation only for hard cases.
        bridged, coverage = keyword_bridge(query_nolang)
        if coverage >= 0.5:
            work_query = bridged
        else:
            translated = await to_english(query_nolang, lang)
            if translated:
                work_query = translated
    reply_lang = override or lang
    # -1. Pure greetings get warmth + a name, never the scope refusal.
    # App-name mentions don't count as substance ("hello ip-sakti"), and a
    # greeting stays a greeting regardless of conversation context.
    bare = strip_appname(work_query)
    if is_greeting(bare) and not lexicon_hit(bare, ""):
        latency_ms = (time.perf_counter() - t0) * 1000
        store = VectorStore(s.db_path_abs)
        store.init()
        store.log_query(req.query, req.jurisdiction, s.llm_model, 1.0, 1.0,
                        False, latency_ms)
        return {
            "answer": greeting_for(req.username, lang=reply_lang),
            "citations": [], "confidence": 1.0,
            "top_score": 1.0, "jurisdiction": req.jurisdiction,
            "abstained": False, "abs_flag": None, "tkdl_flag": None,
            "model": s.llm_model,
            "escalation_hint": None,
            "suggestions": ["What is TKDL?",
                            "What does Section 3(d) of the Patents Act bar?"],
            "clarification": False,
        }
    # 0. Typo empathy: correct legal-term typos ("tdkl" -> "TKDL") for all
    # downstream steps; the correction is always disclosed in the answer.
    fixed_query, corrections = correct_typos(work_query.strip())
    correction_md = correction_note(corrections)
    # A confirmation ("yes, proceed") adopts the previous question wholesale:
    # retrieval, routing and flags all run on it, not on the two-word reply.
    is_confirm = bool(ctx_q) and bool(CONFIRM_PAT.match(work_query.strip()))
    q_eff = ctx_q if is_confirm else fixed_query
    # 1. Scope gate on the effective query: out-of-domain intent (memes,
    # trivia, chit-chat) never spends embedding/retrieval budget.
    in_scope, scope_reason = await check_scope(
        q_eff, "" if is_confirm else ctx_q,
        original=req.query if work_query != req.query else None)
    if not in_scope:
        latency_ms = (time.perf_counter() - t0) * 1000
        store = VectorStore(s.db_path_abs)
        store.init()
        store.log_query(req.query, req.jurisdiction, s.llm_model, 0.0, 0.0,
                        True, latency_ms)
        return {
            "answer": OUT_OF_SCOPE_MESSAGE,
            "citations": [], "confidence": 0.0,
            "top_score": 0.0, "jurisdiction": req.jurisdiction,
            "abstained": True, "abs_flag": None, "tkdl_flag": None,
            "model": s.llm_model,
            "escalation_hint": None,
            "suggestions": [], "clarification": False,
        }
    jurisdiction = route_jurisdiction(q_eff, req.jurisdiction)
    # Abstraction layer (Phase 3): complex queries go agentic, everything
    # else stays on the untouched fast sequential pipeline below.
    use_agent = req.mode == "agentic" or (
        req.mode == "auto" and needs_agent(fixed_query, ctx_q))
    if use_agent:
        return await _answer_agentic(req, q_eff, ctx_q,
                                     (req.context_answer or "").strip(),
                                     jurisdiction, correction_md, t0,
                                     reply_lang=reply_lang)
    if is_confirm:
        retrieval_query = (f"{ctx_q}. Provide a full detailed answer covering "
                           "all relevant provisions.")
        history = (f"Previous question: {ctx_q}\nPrevious answer (grounded summary): "
                   f"{(req.context_answer or '').strip()[:800]}")
    else:
        retrieval_query, history = _expand_followup(
            fixed_query, ctx_q, (req.context_answer or "").strip())
    store = VectorStore(s.db_path_abs)
    store.init()
    retriever = Retriever(store=store)
    results = await retriever.retrieve(retrieval_query, jurisdiction, top_k=req.top_k)

    # Flatten per-jurisdiction excerpts, india first
    ordered_modes = [m for m in ("india", "international") if m in results]
    all_chunks = [c for m in ordered_modes for c in results[m].chunks]
    top_score = max((results[m].top_score for m in ordered_modes), default=0.0)

    abs_flag = analyze_abs(q_eff)
    tkdl_flag = check_tkdl(q_eff)

    gate = guardrails.gate(retrieval_query, all_chunks)
    overlap = guardrails.token_overlap(retrieval_query, all_chunks)
    latency_ms = (time.perf_counter() - t0) * 1000

    citations = []
    idx = 1
    for m in ordered_modes:
        for c in results[m].chunks:
            p = guardrails.citation_payload(c)
            vurl, vlabel = verify_link(p["act_name"], m)
            citations.append({
                "index": idx, "chunk_id": p["chunk_id"], "act_name": p["act_name"],
                "section": p["section"], "doc_type": p["doc_type"],
                "source_file": p["source_file"], "confidence": p["confidence"],
                "quote": p["quote"], "excerpt": p["excerpt"], "jurisdiction": m,
                "source_label": clean_label(p["act_name"], p["source_file"]),
                "verify_url": vurl, "verify_label": vlabel,
            })
            idx += 1

    def base(extra: dict) -> dict:
        d = {
            "answer": "", "citations": citations, "confidence": gate.confidence,
            "top_score": top_score, "jurisdiction": jurisdiction,
            "abstained": True, "abs_flag": abs_flag, "tkdl_flag": tkdl_flag,
            "model": s.llm_model, "escalation_hint": None,
            "suggestions": [], "clarification": False,
        }
        d.update(extra)
        return d

    gen = Generator()
    if gate.abstain:
        near_miss = top_score >= CLARIFY_FLOOR and overlap >= 0.1
        if is_confirm and citations:
            # User confirmed the interpretation: ALWAYS answer best-effort from
            # the retrieved provisions (zero extrapolation) — never loop back
            # into another clarification once they said yes.
            answer = (
                "Proceeding with my best reading of your question — "
                "tell me which track you mean if I got it wrong.\n\n"
                + gen._offline_answer(ctx_q or fixed_query, [{
                    "act_name": c["act_name"], "section": c["section"],
                    "source_file": c["source_file"], "text": c["excerpt"],
                } for c in citations])
            )
            store.log_query(req.query, jurisdiction, s.llm_model, top_score,
                            gate.confidence, False, latency_ms)
            return base({"answer": correction_md + answer, "abstained": False,
                         "escalation_hint": _needs_escalation(
                             req.query, abs_flag, tkdl_flag, gate.confidence)})
        if near_miss:
            # Vague-but-in-scope: empathetic clarification, not a wall.
            sketches = [f"{c['act_name']} — {c['section']}: {c['quote'][:220]}"
                        for c in citations[:5]]
            clar = await gen.clarify(fixed_query, sketches, reply_lang=reply_lang)
            if not clar:
                clar, _ = _template_clarification(fixed_query, citations)
            store.log_query(req.query, jurisdiction, s.llm_model, top_score,
                            gate.confidence, True, latency_ms)
            return base({"answer": correction_md + clar,
                         "clarification": True, "suggestions": ["Yes, proceed"],
                         "escalation_hint": _needs_escalation(
                             req.query, abs_flag, tkdl_flag, gate.confidence)})
        store.log_query(req.query, jurisdiction, s.llm_model, top_score,
                        gate.confidence, True, latency_ms)
        return base({
            "answer": correction_md + s.abstention_message,
            "escalation_hint": _needs_escalation(req.query, abs_flag, tkdl_flag, gate.confidence),
        })

    excerpts = [{
        "act_name": c["act_name"], "section": c["section"],
        "source_file": c["source_file"], "text": c["excerpt"],
    } for c in citations]
    frame = jurisdiction_frame(jurisdiction)
    display_query = q_eff if retrieval_query.startswith(q_eff) else (
        f"{q_eff} (context: {ctx_q})")
    full_query = f"{frame}\n{display_query}"
    answer = await gen.complete(full_query, excerpts, jurisdiction,
                                history=history, reply_lang=reply_lang,
                                brief=guardrails.is_brief_query(fixed_query))
    answer = guardrails.scrub_pii(answer)
    if "INSUFFICIENT_BASIS" in answer:
        if not gate.abstain:
            # Gate passed (grounding exists) but the model vetoed: fall back
            # to the extractive summary so verified excerpts still reach the
            # user instead of a bare abstention. Zero extrapolation either way.
            answer = gen._offline_answer(q_eff, excerpts)
        else:
            store.log_query(req.query, jurisdiction, s.llm_model, top_score,
                            gate.confidence, True, latency_ms)
            return base({
                "answer": correction_md + s.abstention_message,
                "escalation_hint": "Model found no citable basis; facilitator review advised.",
            })
    # NOTE: no disclaimer header in answers — the UI shows a persistent
    # small-print footer instead (user requirement; DPDP note lives there).
    if not answer.strip():
        answer = gen._offline_answer(fixed_query, excerpts)
    store.log_query(req.query, jurisdiction, s.llm_model, top_score,
                    gate.confidence, False, latency_ms)
    return base({
        "answer": correction_md + answer, "abstained": False,
        "escalation_hint": _needs_escalation(req.query, abs_flag, tkdl_flag, gate.confidence),
    })


@router.post("/query", response_model=QueryResponse)
async def post_query(req: QueryRequest):
    data = await _answer_pipeline(req)
    return QueryResponse(
        answer=data["answer"],
        citations=[{k: v for k, v in c.items() if k not in ("jurisdiction", "excerpt")} for c in data["citations"]],
        confidence=data["confidence"], top_score=data["top_score"],
        jurisdiction=data["jurisdiction"], abstained=data["abstained"],
        abs_flag=data["abs_flag"], tkdl_flag=data["tkdl_flag"], model=data["model"],
        clarification=data.get("clarification", False),
        suggestions=data.get("suggestions", []),
    )


@router.post("/query/stream")
async def stream_query(req: QueryRequest):
    """SSE: meta -> token* -> citations -> done (or abstain)."""
    s = get_settings()

    async def event_gen():
        data = await _answer_pipeline(req)
        for c in data["citations"]:
            c.pop("excerpt", None)
        yield f"event: meta\ndata: {json.dumps({'jurisdiction': data['jurisdiction'], 'model': data['model'], 'confidence': data['confidence'], 'disclaimer': s.disclaimer})}\n\n"
        if data["abstained"]:
            yield f"event: abstain\ndata: {json.dumps({'message': data['answer']})}\n\n"
        else:
            # Re-stream token deltas for typing effect (grounded answer already generated)
            text = data["answer"]
            for i in range(0, len(text), 60):
                yield f"event: token\ndata: {json.dumps({'delta': text[i:i+60]})}\n\n"
                await asyncio.sleep(0)
        yield f"event: citations\ndata: {json.dumps({'citations': data['citations'], 'abs_flag': data['abs_flag'], 'tkdl_flag': data['tkdl_flag'], 'escalation_hint': data.get('escalation_hint'), 'clarification': data.get('clarification', False), 'suggestions': data.get('suggestions', [])})}\n\n"
        yield "event: done\ndata: {}\n\n"

    return StreamingResponse(event_gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/ingest", response_model=IngestResponse)
async def post_ingest(limit_files: int = 200):
    from pathlib import Path
    from src.ingestion.chunker import chunk_documents
    from src.ingestion.embedder import Embedder
    from src.ingestion.parser import scan_corpus

    s = get_settings()
    store = VectorStore(s.db_path_abs)
    store.init()
    docs = scan_corpus()
    # cap per-file sections to bound runtime
    docs = docs[: max(1, limit_files * 4)]
    chunks = chunk_documents(docs)
    embedder = Embedder()
    texts = [c.text for c in chunks]
    vecs = await embedder.embed(texts)
    rows = [{
        "jurisdiction": c.jurisdiction, "source_file": c.source_file,
        "doc_name": c.doc_name, "doc_type": c.doc_type,
        "section_id": c.section_id, "chunk_index": c.chunk_index,
        "text": c.text, "embedding": v,
    } for c, v in zip(chunks, vecs)]
    store.insert_chunks(rows)
    return IngestResponse(
        chunks_indexed=len(rows),
        india=store.count("india"), international=store.count("international"),
        files_scanned=len({d.source_file for d in docs}),
        offline_embeddings=embedder.offline,
    )


@router.get("/models")
async def get_models():
    return get_settings().model_dump_public()


@router.post("/escalate", response_model=EscalateResponse)
async def post_escalate(req: EscalateRequest):
    s = get_settings()
    store = VectorStore(s.db_path_abs)
    store.init()
    abs_flag = analyze_abs(req.query)
    tkdl_flag = check_tkdl(req.query)
    summary = (
        f"Escalation [{req.jurisdiction}] — {req.reason[:300]}\n"
        f"Query: {req.query[:500]}\n"
        f"ABS: {(abs_flag or {}).get('risk', 'n/a')} | "
        f"TKDL: {((tkdl_flag or {}).get('matched_terms') or 'none')}"
    )
    eid = store.log_escalation(req.query, req.jurisdiction, req.contact,
                               req.reason, {"context": req.context,
                                            "abs": abs_flag, "tkdl": tkdl_flag})
    return EscalateResponse(escalation_id=eid, summary=summary)


@router.post("/feedback", response_model=FeedbackResponse)
async def post_feedback(req: FeedbackRequest):
    s = get_settings()
    store = VectorStore(s.db_path_abs)
    store.init()
    fid = store.log_feedback(req.session_id, req.query, req.rating, s.llm_model)
    return FeedbackResponse(feedback_id=fid)

