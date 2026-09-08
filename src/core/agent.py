"""Agentic orchestration: tool registry, ReAct loop, planner (Phases 2+3).

Design (additive — the sequential pipeline in routes.py is untouched):
  - Tools wrap EXISTING logic: chunk retriever, TKDL_INDEX lookup, ABS
    specialist, and the Phase-1 graph search. No logic is duplicated.
  - QueryPlanner breaks complex questions into ordered tool steps.
  - AgentExecutor runs steps sequentially, injecting each hop's findings
    into the next hop, then synthesizes a strictly-cited answer by reusing
    Generator.complete (fallback chain, citation retry, offline mode).
  - Final citation validation guarantees [^N] range-correctness; failures
    degrade to the extractive fallback, never to uncited prose.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine

import httpx

from config.settings import get_settings
from src.core import guardrails
from src.core.generator import Generator, strip_thinking
from src.core.retriever import Retriever
from src.services.abs_compliance import analyze_abs
from src.services.tkdl_checker import check_tkdl

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
MAX_REACT_STEPS = 5
MAX_PLAN_STEPS = 4


# ---------------------------------------------------------------- tools ---

@dataclass
class Tool:
    name: str
    description: str
    args_help: str
    func: Callable[..., Coroutine[Any, Any, dict]]


def _payload_chunks(chunks) -> list[dict]:
    return [guardrails.citation_payload(c) for c in chunks]


def to_scored(payloads: list[dict]) -> list:
    """Rebuild ScoredChunk list from internal payload dicts (guardrail input)."""
    from src.core.vector_store import ScoredChunk
    out = []
    for p in payloads:
        try:
            out.append(ScoredChunk(
                id=int(p.get("chunk_id", 0)),
                score=float(p.get("confidence", 0.0)),
                jurisdiction=p.get("jurisdiction", "india"),
                source_file=p.get("source_file", ""),
                doc_name=p.get("act_name", ""),
                doc_type=p.get("doc_type", ""),
                section_id=p.get("section", ""),
                chunk_index=0,
                text=p.get("excerpt") or p.get("quote") or ""))
        except Exception:
            continue
    return out


COMPARE_PAT = re.compile(
    r"\bcompar|versus|\bvs\b|difference between|both\b.*\band\b|"
    r"india.*international|international.*india|chapter i.*chapter ii",
    re.IGNORECASE)
DOMAIN_PATS = [
    re.compile(r"neem|turmeric|ashwagandha|triphala|herb|tkdl|traditional knowledge", re.IGNORECASE),
    re.compile(r"foreign|abroad|\bpct\b|ipr\b|filing|nba\b|abs\b|approval", re.IGNORECASE),
    re.compile(r"patent|trademark|copyright|design|infring|opposition|sc 3\(d\)|section 3", re.IGNORECASE),
    re.compile(r"trips|wipo|treaty|pct\b.*procedure|national phase", re.IGNORECASE),
]


def needs_agent(query: str, context_query: str = "") -> bool:
    """Deterministic complexity gate: multi-faceted questions go agentic.

    Fires when the (query + context) touches 2+ distinct legal domains, uses
    comparison language, or is a long multi-clause legal question.
    Conservative by design — everything else stays on the fast pipeline.
    """
    text = f"{context_query} {query}"
    domains = sum(1 for pat in DOMAIN_PATS if pat.search(text))
    if domains >= 2:
        return True
    if COMPARE_PAT.search(text):
        return True
    if len(text) > 220 and sum(1 for pat in DOMAIN_PATS if pat.search(text)) >= 1 \
            and len(re.findall(r"\band\b|\;|\?", text)) >= 2:
        return True
    return False


async def tool_search_case_law(query: str, jurisdiction: str = "dual",
                               top_k: int = 5, context: str = "",
                               retriever: Retriever | None = None) -> dict:
    """Existing hybrid chunk retriever (statutes, rules, treaties)."""
    ret = retriever or Retriever()
    eff = f"{query} {context[:300]}".strip() if context else query
    results = await ret.retrieve(eff, jurisdiction, top_k=top_k)
    order = [m for m in ("india", "international") if m in results]
    chunks = [c for m in order for c in results[m].chunks]
    payloads = _payload_chunks(chunks)
    for p, c in zip(payloads, chunks):
        p["jurisdiction"] = c.jurisdiction
    lines = [f"[{c.doc_name} — {c.section_id}] {c.text[:220]}" for c in chunks[:6]]
    return {"summary": "\n".join(lines) or "No provisions retrieved.",
            "chunks": payloads,
            "top_score": max((c.score for c in chunks), default=0.0)}


async def tool_graph_expand(query: str, jurisdiction: str = "dual",
                            retriever: Retriever | None = None) -> dict:
    """Phase-1 graph: seed entities -> 1-hop relations -> linked chunks."""
    ret = retriever or Retriever()
    try:
        g = await ret.graph_search(query, jurisdiction)
    except Exception as exc:
        return {"summary": f"Graph unavailable: {exc}", "chunks": [],
                "statements": []}
    order = [m for m in ("india", "international") if m in g]
    statements: list[str] = []
    chunks = []
    for m in order:
        statements.extend(g[m].statements)
        for c in g[m].chunks:
            d = guardrails.citation_payload(c)
            d["jurisdiction"] = m
            chunks.append(d)
    if not statements:
        return {"summary": "Knowledge graph is empty for this query; "
                           "use flat provisions instead.",
                "chunks": [], "statements": []}
    return {"summary": "Graph facts:\n" + "\n".join(f"- {s}" for s in statements[:12]),
            "chunks": _payload_chunks(chunks), "statements": statements[:12]}


async def tool_check_tkdl(query: str) -> dict:
    """Existing TKDL_INDEX heuristic lookup."""
    hit = check_tkdl(query)
    if not hit:
        return {"summary": "No TKDL-relevant terms detected.", "matched": []}
    return {"summary": "TKDL prior-art pointer: "
                       + "; ".join(hit["prior_art_notes"][:6])
                       + f" Caution: {hit['caution']}",
            "matched": hit["matched_terms"],
            "novelty_risk": hit["novelty_risk"]}


async def tool_check_abs(query: str) -> dict:
    """Existing ABS (Biological Diversity Act) specialist logic."""
    hit = analyze_abs(query)
    if not hit:
        return {"summary": "No ABS-relevant biological-resource use detected.",
                "risk": "none"}
    return {"summary": f"ABS obligations (risk={hit['risk']}): "
                       + " | ".join(hit["obligations"])
                       + f" Authority: {hit['authority']}",
            "risk": hit["risk"], "obligations": hit["obligations"]}


def build_registry(retriever: Retriever | None = None) -> dict[str, Tool]:
    async def _search(query: str, jurisdiction: str = "dual",
                      context: str = "") -> dict:
        return await tool_search_case_law(query, jurisdiction, 5, context,
                                          retriever)
    async def _graph(query: str, jurisdiction: str = "dual") -> dict:
        return await tool_graph_expand(query, jurisdiction, retriever)
    return {
        "search_case_law": Tool(
            "search_case_law",
            "Search statutes, rules and treaties (flat hybrid retrieval).",
            '{"query": str, "jurisdiction": "india|international|dual"}', _search),
        "graph_expand": Tool(
            "graph_expand",
            "Traverse the knowledge graph for linked entities/relations.",
            '{"query": str, "jurisdiction": "india|international|dual"}', _graph),
        "check_tkdl": Tool(
            "check_tkdl",
            "Screen for traditional-knowledge prior art (TKDL pointer).",
            '{"query": str}', lambda query, **kw: tool_check_tkdl(query)),
        "check_abs": Tool(
            "check_abs",
            "Assess Biological Diversity Act ABS obligations (NBA Sec 3/4/6).",
            '{"query": str}', lambda query, **kw: tool_check_abs(query)),
    }


# ------------------------------------------------------------ planner ---

@dataclass
class PlanStep:
    tool: str
    args: dict[str, Any]
    goal: str


PLANNER_PROMPT = """You are a query planner for a legal/IP research agent. Tools:
{tools}
Break the question into an ordered list of tool calls (1-4 steps). Later steps
may depend on earlier findings. Return STRICT JSON only:
{{"steps": [{{"tool": "<name>", "args": {{...}}, "goal": "<one line>"}}]}}
Example — "Can I patent a neem extract with a foreign partner?":
{{"steps": [{{"tool": "check_tkdl", "args": {{"query": "neem extract patent"}},
"goal": "TKDL novelty risk"}}, {{"tool": "check_abs",
"args": {{"query": "neem extract foreign partner patent"}},
"goal": "NBA approval duties"}}, {{"tool": "search_case_law",
"args": {{"query": "patent biological resource foreign applicant approval",
"jurisdiction": "india"}}, "goal": "Statutory provisions"}}]}}
Always pass jurisdiction "india", "international" or "dual" to search tools."""


def _tool_spec(registry: dict[str, Tool]) -> str:
    return "\n".join(f"- {t.name}{t.args_help}: {t.description}"
                     for t in registry.values())


async def _agent_chat(messages: list[dict[str, str]],
                      max_tokens: int = 400) -> str | None:
    """Single Groq chat with primary+fallback models; None on total failure.
    Default cap is small — planner and ReAct turns are JSON, not prose."""
    s = get_settings()
    async with httpx.AsyncClient() as client:
        for m in [s.llm_model, *s.llm_fallbacks]:
            try:
                resp = await client.post(
                    GROQ_URL,
                    headers={"Authorization": f"Bearer {s.groq_key}",
                             "Content-Type": "application/json"},
                    json={"model": m, "messages": messages, "temperature": 0.1,
                          "max_tokens": max_tokens},
                    timeout=90.0)
                if resp.status_code in (400, 404) and "model" in resp.text.lower():
                    continue
                resp.raise_for_status()
                text = resp.json()["choices"][0]["message"]["content"] or ""
                clean = strip_thinking(text)
                if clean:
                    return clean
            except Exception:
                continue
    return None


def _parse_json_obj(text: str) -> dict | None:
    try:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            return None
        obj = json.loads(text[start:end + 1])
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


class QueryPlanner:
    """LLM planner with a deterministic single-search fallback."""

    def __init__(self, registry: dict[str, Tool] | None = None):
        self.registry = registry or build_registry()

    async def plan(self, query: str, jurisdiction: str) -> list[PlanStep]:
        raw = await _agent_chat([
            {"role": "system",
             "content": "Output strict JSON only. No prose, no thinking traces.\n/no_think"},
            {"role": "user", "content": PLANNER_PROMPT.format(
                tools=_tool_spec(self.registry))
             + f"\nQuestion: {query}\nJurisdiction: {jurisdiction}"}])
        steps: list[PlanStep] = []
        if raw:
            obj = _parse_json_obj(raw)
            for st in (obj or {}).get("steps", [])[:MAX_PLAN_STEPS]:
                if isinstance(st, dict) and st.get("tool") in self.registry:
                    args = st.get("args", {})
                    steps.append(PlanStep(
                        tool=st["tool"],
                        args=args if isinstance(args, dict) else {},
                        goal=str(st.get("goal", ""))[:200]))
        if not steps:
            steps = [PlanStep(tool="search_case_law",
                              args={"query": query, "jurisdiction": jurisdiction},
                              goal="Direct provision lookup (planner fallback)")]
        # normalize jurisdiction on search tools
        for st in steps:
            if st.tool in ("search_case_law", "graph_expand"):
                st.args.setdefault("jurisdiction", jurisdiction)
                if st.args.get("jurisdiction") not in (
                        "india", "international", "dual"):
                    st.args["jurisdiction"] = jurisdiction
        return steps


# ----------------------------------------------------------- executor ---

REACT_PROMPT = """You are a legal/IP research agent. Tools:
{tools}
Per turn output EXACTLY one JSON object, no prose:
{{"thought": "<brief>", "action": {{"tool": "<name>", "args": {{...}}}}}}
or, when you have enough evidence:
{{"thought": "<brief>", "answer": "<final answer WITHOUT citations; they are added later>"}}
Rules: at most {max_steps} tool calls; pass jurisdiction through; never invent
statutes; if evidence is insufficient, answer "INSUFFICIENT_BASIS"."""


@dataclass
class AgentResult:
    answer: str
    citations: list[dict]  # internal citation-payload dicts (jurisdiction+excerpt kept)
    top_score: float
    abstained: bool
    abs_flag: dict | None = None
    tkdl_flag: dict | None = None
    trace: list[dict] = field(default_factory=list)


class AgentExecutor:
    def __init__(self, registry: dict[str, Tool] | None = None,
                 retriever: Retriever | None = None):
        self.registry = registry or build_registry(retriever)
        self.retriever = retriever or Retriever()
        self.generator = Generator()
        self.trace: list[dict] = []

    def _collect(self, out: dict, chunk_ids: set[int],
                 chunks: list[dict]) -> None:
        for c in out.get("chunks", []):
            if isinstance(c, dict) and c.get("chunk_id") not in chunk_ids:
                chunk_ids.add(c["chunk_id"])
                chunks.append(c)

    async def _run_tool(self, name: str, args: dict) -> dict:
        tool = self.registry.get(name)
        if tool is None:
            return {"summary": f"Unknown tool '{name}'. "
                               f"Use one of: {sorted(self.registry)}.",
                    "chunks": []}
        try:
            clean = {k: v for k, v in args.items() if not k.startswith("_")}
            out = await tool.func(**clean)
            return out if isinstance(out, dict) else {"summary": str(out),
                                                     "chunks": []}
        except Exception as exc:
            return {"summary": f"Tool {name} failed: {exc}", "chunks": []}

    async def _synthesize(self, query: str, jurisdiction: str, history: str,
                          chunk_ids: set[int], chunks: list[dict],
                          reply_lang: str = "en") -> str:
        if not chunks:
            return "INSUFFICIENT_BASIS"
        excerpts = [{
            "act_name": c.get("act_name", ""), "section": c.get("section", ""),
            "source_file": c.get("source_file", ""),
            "text": (c.get("excerpt") or c.get("quote") or "")[:1500],
        } for c in chunks[:8]]
        return await self.generator.complete(query, excerpts, jurisdiction,
                                             history=history,
                                             reply_lang=reply_lang)

    # -- strict sequential plan: hop N findings injected into hop N+1 ------

    async def run_planned(self, query: str, jurisdiction: str, history: str,
                          steps: list[PlanStep], reply_lang: str = "en") -> AgentResult:
        chunk_ids: set[int] = set()
        chunks: list[dict] = []
        trail: list[str] = []
        top = 0.0
        abs_flag = tkdl_flag = None
        for i, step in enumerate(steps, 1):
            args = dict(step.args)
            if i > 1 and step.tool in ("search_case_law", "graph_expand"):
                # Hop injection: prior findings steer this hop's retrieval.
                prior = " | ".join(trail[-2:])[:400]
                if step.tool == "search_case_law":
                    args["context"] = f"Prior findings: {prior}"
                else:
                    args["query"] = f"{args.get('query', query)} {prior}"[:600]
            out = await self._run_tool(step.tool, args)
            self._collect(out, chunk_ids, chunks)
            for c in out.get("chunks", []):
                try:
                    top = max(top, float(c.get("confidence", 0.0)))
                except Exception:
                    pass
            if step.tool == "check_abs" and out.get("risk") not in (None, "none"):
                from src.services.abs_compliance import analyze_abs
                abs_flag = analyze_abs(query)
            if step.tool == "check_tkdl" and out.get("matched"):
                from src.services.tkdl_checker import check_tkdl
                tkdl_flag = check_tkdl(query)
            note = f"Hop {i} [{step.tool}] {step.goal}: {out.get('summary','')[:500]}"
            trail.append(note)
            self.trace.append({"hop": i, "tool": step.tool, "goal": step.goal,
                               "summary": out.get("summary", "")[:500]})
        answer = await self._synthesize(query, jurisdiction, history,
                                        chunk_ids, chunks,
                                        reply_lang=reply_lang)
        return AgentResult(answer=answer, citations=chunks, top_score=top,
                           abstained="INSUFFICIENT_BASIS" in answer,
                           abs_flag=abs_flag, tkdl_flag=tkdl_flag,
                           trace=list(self.trace))

    # -- free-form ReAct loop (dynamic tool choice at runtime) ------------

    async def run_react(self, query: str, jurisdiction: str,
                        history: str, reply_lang: str = "en") -> AgentResult:
        chunk_ids: set[int] = set()
        chunks: list[dict] = []
        top = 0.0
        transcript = (f"Conversation so far:\n{history}\n\n" if history else "")
        messages = [
            {"role": "system", "content": "Output one JSON object per turn. "
                                          "No prose, no thinking traces.\n/no_think"},
            {"role": "user", "content": REACT_PROMPT.format(
                tools=_tool_spec(self.registry), max_steps=MAX_REACT_STEPS)
             + f"\nJurisdiction: {jurisdiction}\nQuestion: {query}\n{transcript}"},
        ]
        for _ in range(MAX_REACT_STEPS):
            raw = await _agent_chat(messages)
            if not raw:
                break
            obj = _parse_json_obj(raw)
            if not obj or not isinstance(obj.get("thought"), str):
                messages.append({"role": "assistant", "content": raw[:500]})
                messages.append({"role": "user", "content": "Malformed turn. "
                                 "Reply with exactly one JSON object."})
                continue
            if "answer" in obj:
                answer = str(obj["answer"])
                break
            action = obj.get("action") or {}
            out = await self._run_tool(str(action.get("tool", "")),
                                       action.get("args", {})
                                       if isinstance(action.get("args"), dict) else {})
            self._collect(out, chunk_ids, chunks)
            for c in out.get("chunks", []):
                try:
                    top = max(top, float(c.get("confidence", 0.0)))
                except Exception:
                    pass
            self.trace.append({"tool": action.get("tool"),
                               "summary": out.get("summary", "")[:400]})
            messages.append({"role": "assistant", "content": raw[:800]})
            messages.append({"role": "user", "content": "Observation: "
                             + out.get("summary", "")[:1500]})
        else:
            answer = None
        if answer is None:
            answer = await self._synthesize(query, jurisdiction, history,
                                            chunk_ids, chunks)
        from src.services.abs_compliance import analyze_abs
        from src.services.tkdl_checker import check_tkdl
        return AgentResult(answer=answer, citations=chunks, top_score=top,
                           abstained="INSUFFICIENT_BASIS" in answer,
                           abs_flag=analyze_abs(query),
                           tkdl_flag=check_tkdl(query),
                           trace=list(self.trace))

    async def run(self, query: str, jurisdiction: str = "dual",
                  history: str = "", reply_lang: str = "en") -> AgentResult:
        """Planner first; multi-step plans run sequentially, else ReAct."""
        self.trace = []
        planner = QueryPlanner(self.registry)
        steps = await planner.plan(query, jurisdiction)
        self.trace.append({"plan": [(s.tool, s.goal) for s in steps]})
        if len(steps) >= 2:
            return await self.run_planned(query, jurisdiction, history, steps,
                                          reply_lang)
        return await self.run_react(query, jurisdiction, history, reply_lang)
