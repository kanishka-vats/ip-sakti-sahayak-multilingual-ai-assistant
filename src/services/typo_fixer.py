"""Transposition-aware typo correction for legal/IP terms.

Catches 'tdkl' -> 'TKDL', 'ptc' -> 'PCT', 'tmdl' -> 'TKDL' etc. before the
scope gate and retrieval run, so the pipeline searches the intended term.
The correction is always surfaced to the user as an acknowledgment line —
never applied silently.
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher

CANONICAL_TERMS = [
    "tkdl", "pct", "trips", "wipo", "epo", "uspto", "nba", "ayush",
    "ayurveda", "unani", "siddha", "patent", "copyright", "trademark",
    "design", "gi", "cbd", "nagoya", "hague", "madrid", "lisbon",
    "biodiversity", "aahara", "fssai", "cdsco", "novelty",
    "infringement", "opposition", "licence", "license",
]

_TOKEN = re.compile(r"[A-Za-z]{3,}")


def _is_trivial_variant(token: str, term: str) -> bool:
    """True for plurals / exact hits that must NOT be 'corrected'."""
    t = token.lower()
    if t == term:
        return True
    if t.rstrip("s") == term or term.rstrip("s") == t:
        return True
    if t in term or term in t:
        # substring containment (e.g. 'patents' in ... ) — leave alone
        return True
    return False


def _close_enough(token: str, term: str) -> bool:
    t = token.lower()
    if len(t) != len(term):
        # single substitution / transposition only for equal lengths,
        # except short tokens where one-char diff is significant
        if abs(len(t) - len(term)) > 1:
            return False
    if sorted(t) == sorted(term) and len(t) >= 3:
        return True  # transposition / anagram ("tdkl" <-> "tkdl")
    if len(t) < 4:
        return False
    return SequenceMatcher(None, t, term).ratio() >= 0.8


def correct_typos(text: str) -> tuple[str, list[tuple[str, str]]]:
    """Return (corrected_text, [(raw, fixed), ...]). Case of fixed follows term."""
    corrections: list[tuple[str, str]] = []

    def fix(m: re.Match) -> str:
        tok = m.group(0)
        low = tok.lower()
        for term in CANONICAL_TERMS:
            if _is_trivial_variant(low, term):
                return tok
        for term in CANONICAL_TERMS:
            if _close_enough(low, term):
                fixed = term.upper() if len(term) <= 4 else term
                if tok[0].isupper():
                    fixed = fixed.capitalize() if len(term) > 4 else fixed
                corrections.append((tok, fixed))
                return fixed
        return tok

    return _TOKEN.sub(fix, text), corrections


def correction_note(corrections: list[tuple[str, str]]) -> str:
    if not corrections:
        return ""
    bits = ", ".join(f"'{raw}' → **{fixed}**" for raw, fixed in corrections)
    first_raw, first_fixed = corrections[0]
    return (
        f"> _Note: '{first_raw}' isn't a term in IP law — "
        f"I took it to mean **{first_fixed}**. "
        f"Corrections applied: {bits}. If that's wrong, please rephrase._\n\n"
    )
