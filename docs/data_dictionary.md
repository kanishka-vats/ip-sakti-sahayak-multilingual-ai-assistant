# Data Dictionary — Corpus Taxonomy

| Field | Values / format | Notes |
|---|---|---|
| jurisdiction | `india` \| `international` | From folder: `raw_data/national` → india, `raw_data/international` → international |
| source_file | relative path e.g. `raw_data/national/corpus/Pdfs/The Biological Diversity Act...pdf` | |
| doc_name | file stem, spaces | human title |
| doc_type | `patent_statute`, `trademark_statute`, `copyright_statute`, `design_statute`, `gi_statute`, `biodiversity_statute`, `drug_regulation`, `tkdl_reference`, `treaty`, `ayush_dataset`, `survey_dataset`, `gi_registry`, `structured_dataset`, `statute_or_guideline` | keyword-guessed |
| section_id | `Section 3` / `Article 27` / `Rule 170` / `preamble` / `full-document` | regex header split |
| chunk_index | int per source_file | |
| text | clause chunk 400–800 tok | 10% overlap |
| embedding | JSON float[384] | OpenRouter or offline hash |

## National (india) holdings
Patents Act/Rules + Manual, Trade Marks Act/Rules, Copyright Act/Rules, Designs Act/Rules,
GI Act/records, Biological Diversity Act/Rules, Drugs & Cosmetics + AYUSH orders, Consumer
Protection 2019, Food Safety standards, TKDL/Ayurvedic pharmacopoeia refs, NSS AYUSH surveys.

## International holdings
PCT, TRIPS, CBD + Nagoya Protocol, Hague/Lisbon systems, WIPO GRTKF docs, SPS/TBT notes,
foreign herbal/regulatory guidance (comparative only — never cited for Indian advice).
