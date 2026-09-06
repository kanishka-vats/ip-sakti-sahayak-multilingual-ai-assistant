# Project Context — IP / Legal RAG Domain Rules

## IP types covered
- **Patents** (India: Patents Act 1970 + Patents Rules; Intl: PCT, TRIPS Art. 27–34).
- **Trademarks** (Trade Marks Act 1999; Intl: Madrid/Hague notes where indexed).
- **Copyright** (Copyright Act 1957), **Designs** (Designs Act 2000),
  **GI** (Geographical Indications Act 1999), **TKDL / Traditional Knowledge**.

## ABS (Access & Benefit Sharing) — Biological Diversity Act, 2002
- **Section 3:** Prior NBA approval for foreign persons/entities accessing Indian
  biological resources / associated knowledge for research, commercial use, survey.
- **Section 4:** No transfer of research results to foreigners without NBA approval.
- **Section 6:** Prior NBA approval before applying for IPR on inventions based on
  Indian biological resources (exemptions: Plant Varieties Act route).
- **NBA = National Biodiversity Authority (Chennai).** Route queries mentioning
  Indian plants, formulations, extracts, bio-resources → `abs_compliance.py`.
- Always flag: approval requirement, Form-I / benefit-sharing agreement pointer,
  and escalation to IP facilitator for high-risk filings.

## TKDL (Traditional Knowledge Digital Library) guidelines
- TKDL is a **prior-art pointer, not a grant of rights**. If a query formulation
  matches Ayurveda/Unani/Siddha ingredients (turmeric, neem, ashwagandha, triphala,
  …), surface a TKDL prior-art caution: novelty risk under Patents Act Sec. 3(p).
- Heuristic keyword screen lives in `src/services/tkdl_checker.py`; full TKDL
  access requires CSIR/TKDL institutional route — the app only *points*.

## Citation format
- Machine: `[^N]` inline + `citations[N] = {chunk_id, act_name, section, quote, confidence, source_file}`.
- Human fallback: `[Source: <DocName>, Sec. <X>]`.
- Quotes capped at 600 chars, verbatim from chunk text.

## DPDP Act (Digital Personal Data Protection Act, 2023) notes
- Every response header carries the disclaimer (info only, not legal advice).
- Audit log (`queries_audit`) records query + jurisdiction + model + latency;
  user contact on escalation stored minimally, consented via modal checkbox.
- No cross-user data leakage; no training on user queries.

## Jurisdiction routing criteria
- Keywords `patent office india, section 3(d), biodiversity, ayush, nba` → india.
- Keywords `pct, trips, epo, uspto, wipo, hague, madrid` → international.
- Both present or explicit toggle → dual (isolated parallel reasoning + comparison table).
- Default when ambiguous: dual.
