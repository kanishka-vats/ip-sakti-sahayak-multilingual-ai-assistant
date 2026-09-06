"""Out-of-scope intent gate — runs BEFORE retrieval/embedding spend.

Two layers:
  1. Deterministic lexicon fast-path (no LLM cost): the query — plus follow-up
     context — must contain at least one IP/legal/regulatory signal.
  2. LLM verifier (only when layer 1 says OUT): a tiny YES/NO Groq call rescues
     edge cases ("can I protect my grandmother's recipe?"). Fail-open to
     retrieval on LLM errors; downstream guards (threshold + overlap veto)
     still apply.
"""
from __future__ import annotations

import re

import httpx

from config.settings import get_settings

# Legal/IP/regulatory signal stems. Deliberately broad recall; the LLM layer
# handles anything this misses.
SCOPE_PAT = re.compile(
    r"patent|copyright|trademark|trade\s*mark|design|geographical|\bgi\b|"
    r"biodiversity|abs\b|nba\b|tkdl|ayurv|unani|siddha|yoga|herbal|formulation|"
    r"\bpct\b|trips|wipo|\bepo\b|uspto|hague|madrid|lisbon|nagoya|\bcbd\b|"
    r"statute|legislat|law|legal|act\b|section|rule|regulation|clause|article|"
    r"licen[sc]e|permit|approv|compliance|infring|opposit|revok|novel|prior art|"
    r"claim|filing|applicant|examin|office action|hearing|tribunal|court|"
    r"sue|damages|royalt|assign|transfer|contract|agreement|dispute|"
    r"biological resource|genetic|benefit shar|traditional knowledge|"
    r"startup|ayush|fssai|cdsco|drug|cosmetic|"
    r"pharma|clinical|brand|logo|plagiar|piracy|biopiracy|"
    r"protect.*(idea|invention|recipe|brand|work|design)|"
    r"register.*(brand|logo|design|work|drug)|intellectual propert|\bip\b|ipr\b",
    re.IGNORECASE)

OUT_OF_SCOPE_MESSAGE = (
    "This question is outside the scope of this workspace, which covers "
    "intellectual property and regulatory law (patents, trademarks, copyright, "
    "designs, geographical indications, biodiversity/ABS compliance, TKDL, and "
    "PCT/TRIPS treaties). Please ask a question related to IP or regulation."
)

VERIFY_PROMPT = """Decide if the user's question is about intellectual property, law, \
regulation, or legal compliance (patents, trademarks, copyright, designs, \
biodiversity/ABS, traditional knowledge, drugs/cosmetics regulation, treaties). \
Follow-up context is provided when the question alone is terse.
Answer with exactly one word: YES or NO."""


def lexicon_hit(query: str, context_query: str = "") -> bool:
    combined = f"{context_query} {query}"
    return bool(SCOPE_PAT.search(combined))


async def verify_intent_llm(query: str, context_query: str = "") -> bool | None:
    """Return True/False, or None when the verifier itself is unavailable."""
    s = get_settings()
    if not s.groq_key:
        return None
    user = f"Question: {query}"
    if context_query:
        user = f"Previous question: {context_query}\nFollow-up: {query}\n{user}"
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {s.groq_key}",
                         "Content-Type": "application/json"},
                json={"model": s.llm_model,
                      "messages": [{"role": "system", "content": VERIFY_PROMPT},
                                   {"role": "user", "content": user}],
                      "temperature": 0.0, "max_tokens": 5},
                timeout=20.0)
            if resp.status_code in (400, 404, 429):
                # Try fallbacks once each on model/rate errors.
                for m in s.llm_fallbacks:
                    r2 = await client.post(
                        "https://api.groq.com/openai/v1/chat/completions",
                        headers={"Authorization": f"Bearer {s.groq_key}",
                                 "Content-Type": "application/json"},
                        json={"model": m,
                              "messages": [{"role": "system", "content": VERIFY_PROMPT},
                                           {"role": "user", "content": user}],
                              "temperature": 0.0, "max_tokens": 5},
                        timeout=20.0)
                    if r2.status_code == 200:
                        resp = r2
                        break
                else:
                    return None
            resp.raise_for_status()
            verdict = resp.json()["choices"][0]["message"]["content"].strip().upper()
            if verdict.startswith("YES"):
                return True
            if verdict.startswith("NO"):
                return False
            return None
    except Exception:
        return None


async def check_scope(query: str, context_query: str = "") -> tuple[bool, str]:
    """Return (in_scope, reason). Runs lexicon first, LLM verifier on misses."""
    if lexicon_hit(query, context_query):
        return True, "lexicon"
    verdict = await verify_intent_llm(query, context_query)
    if verdict is True:
        return True, "llm-verifier"
    if verdict is False:
        return False, "llm-verifier"
    # Verifier unavailable: fail OPEN — downstream threshold + overlap veto
    # still guard retrieval quality.
    return True, "verifier-unavailable"
