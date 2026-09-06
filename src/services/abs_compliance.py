"""ABS compliance helper — Biological Diversity Act, 2002 (NBA India)."""
from __future__ import annotations

import re

BIO_HINTS = re.compile(
    r"neem|turmeric|ashwagandha|triphala|brahmi|tulsi|amla|shatavari|plant|herb|"
    r"extract|formulation|biological resource|genetic resource|traditional knowledge|"
    r"ayurved|unani|siddha|bio-?prospect|benefit.?shar", re.IGNORECASE)
FOREIGN_HINTS = re.compile(
    r"foreign|abroad|pct|wipo|export|licensee|collaborat|uspto|epo|multinational", re.IGNORECASE)
IPR_HINTS = re.compile(r"patent|ipr|intellectual property|fil(e|ing)|claim", re.IGNORECASE)


def analyze_abs(query: str) -> dict | None:
    """Return ABS flag dict, or None when query is not ABS-relevant."""
    if not BIO_HINTS.search(query):
        return None
    foreign = bool(FOREIGN_HINTS.search(query))
    ipr = bool(IPR_HINTS.search(query))
    obligations: list[str] = []
    if foreign:
        obligations.append(
            "Sec. 3 — prior NBA approval required for foreign persons/entities accessing "
            "Indian biological resources or associated knowledge.")
    else:
        obligations.append(
            "Sec. 7 — Indian entities: prior intimation to State Biodiversity Board (SBB); "
            "commercial use may trigger benefit-sharing.")
    if ipr or foreign:
        obligations.append(
            "Sec. 6 — prior NBA approval before applying for IPR on inventions based on "
            "Indian biological resources (file Form-III; route via NBA Chennai).")
    obligations.append(
        "Sec. 4 — no transfer of research results to foreigners without NBA approval.")
    risk = "high" if (foreign and ipr) else ("medium" if (foreign or ipr) else "low")
    return {
        "applies": True,
        "risk": risk,
        "obligations": obligations,
        "authority": "National Biodiversity Authority (NBA), Chennai — nbaindia.org",
        "next_steps": [
            "Identify the biological resource + source location/access point.",
            "File NBA Form-I (access) / Form-III (IPR) as applicable; execute benefit-sharing agreement.",
            "Escalate to an IP facilitator before drafting patent claims.",
        ],
    }
