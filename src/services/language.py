"""Multilingual layer: detect Hindi / Hinglish, translate to English for the
pipeline, and answer back in the user's language.

Pipeline position: VERY FIRST step in _answer_pipeline — everything downstream
(greetings, scope gate, typo fixer, retrieval, guardrails, generation) then
operates on English text, so none of those English-built components need
per-language variants.

Cost: zero for English (regex only); one small Groq call per non-English
query. Safe degradation: if translation fails, the original text flows
through unchanged (old behavior).
"""
from __future__ import annotations

import re

import httpx

from config.settings import get_settings

DEVANAGARI_PAT = re.compile(r"[\u0900-\u097F]")

# Unambiguous Hinglish markers (not English words) score 2 ...
HINGLISH_STRONG = {
    "kya", "kyaa", "hai", "hain", "kaise", "kaun", "kahan", "kab", "kyun",
    "kyon", "aap", "tum", "tumhe", "nahi", "nahin", "matlab", "chahiye",
    "hota", "hoti", "hote", "karte", "karta", "karti", "karein", "karo",
    "liye", "baare", "kaunsa", "kaunsi", "kitna", "kitni", "kafi",
}
# ... ambiguous ones (also English words / substrings) score 1.
HINGLISH_WEAK = {
    "me", "main", "ko", "se", "par", "ka", "ki", "ke", "ne", "aur",
    "yeh", "woh", "tera", "mera", "hamara", "apka", "batao", "samjhao",
}

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"


def detect_language(query: str) -> str:
    """Return 'hi' (Devanagari), 'hinglish' (Latin-script Hindi), or 'en'."""
    if DEVANAGARI_PAT.search(query or ""):
        return "hi"
    toks = set(re.findall(r"[a-z]+", (query or "").lower()))
    if not toks:
        return "en"
    score = 2 * len(toks & HINGLISH_STRONG) + 1 * len(toks & HINGLISH_WEAK)
    return "hinglish" if score >= 2 else "en"


async def to_english(text: str, lang: str) -> str | None:
    """Translate a Hindi/Hinglish query to plain English. None on failure."""
    s = get_settings()
    if not s.groq_key or lang == "en":
        return None
    label = "Hindi (Devanagari script)" if lang == "hi" else "Hinglish (Hindi written in Latin script)"
    user = (f"Translate the following {label} question into plain English. "
            f"Output ONLY the translation, no quotes or explanation. "
            f"Transliterated proper nouns must be restored exactly — in "
            f"particular: TKDL, NBA, PCT, TRIPS, WIPO, CBD, AYUSH, GI.\n{text[:1000]}")
    async with httpx.AsyncClient() as client:
        for m in [s.llm_model, *s.llm_fallbacks]:
            try:
                resp = await client.post(
                    GROQ_URL,
                    headers={"Authorization": f"Bearer {s.groq_key}",
                             "Content-Type": "application/json"},
                    json={"model": m,
                          "messages": [{"role": "user", "content": user}],
                          "temperature": 0.0, "max_tokens": 120},
                    timeout=30.0)
                if resp.status_code in (400, 404, 429):
                    continue
                resp.raise_for_status()
                out = (resp.json()["choices"][0]["message"]["content"] or "").strip()
                if out:
                    return out.strip().strip('"').strip("'")
            except Exception:
                continue
    return None


def reply_instruction(lang: str) -> str:
    if lang == "hi":
        return "Reply in Hindi using Devanagari script."
    if lang == "hinglish":
        return "Reply in Hinglish (Hindi written in Latin/Roman script)."
    return ""


# Explicit answer-language requests inside the query itself
# ("what is tkdl in hindi"). First match wins; marker is stripped so it
# can't pollute retrieval.
REPLY_MARKERS = [
    (re.compile(r"\bhindi\s*(mein|me|main|bhasha|language)?\b|\bin hindi\b|हिंदी में|देवनागरी( में)?",
                re.IGNORECASE), "hi"),
    (re.compile(r"\bhinglish\s*(mein|me|main)?\b|\bin hinglish\b|roman (hindi|script)",
                re.IGNORECASE), "hinglish"),
    (re.compile(r"\bin english\b|english (mein|me|main)|अंग्रेजी में",
                re.IGNORECASE), "en"),
]


def split_reply_override(query: str) -> tuple[str, str | None]:
    """Return (cleaned_query, forced_reply_lang|None)."""
    for pat, lang in REPLY_MARKERS:
        m = pat.search(query or "")
        if m:
            cleaned = re.sub(r"\s{2,}", " ",
                             (query[:m.start()] + " " + query[m.end():])).strip(" ,-")
            return cleaned, lang
    return query, None


