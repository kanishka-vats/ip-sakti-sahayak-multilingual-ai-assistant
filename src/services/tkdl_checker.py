"""TKDL prior-art pointer — heuristic screening for traditional formulations."""
from __future__ import annotations

import re

TKDL_INDEX: dict[str, list[str]] = {
    "turmeric": ["Curcuma longa — wound healing, anti-inflammatory (Ayurveda/Unani)"],
    "neem": ["Azadirachta indica — pesticide, skin disorders (Ayurveda/Unani)"],
    "ashwagandha": ["Withania somnifera — rasayana, stress (Ayurveda)"],
    "triphala": ["Amalaki + Bibhitaki + Haritaki — digestive (Ayurveda)"],
    "brahmi": ["Bacopa monnieri — cognition (Ayurveda)"],
    "tulsi": ["Ocimum tenuiflorum — respiratory (Ayurveda)"],
    "amla": ["Phyllanthus emblica — rejuvenative (Ayurveda/Siddha)"],
    "shatavari": ["Asparagus racemosus — reproductive health (Ayurveda)"],
    "giloy": ["Tinospora cordifolia — febrifuge (Ayurveda)"],
    "mulethi": ["Glycyrrhiza glabra — demulcent (Ayurveda/Unani)"],
    "haridra": ["Curcuma longa synonym — see turmeric"],
    "karela": ["Momordica charantia — antidiabetic (Ayurveda)"],
    "methi": ["Trigonella foenum-graecum — metabolic (Ayurveda/Unani)"],
    "ajwain": ["Trachyspermum ammi — carminative (Ayurveda/Unani)"],
    "hing": ["Ferula asafoetida — digestive (Ayurveda/Unani)"],
    "chavanprash": ["Polyherbal rasayana — immunity (Ayurveda)"],
}

_NOVELTY_RISK = re.compile(r"patent|novel|invent|claim|new (use|formulation|drug)", re.IGNORECASE)


def check_tkdl(query: str) -> dict | None:
    low = query.lower()
    hits = {k: v for k, v in TKDL_INDEX.items() if re.search(rf"\b{re.escape(k)}\b", low)}
    mentions_tk = bool(re.search(r"\btkdl\b|traditional knowledge|traditional medicine", low))
    if not hits and not mentions_tk:
        return None
    return {
        "applies": True,
        "matched_terms": sorted(hits) or (["tkdl-general"] if mentions_tk else []),
        "prior_art_notes": [f"{k}: {'; '.join(v)}" for k, v in hits.items()] or [
            "Query concerns TKDL / traditional knowledge: screen formulations against "
            "CSIR-TKDL prior art before asserting novelty."],
        "novelty_risk": bool(_NOVELTY_RISK.search(query)),
        "caution": ("TKDL-indexed prior art may defeat novelty under Patents Act Sec. 3(p). "
                    "TKDL is a prior-art pointer via CSIR/TKDL institutional access — "
                    "this app does not grant TKDL access."),
    }
