"""LLM triple extraction: chunks -> entities + edges (Phase 1 ingestion).

Batch job populating the graph tables from the existing flat corpus:

    uv run python -m src.ingestion.graph_ingester --limit 50
    uv run python -m src.ingestion.graph_ingester --jurisdiction india  # full run

Resume-safe: chunks already covered (see GraphStore.covered_chunk_ids)
are skipped. Uses the configured Groq model + fallbacks at temperature 0.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys

import httpx

from config.settings import get_settings
from src.core.graph_store import GraphStore
from src.core.vector_store import VectorStore
from src.ingestion.embedder import Embedder

ENTITY_TYPES = ["act", "section", "rule", "herb", "formulation", "doctrine",
                "authority", "treaty", "form", "case", "concept"]
RELATIONS = ["defines", "requires", "defeats", "exempts", "amends",
             "references", "protects", "governs", "penalizes", "subject_to"]

EXTRACT_PROMPT = """Extract a legal knowledge graph from the statute excerpt below.
Return STRICT JSON only, no prose, no code fences:
{{"entities": [{{"type": "<one of: {types}>", "name": "<canonical short name>",
"description": "<one line>"}}],
"edges": [{{"source": "<entity name>", "target": "<entity name>",
"relation": "<one of: {rels}>"}}]}}
Rules: at most 8 entities and 8 edges; entity names in edges must match
entity names exactly; prefer sections, acts, herbs, authorities and their
normative relations (requires, defeats, protects, governs...).
Excerpt:
{text}"""

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"


def _parse_triples(raw: str) -> dict:
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        return {"entities": [], "edges": []}
    try:
        data = json.loads(raw[start:end + 1])
    except Exception:
        return {"entities": [], "edges": []}
    ents = [e for e in data.get("entities", [])
            if isinstance(e, dict) and e.get("name")]
    edges = [g for g in data.get("edges", [])
             if isinstance(g, dict) and g.get("source") and g.get("target")]
    return {"entities": ents[:8], "edges": edges[:8]}


async def _chat_json(client: httpx.AsyncClient, models: list[str], api_key: str,
                     text: str) -> dict:
    prompt = EXTRACT_PROMPT.format(types="|".join(ENTITY_TYPES),
                                   rels="|".join(RELATIONS),
                                   text=text[:4000])
    last: Exception | None = None
    for m in models:
        try:
            resp = await client.post(
                GROQ_URL,
                headers={"Authorization": f"Bearer {api_key}",
                         "Content-Type": "application/json"},
                json={"model": m,
                      "messages": [{"role": "user", "content": prompt}],
                      "temperature": 0.0, "max_tokens": 800},
                timeout=90.0)
            if resp.status_code in (400, 404):
                continue
            resp.raise_for_status()
            return _parse_triples(
                resp.json()["choices"][0]["message"]["content"] or "")
        except Exception as exc:
            last = exc
            continue
    if last:
        print(f"[graph_ingester] extraction failed: {last}")
    return {"entities": [], "edges": []}


async def ingest(corpus_limit: int | None = None, jurisdiction: str | None = None,
                offset: int = 0, batch_embed: int = 16) -> dict:
    s = get_settings()
    store = VectorStore(s.db_path_abs)
    store.init()
    graph = GraphStore(s.db_path_abs)
    graph.init()
    embedder = Embedder()
    covered = graph.covered_chunk_ids(jurisdiction)
    rows = store.list_chunks(jurisdiction, limit=None, offset=offset)
    # list_chunks(limit=None) streams everything; apply corpus_limit manually
    # so resume-skips don't consume the budget.
    todo = [r for r in rows if r["id"] not in covered]
    if corpus_limit is not None:
        todo = todo[:corpus_limit]
    print(f"[graph_ingester] {len(todo)} chunks to process "
          f"({len(covered)} already covered)", flush=True)
    stats = {"chunks": 0, "entities": 0, "edges": 0}
    models = [s.llm_model, *s.llm_fallbacks]
    async with httpx.AsyncClient() as client:
        for n, row in enumerate(todo, 1):
            triples = await _chat_json(client, models, s.groq_key, row["text"])
            if not triples["entities"] and not triples["edges"]:
                continue
            texts = [f"{e.get('type','')}: {e.get('name','')}. "
                     f"{e.get('description','')}" for e in triples["entities"]]
            try:
                vecs = await embedder.embed(texts)
            except Exception as exc:
                print(f"[graph_ingester] embed failed, skipping chunk {row['id']}: {exc}")
                continue
            name_to_id: dict[str, int] = {}
            for e, v in zip(triples["entities"], vecs):
                etype = str(e.get("type", "concept")).lower()[:24] or "concept"
                eid = graph.upsert_entity(
                    etype, str(e["name"])[:160], str(e.get("description", ""))[:600],
                    row["jurisdiction"], row["id"], v, 0.9)
                name_to_id[str(e["name"]).strip().lower()] = eid
                stats["entities"] += 1
            for g in triples["edges"]:
                a = name_to_id.get(str(g["source"]).strip().lower())
                b = name_to_id.get(str(g["target"]).strip().lower())
                rel = str(g.get("relation", "references")).lower()[:32] or "references"
                if a and b and a != b:
                    graph.add_edge(a, b, rel)
                    stats["edges"] += 1
            stats["chunks"] += 1
            if n % 10 == 0:
                print(f"[graph_ingester] {n}/{len(todo)} chunks, "
                      f"{stats['entities']} entities, {stats['edges']} edges", flush=True)
    print(f"[graph_ingester] DONE {stats} | store: {graph.stats()}", flush=True)
    return stats


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=50)
    ap.add_argument("--jurisdiction", default=None)
    ap.add_argument("--offset", type=int, default=0)
    args = ap.parse_args()
    asyncio.run(ingest(args.limit, args.jurisdiction, args.offset))


if __name__ == "__main__":
    sys.exit(main() or 0)
