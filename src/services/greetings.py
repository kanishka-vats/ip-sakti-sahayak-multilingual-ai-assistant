"""Greeting short-circuit — runs before the scope gate.

Pure greetings (hi, namaste, good evening, kaise ho, …) get a warm,
time-aware reply addressed by name — never the out-of-scope refusal.
Anything with legal substance falls through to the normal pipeline, even
when it opens with a greeting ("hello, what is TKDL").
"""
from __future__ import annotations

import datetime
import re

# First-token greeting starters (incl. common misspellings / transliterations).
STARTERS = {
    "hi", "hii", "hello", "helo", "halo", "hey", "howdy", "yo", "hai",
    "namaste", "namaskar", "kaise", "good", "goodd", "morning", "afternoon",
    "evening", "night", "shubh", "sat", "adab", "ram", "pranam", "vanakkam",
}

# Full-phrase small talk ("how are you", "what's up", …).
PHRASES = re.compile(
    r"^(how are (you|u)|how r u|how do you do|what'?s up|how'?s it going|"
    r"how have you been|kaise ho(\s+(aap|tum))?|aur (batao|sab badhiya)|"
    r"good (morning|afternoon|evening|night|day)|shubh (ratri|prabhat|din)|"
    r"sat sri akal|ram ram|namaste(\s+\w+)?|hello(\s+\w+)?|hi(\s+\w+)?)\s*[!.?…]*$",
    re.IGNORECASE)

_WORD = re.compile(r"[a-z]+")


def is_greeting(query: str) -> bool:
    """True only for pure small talk: short, no legal substance.

    The caller MUST check scope-lexicon first semantics separately — this
    function only answers 'is this shaped like a greeting'. The pipeline
    applies it only when the scope gate finds no legal signal.
    """
    text = query.strip().lower()
    if not text or len(text) > 60:
        return False
    if PHRASES.match(text):
        return True
    words = _WORD.findall(text)
    return bool(words) and words[0] in STARTERS and len(words) <= 4


def greeting_for(username: str | None = None,
                 now: datetime.datetime | None = None) -> str:
    now = now or datetime.datetime.now()
    h = now.hour
    if 5 <= h < 12:
        daypart = "Good morning"
    elif 12 <= h < 17:
        daypart = "Good afternoon"
    elif 17 <= h < 22:
        daypart = "Good evening"
    else:
        daypart = "Shubh ratri"
    name = (username or "").strip()
    hello = f"{daypart}, {name}!" if name else f"{daypart}!"
    return (
        f"{hello} How may I help you today?\n\n"
        "I can walk you through patents, trademarks, copyright, AYUSH and "
        "ABS compliance, TKDL prior art, or PCT/TRIPS filings — "
        "what would you like to talk about?"
    )
