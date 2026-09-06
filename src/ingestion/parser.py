"""Robust PDF extractor with hierarchy/section preservation.

Scans ./raw_data/national and ./raw_data/international (recursively, so
corpus/Pdfs, structured/, etc. are all covered). Extracts clean text via pypdf,
detects section headers, and emits Document records with metadata:
jurisdiction, source_file, doc_type, section_id.
"""
from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

try:
    from pypdf import PdfReader
except ImportError:  # pragma: no cover
    PdfReader = None  # type: ignore

ROOT = Path(__file__).resolve().parents[2]
RAW_NATIONAL = ROOT / "raw_data" / "national"
RAW_INTERNATIONAL = ROOT / "raw_data" / "international"

SECTION_PATTERNS = [
    re.compile(r"^\s*(section\s+\d+[A-Z]?(?:\s*\([^)]*\))?)\b[:.\-–—]?\s*(.*)$", re.IGNORECASE),
    re.compile(r"^\s*(article\s+\d+[A-Z]?)\b[:.\-–—]?\s*(.*)$", re.IGNORECASE),
    re.compile(r"^\s*(rule\s+\d+[A-Z]?)\b[:.\-–—]?\s*(.*)$", re.IGNORECASE),
    re.compile(r"^\s*(chapter\s+[IVXLC\d]+)\b[:.\-–—]?\s*(.*)$", re.IGNORECASE),
    re.compile(r"^\s*(\d{1,3}\.\d{1,3}(?:\.\d{1,3})?)\s+(.{4,120})$"),
]

DOC_TYPE_HINTS = {
    "patent": "patent_statute",
    "copyright": "copyright_statute",
    "trademark": "trademark_statute",
    "trade mark": "trademark_statute",
    "design": "design_statute",
    "geographical": "gi_statute",
    "biodiversity": "biodiversity_statute",
    "biological diversity": "biodiversity_statute",
    "drug": "drug_regulation",
    "cosmetic": "drug_regulation",
    "ayurv": "tkdl_reference",
    "tkdl": "tkdl_reference",
    "trips": "treaty",
    "pct": "treaty",
    "wipo": "treaty",
    "hague": "treaty",
    "nagoya": "treaty",
    "cbd": "treaty",
    "lisbon": "treaty",
    "sps": "treaty",
    "ayush": "ayush_dataset",
    "nss": "survey_dataset",
    "gi ": "gi_registry",
}


@dataclass
class Document:
    text: str
    jurisdiction: str  # india | international
    source_file: str
    doc_type: str
    section_id: str = "full-document"
    doc_name: str = ""
    page_count: int = 0
    extra: dict = field(default_factory=dict)


def guess_doc_type(filename: str) -> str:
    low = filename.lower()
    for hint, dtype in DOC_TYPE_HINTS.items():
        if hint in low:
            return dtype
    if filename.lower().endswith((".xlsx", ".csv", ".json", ".docx")):
        return "structured_dataset"
    return "statute_or_guideline"


def clean_text(raw: str) -> str:
    raw = raw.replace("\r\n", "\n").replace("\r", "\n")
    raw = re.sub(r"[ \t]+", " ", raw)
    raw = re.sub(r"\n{3,}", "\n\n", raw)
    # drop bare page-number lines
    lines = [ln.strip() for ln in raw.split("\n")]
    kept = [ln for ln in lines if not re.fullmatch(r"\d{1,4}", ln or " ")]
    return "\n".join(kept).strip()


def detect_sections(text: str) -> list[tuple[str, str]]:
    """Split text at detected section headers. Returns [(section_id, section_text)]."""
    lines = text.split("\n")
    chunks: list[tuple[str, list[str]]] = []
    current_id = "preamble"
    current: list[str] = []
    for ln in lines:
        hit = None
        for pat in SECTION_PATTERNS:
            m = pat.match(ln.strip())
            if m:
                hit = m.group(1).strip()
                break
        if hit and len(current) > 3:
            chunks.append((current_id, current))
            current_id = re.sub(r"\s+", " ", hit)[:120]
            current = [ln]
        else:
            current.append(ln)
    if current:
        chunks.append((current_id, current))
    return [(sid, "\n".join(body).strip()) for sid, body in chunks if "\n".join(body).strip()]


def extract_pdf(path: Path, jurisdiction: str) -> list[Document]:
    if PdfReader is None:
        raise RuntimeError("pypdf is not installed. Run `uv sync`.")
    reader = PdfReader(str(path))
    pages = []
    for page in reader.pages:
        try:
            pages.append(page.extract_text() or "")
        except Exception:
            pages.append("")
    full = clean_text("\n\n".join(pages))
    if not full:
        return []
    doc_name = path.stem.replace("_", " ").strip()
    dtype = guess_doc_type(path.name)
    out: list[Document] = []
    for sid, body in detect_sections(full):
        if len(body) < 50:
            continue
        out.append(Document(
            text=body, jurisdiction=jurisdiction, source_file=str(path.relative_to(ROOT)),
            doc_type=dtype, section_id=sid, doc_name=doc_name, page_count=len(reader.pages),
        ))
    if not out:
        out.append(Document(text=full, jurisdiction=jurisdiction,
                            source_file=str(path.relative_to(ROOT)), doc_type=dtype,
                            doc_name=doc_name, page_count=len(reader.pages)))
    return out


def extract_text_file(path: Path, jurisdiction: str) -> list[Document]:
    try:
        raw = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return []
    if path.suffix.lower() == ".json":
        try:
            data = json.loads(raw)
            raw = json.dumps(data, ensure_ascii=False)[:20000]
        except Exception:
            pass
    full = clean_text(raw)
    if not full:
        return []
    return [Document(text=full[:12000], jurisdiction=jurisdiction,
                     source_file=str(path.relative_to(ROOT)), doc_type=guess_doc_type(path.name),
                     doc_name=path.stem)]


def scan_corpus(root: Path | None = None) -> list[Document]:
    """Scan both jurisdiction trees. Returns section-level Documents."""
    base = root or ROOT
    docs: list[Document] = []
    for jurisdiction, folder in (("india", base / "raw_data" / "national"),
                                 ("international", base / "raw_data" / "international")):
        if not folder.exists():
            continue
        for path in sorted(folder.rglob("*")):
            if not path.is_file() or path.name.startswith("."):
                continue
            suf = path.suffix.lower()
            try:
                if suf == ".pdf":
                    docs.extend(extract_pdf(path, jurisdiction))
                elif suf in (".txt", ".md", ".csv", ".json"):
                    # cap CSVs to avoid giant rows
                    if suf == ".csv":
                        rows = _csv_preview(path)
                        if rows:
                            docs.append(Document(text=rows, jurisdiction=jurisdiction,
                                                source_file=str(path.relative_to(base)),
                                                doc_type="structured_dataset",
                                                doc_name=path.stem))
                    else:
                        docs.extend(extract_text_file(path, jurisdiction))
            except Exception:
                continue
    return docs


def _csv_preview(path: Path, max_rows: int = 60) -> str:
    try:
        with open(path, newline="", encoding="utf-8", errors="ignore") as f:
            reader = csv.reader(f)
            rows = [", ".join(r) for _, r in zip(range(max_rows), reader)]
        return clean_text("\n".join(rows))[:12000]
    except Exception:
        return ""