# Deterministic Hindi/Hinglish -> English glossary (offline, zero tokens).
# Tried BEFORE Groq translation: keyword-English is ideal for retrieval, and
# it keeps Hindi working even when the LLM tier is throttled.
GLOSSARY = {
    # greetings
    "namaste": "hello", "namaskar": "hello", "pranam": "hello",
    "नमस्ते": "hello", "नमस्कार": "hello", "प्रणाम": "hello",
    "sat": "hello", "sri": "hello", "akal": "hello",
    # question words
    "kya": "what", "kyaa": "what", "क्या": "what",
    "kaun": "who", "कौन": "who", "kise": "whom", "किसे": "whom",
    "kaise": "how", "कैसे": "how", "kyun": "why", "kyon": "why", "क्यों": "why",
    "kahan": "where", "कहाँ": "where", "kab": "when", "कब": "when",
    "kitna": "how much", "kitni": "how much", "कितना": "how much",
    "kaunsa": "which", "kaunsi": "which", "कौनसा": "which",
    "hai": "is", "hain": "are", "है": "is", "हैं": "are",
    "tha": "was", "thi": "was", "the": "were", "थे": "were", "था": "was",
    # core IP vocabulary (Devanagari + Hinglish)
    "petent": "patent", "पेटेंट": "patent",
    "trademark": "trademark", "ट्रेडमार्क": "trademark",
    "copyright": "copyright", "कॉपीराइट": "copyright",
    "design": "design", "डिजाइन": "design", "डिज़ाइन": "design",
    "kanoon": "law", "कानून": "law", "vidhi": "law",
    "adhiniyam": "act", "अधिनियम": "act",
    "dhara": "section", "धारा": "section",
    "niyam": "rules", "नियम": "rules", "niyamavali": "rules",
    "license": "license", "licence": "license", "लाइसेंस": "license",
    "panjikaran": "registration", "पंजीकरण": "registration",
    "avedan": "application", "आवेदन": "application",
    "anumodan": "approval", "अनुमोदन": "approval", "manzoori": "approval",
    "ullanghan": "infringement", "उल्लंघन": "infringement",
    "virodh": "opposition", "विरोध": "opposition",
    "daava": "claim", "दावा": "claim", "daave": "claims",
    "naveenta": "novelty", "नवीनता": "novelty",
    "radd": "cancel", "रद्द": "cancel", "nirast": "revoke", "निरस्त": "revoke",
    "avadhi": "term", "अवधि": "term", "miyad": "term",
    "shulk": "fees", "शुल्क": "fees", "fees": "fees",
    "prakriya": "procedure", "प्रक्रिया": "procedure",
    "dastavej": "document", "दस्तावेज़": "document", "dastavez": "document",
    "adalat": "court", "अदालत": "court",
    "dand": "penalty", "दंड": "penalty", "jurmana": "penalty",
    "laabh": "benefit", "लाभ": "benefit",
    # biodiversity / AYUSH domain
    "jaiv": "bio", "vividhta": "diversity", "जैव": "bio", "विविधता": "diversity",
    "sansadhan": "resource", "संसाधन": "resource",
    "paramparik": "traditional", "पारंपरिक": "traditional",
    "gyaan": "knowledge", "ज्ञान": "knowledge",
    "aushadhi": "medicine", "औषधि": "medicine", "dawai": "medicine",
    "jadi": "herb", "booti": "herb", "जड़ी": "herb", "बूटी": "herb",
    "videshi": "foreign", "विदेशी": "foreign",
    "bharatiya": "indian", "भारतीय": "indian",
    "neem": "neem", "नीम": "neem",
    "haldi": "turmeric", "हल्दी": "turmeric",
    "ashwagandha": "ashwagandha", "अश्वगंधा": "ashwagandha",
    "tulsi": "tulsi", "तुलसी": "tulsi",
    "amla": "amla", "आंवला": "amla",
    "ayurveda": "ayurveda", "आयुर्वेद": "ayurveda", "ayurved": "ayurveda",
    "unani": "unani", "यूनानी": "unani",
    "siddha": "siddha", "सिद्ध": "siddha",
    "yoga": "yoga", "योग": "yoga",
    "tkdl": "TKDL", "टीकेडीएल": "TKDL",
    "nba": "NBA", "एनबीए": "NBA",
    "pct": "PCT", "पीसीटी": "PCT",
    "trips": "TRIPS", "ट्रिप्स": "TRIPS",
    "wipo": "WIPO", "वाइपो": "WIPO",
    "cbd": "CBD", "ayush": "AYUSH", "आयुष": "AYUSH",
    "startup": "startup", "स्टार्टअप": "startup",
    "company": "company", "कंपनी": "company",
    "dhaara": "section",
    "form": "form", "फॉर्म": "form",
    "saath": "with", "sath": "with", "साथ": "with",
}

# Grammar particles carrying no retrieval signal — dropped.
HI_STOP = {
    "का", "की", "के", "को", "ने", "से", "में", "पर", "तक", "और", "या",
    "यह", "वह", "ये", "वे", "जो", "तो", "भी", "ही", "नहीं", "मत", "हो",
    "था", "ka", "ki", "ke", "ko", "ne", "se", "mein", "par", "aur", "ya",
}

_TOKENIZE = re.compile(r"[\u0900-\u097F]+|[a-z]+")
_DEVA_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")


def keyword_bridge(text: str) -> tuple[str, float]:
    """Deterministic glossary translation. Returns (bridged, coverage).

    Coverage = usable tokens / content tokens. Use the bridge when it is
    >= 0.5 (at least half the content words mapped or already English);
    otherwise fall back to Groq translation.
    """
    toks = _TOKENIZE.findall((text or "").lower().translate(_DEVA_DIGITS))
    out: list[str] = []
    usable = 0
    content = 0
    for t in toks:
        if t in HI_STOP or len(t) < 2:
            continue
        content += 1
        if t in GLOSSARY:
            out.append(GLOSSARY[t])
            usable += 1
        elif t.isascii():
            out.append(t)  # already English, keep as-is
            usable += 1
        # else: unknown Devanagari token — dropped (can't retrieve on it)
    coverage = (usable / content) if content else 0.0
    mapped = sum(1 for t in toks if t in GLOSSARY)
    if coverage >= 0.5 and mapped >= 1:
        return " ".join(out), round(coverage, 2)
    return "", 0.0
