"""Isolated reasoning for Indian vs International law (dual-split safe)."""
from __future__ import annotations

import re

INDIA_HINTS = re.compile(
    r"india|indian|patent office|section 3\(d\)|biodiversity|nba\b|ayush|tkdl|"
    r"gi tag|geographical indication.*india|dsir|csir", re.IGNORECASE)
INTL_HINTS = re.compile(
    r"\bpct\b|trips|wipo|\bepo\b|uspto|hague|madrid|lisbon|nagoya|cbd\b|paris convention",
    re.IGNORECASE)


def route_jurisdiction(query: str, requested: str = "dual") -> str:
    """Honor explicit UI toggle; auto-detect only when requested == 'auto'."""
    if requested in ("india", "international", "dual"):
        return requested
    india = bool(INDIA_HINTS.search(query))
    intl = bool(INTL_HINTS.search(query))
    if india and not intl:
        return "india"
    if intl and not india:
        return "international"
    return "dual"


def jurisdiction_frame(jurisdiction: str) -> str:
    if jurisdiction == "india":
        return ("Scope: Indian law only (Patents Act 1970, BD Act 2002, GI/Copyright/Designs "
                "statutes, IPO practice). Do not cite foreign law.")
    if jurisdiction == "international":
        return ("Scope: International treaties only (PCT, TRIPS, WIPO treaties, EPO/USPTO "
                "guidelines). Do not cite Indian domestic statutes.")
    return ("Scope: Compare both jurisdictions in isolated sections — "
            "## India and ## International — plus a comparison table.")
