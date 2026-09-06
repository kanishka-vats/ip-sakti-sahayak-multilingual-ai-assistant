"""External verifiability for citations.

The client never sees the raw corpus, so every citation carries:
  - `source_label`: human-readable document name (no raw_data/... paths).
  - `verify_url` + `verify_label`: an official portal where the user can check
    the provision (India Code, WIPO, WTO, IP India, CBD, TKDL).

Only well-known stable portal URLs are used — never guessed deep links.
"""
from __future__ import annotations

import re

# (match-term, official URL, portal label) — first match wins.
PORTALS: list[tuple[str, str, str]] = [
    ("biological diversity", "https://www.indiacode.nic.in/", "India Code"),
    ("patent", "https://ipindia.gov.in/", "IP India"),
    ("trade mark", "https://ipindia.gov.in/", "IP India"),
    ("copyright", "https://www.indiacode.nic.in/", "India Code"),
    ("design", "https://ipindia.gov.in/", "IP India"),
    ("geographical indication", "https://ipindia.gov.in/", "IP India"),
    ("drugs", "https://cdsco.gov.in/", "CDSCO"),
    ("cosmetic", "https://cdsco.gov.in/", "CDSCO"),
    ("ayurveda", "https://ayush.gov.in/", "Ministry of Ayush"),
    ("ayush", "https://ayush.gov.in/", "Ministry of Ayush"),
    ("food safety", "https://www.fssai.gov.in/", "FSSAI"),
    ("consumer protection", "https://www.indiacode.nic.in/", "India Code"),
    ("pct", "https://www.wipo.int/pct/en/", "WIPO PCT"),
    ("trips", "https://www.wto.org/english/tratop_e/trips_e/trips_e.htm", "WTO TRIPS"),
    ("hague", "https://www.wipo.int/hague/en/", "WIPO Hague"),
    ("lisbon", "https://www.wipo.int/lisbon/en/", "WIPO Lisbon"),
    ("madrid", "https://www.wipo.int/madrid/en/", "WIPO Madrid"),
    ("nagoya", "https://www.cbd.int/abs/", "CBD ABS"),
    ("cbd", "https://www.cbd.int/", "CBD"),
    ("tkdl", "https://www.tkdl.res.in/", "TKDL (CSIR)"),
    ("wipo", "https://www.wipo.int/", "WIPO"),
    ("grtkf", "https://www.wipo.int/tk/en/", "WIPO TK"),
]

FALLBACK = {
    "india": ("https://www.indiacode.nic.in/", "India Code"),
    "international": ("https://www.wipo.int/", "WIPO"),
}


def clean_label(doc_name: str, source_file: str = "") -> str:
    """Human-readable source label, no filesystem paths."""
    name = (doc_name or "").strip()
    if not name and source_file:
        name = source_file.split("/")[-1].split("\\")[-1]
        name = re.sub(r"\.(pdf|md|txt|csv|json)$", "", name, flags=re.IGNORECASE)
    name = re.sub(r"_+", " ", name).strip()
    name = re.sub(r"\s{2,}", " ", name)
    return name[:110] if name else "Curated corpus document"


def verify_link(doc_name: str, jurisdiction: str = "india") -> tuple[str, str]:
    low = (doc_name or "").lower()
    for term, url, label in PORTALS:
        if term in low:
            return url, label
    return FALLBACK.get(jurisdiction, FALLBACK["india"])
