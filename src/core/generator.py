"""Groq Qwen client with strict JSON/streaming enforcement.

Uses the Groq OpenAI-compatible endpoint. Model id resolved from
config/models.yaml via get_settings() — never hardcoded.
"""
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from config.settings import get_settings

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
DISCLAIMER = "Information only; not legal advice."

SYSTEM_PROMPT = """You are a legal/IP research assistant for Indian and international IP law.
STRICT RULES:
1. Answer ONLY from the provided context excerpts. Zero extrapolation outside them.
2. Every factual legal claim MUST end with an inline bracketed citation [^N] where N
   is the 1-based index of the excerpt used. If no excerpt supports a claim, omit it.
   For procedures and multi-step processes, EVERY step ends with its own [^N].
   Write steps exactly like this example:
     - File Form 1 with the provisional specification [^2].
     - Respond to the First Examination Report within 6 months [^3].
3. Be thorough: cover every relevant excerpt, use Markdown headings, bullets, and a
   comparison table when excerpts span jurisdictions. Follow-up questions ("explain
   it", "more detail") demand a full detailed explanation, not a summary.
   A correct answer NEVER contains zero citations when excerpts are provided.
4. If context is insufficient, output exactly: INSUFFICIENT_BASIS and nothing else.
5. Never invent section numbers, case names, or treaty articles.
6. Comply with India's DPDP Act: never request or repeat personal data.
7. Never include legal disclaimers or "not legal advice" lines — the UI shows them.
8. Output ONLY the final answer. Never emit chain-of-thought, <think> blocks,
   or reasoning traces.
/no_think
"""

REF_PAT = None  # compiled lazily in count_refs to keep import light


def count_refs(text: str) -> int:
    """Number of distinct inline [^N] citations in a draft answer."""
    global REF_PAT
    if REF_PAT is None:
        import re as _re
        REF_PAT = _re.compile(r"\[\^(\d+)\]")
    return len(set(REF_PAT.findall(text or "")))


REWRITE_INSTRUCTION = (
    "Your draft above contains ZERO inline [^N] citations, which violates rule 2. "
    "Rewrite the same answer now with these constraints:\n"
    "- Put one [^N] citation at the end of EVERY bullet, step, and factual sentence, "
    "using only excerpt numbers 1..{n}.\n"
    "- For multi-step procedures, every numbered step ends with its citation, e.g.:\n"
    "  - File Form 1 with the provisional specification [^2].\n"
    "  - Respond to the First Examination Report within 6 months [^3].\n"
    "- Do not add facts absent from the excerpts. Under 600 words. No <think> tags."
)


def build_messages(query: str, excerpts: list[dict[str, Any]], jurisdiction: str,
                   history: str = "") -> list[dict[str, str]]:
    ctx_lines = []
    for i, e in enumerate(excerpts, 1):
        ctx_lines.append(
            f"[{i}] {e.get('act_name','')} — {e.get('section','')} "
            f"({e.get('source_file','')}):\n{e.get('text','')[:2000]}"
        )
    context = "\n\n".join(ctx_lines) if ctx_lines else "(no excerpts)"
    user = (
        f"Jurisdiction: {jurisdiction}\nQuestion: {query}\n\n"
        f"Context excerpts:\n{context}\n\n"
        "Answer with inline [^N] citations mapped to the excerpt numbers above."
    )
    if history:
        user = f"Conversation so far:\n{history}\n\n{user}"
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


class Generator:
    def __init__(self, api_key: str | None = None, model: str | None = None):
        s = get_settings()
        self.api_key = api_key or s.groq_key
        self.model = model or s.llm_model
        self.fallbacks = s.llm_fallbacks
        self.temperature = s.llm_temperature
        self.max_tokens = s.llm_max_tokens

    @property
    def offline(self) -> bool:
        return not self.api_key

    def _offline_answer(self, query: str, excerpts: list[dict[str, Any]]) -> str:
        if not excerpts:
            return "INSUFFICIENT_BASIS"
        lines = [f"## Grounded summary — {query[:120]}", ""]
        for i, e in enumerate(excerpts[:5], 1):
            snippet = e.get("text", "")[:350].replace("\n", " ")
            lines.append(f"- {e.get('act_name','Source')} ({e.get('section','')}) [^{i}]: {snippet}")
        lines += ["", "Full statutory text is excerpted in the citation panel. "
                       "Connect GROQ_API_KEY for full abstractive answers."]
        return "\n".join(lines)

    async def _chat(self, client: httpx.AsyncClient, model: str,
                    messages: list[dict[str, str]], stream: bool) -> httpx.Response:
        resp = await client.post(
            GROQ_URL,
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            json={"model": model, "messages": messages, "temperature": self.temperature,
                  "max_tokens": self.max_tokens, "stream": stream},
            timeout=90.0,
        )
        if resp.status_code in (400, 404) and "model" in resp.text.lower():
            raise ValueError(f"model_error:{model}:{resp.text[:200]}")
        resp.raise_for_status()
        return resp

    async def complete(self, query: str, excerpts: list[dict[str, Any]],
                       jurisdiction: str = "dual", history: str = "") -> str:
        messages = build_messages(query, excerpts, jurisdiction, history)
        if self.offline:
            return self._offline_answer(query, excerpts)
        async with httpx.AsyncClient() as client:
            models = [self.model, *self.fallbacks]
            last: Exception | None = None
            for m in models:
                try:
                    resp = await self._chat(client, m, messages, stream=False)
                    raw = resp.json()["choices"][0]["message"]["content"] or ""
                    clean = strip_thinking(raw)
                    if not clean and raw.strip():
                        # Think-only truncation: one retry demanding a direct answer.
                        retry = [messages[0], {
                            "role": "user",
                            "content": messages[1]["content"] + (
                                "\nAnswer directly in under 600 words. "
                                "No reasoning, no <think> tags.")}]
                        resp2 = await self._chat(client, m, retry, stream=False)
                        raw2 = resp2.json()["choices"][0]["message"]["content"] or ""
                        clean = strip_thinking(raw2)
                    if not clean:
                        last = RuntimeError(f"empty answer from {m}")
                        continue
                    if "INSUFFICIENT_BASIS" in clean or not excerpts:
                        return clean
                    if count_refs(clean) == 0:
                        # Citation-compliance retry: procedural/multi-step drafts
                        # sometimes synthesize uncited prose. Demand a rewrite
                        # with per-step [^N] markers on the SAME model.
                        print(f"[generator] no citations from {m}; rewrite retry.")
                        rewrite_msgs = [
                            messages[0], messages[1],
                            {"role": "assistant", "content": clean[:1500]},
                            {"role": "user", "content":
                             REWRITE_INSTRUCTION.format(n=len(excerpts))},
                        ]
                        resp3 = await self._chat(client, m, rewrite_msgs, stream=False)
                        raw3 = resp3.json()["choices"][0]["message"]["content"] or ""
                        clean3 = strip_thinking(raw3)
                        if clean3 and "INSUFFICIENT_BASIS" in clean3:
                            return clean3
                        if count_refs(clean3) > 0:
                            return clean3
                        last = RuntimeError(f"uncited answer from {m} after rewrite")
                        continue
                    return clean
                except Exception as exc:
                    last = exc
                    continue
            # All LLM models failed (decommissioned / rate-limited / offline):
            # degrade to grounded extractive answer instead of HTTP 500.
            print(f"[generator] all Groq models failed ({last}); extractive fallback.")
            return self._offline_answer(query, excerpts)

    async def clarify(self, query: str, sketches: list[str]) -> str | None:
        """Empathetic clarification for vague-but-in-scope questions.

        Returns markdown (or None when the LLM is unusable — caller falls back
        to a deterministic template). Never cites; only references the
        retrieved provisions by name so the user can pick a track.
        """
        listing = "\n".join(f"- {s}" for s in sketches[:5]) or "(none)"
        user = (
            "The user's question is vague but in-scope. Closest provisions found:\n"
            f"{listing}\n\nUser question: {query}\n\n"
            "Write a short empathetic reply (max 150 words): acknowledge you want to "
            "point them to the right provisions; state your best-guess interpretation "
            "in one sentence starting exactly 'My best reading is:'; name the 2-3 "
            "closest provisions above; end by asking them to reply 'yes, proceed' "
            "or specify which track they mean. Plain markdown, no citations, "
            "no disclaimers."
        )
        messages = [
            {"role": "system",
             "content": "You are an empathetic legal research assistant. Be warm and "
                        "conversational, but never answer beyond the provisions listed. "
                        "Never emit thinking traces or <think> blocks.\n/no_think"},
            {"role": "user", "content": user},
        ]
        if self.offline:
            return None
        async with httpx.AsyncClient() as client:
            for m in [self.model, *self.fallbacks]:
                try:
                    resp = await self._chat(client, m, messages, stream=False)
                    raw = resp.json()["choices"][0]["message"]["content"] or ""
                    clean = strip_thinking(raw)
                    if clean:
                        return clean
                except Exception:
                    continue
        return None

    async def stream(self, query: str, excerpts: list[dict[str, Any]],
                     jurisdiction: str = "dual", history: str = "") -> AsyncIterator[str]:
        """Yields text deltas. Offline mode yields the extractive answer in chunks."""
        if self.offline:
            full = self._offline_answer(query, excerpts)
            for i in range(0, len(full), 120):
                yield full[i:i + 120]
            return
        messages = build_messages(query, excerpts, jurisdiction, history)
        models = [self.model, *self.fallbacks]
        async with httpx.AsyncClient(timeout=90.0) as client:
            for m in models:
                try:
                    buf: list[str] = []
                    async with client.stream(
                        "POST", GROQ_URL,
                        headers={"Authorization": f"Bearer {self.api_key}"},
                        json={"model": m, "messages": messages,
                              "temperature": self.temperature,
                              "max_tokens": self.max_tokens, "stream": True},
                    ) as resp:
                        if resp.status_code in (400, 404):
                            body = await resp.aread()
                            if b"model" in body.lower():
                                continue
                            raise RuntimeError(body[:300].decode(errors="ignore"))
                        resp.raise_for_status()
                        async for line in resp.aiter_lines():
                            if not line or not line.startswith("data:"):
                                continue
                            payload = line[5:].strip()
                            if payload == "[DONE]":
                                return
                            try:
                                delta = json.loads(payload)["choices"][0]["delta"].get("content", "")
                            except Exception:
                                continue
                            if delta:
                                buf.append(delta)
                        full = strip_thinking("".join(buf))
                        for i in range(0, len(full), 120):
                            yield full[i:i + 120]
                        return
                except ValueError:
                    continue
            yield "INSUFFICIENT_BASIS"


def strip_thinking(text: str) -> str:
    """Remove Qwen-style <think>...</think> reasoning traces (never user-facing)."""
    import re as _re
    out = _re.sub(r"<think>.*?</think>", "", text, flags=_re.DOTALL | _re.IGNORECASE)
    # Unclosed trailing block (truncated generation): drop from <think> onward.
    out = _re.sub(r"<think>.*$", "", out, flags=_re.DOTALL | _re.IGNORECASE)
    return out.strip()
