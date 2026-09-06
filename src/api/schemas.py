"""Request/response validation models (Pydantic v2)."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

Jurisdiction = Literal["india", "international", "dual", "auto"]


class QueryRequest(BaseModel):
    query: str = Field(min_length=2, max_length=4000)
    jurisdiction: Jurisdiction = "auto"
    top_k: int | None = Field(default=None, ge=1, le=20)
    # Conversational follow-up support: client sends the previous turn so
    # terse follow-ups ("explain it", "tell me more") resolve correctly.
    context_query: str | None = Field(default=None, max_length=2000)
    context_answer: str | None = Field(default=None, max_length=4000)
    # Browser-local display name for personalizing greetings (no auth).
    username: str | None = Field(default=None, max_length=30)


class Citation(BaseModel):
    index: int
    chunk_id: int
    act_name: str
    section: str
    doc_type: str = ""
    source_file: str = ""
    confidence: float = 0.0
    quote: str = ""
    source_label: str = ""
    verify_url: str = ""
    verify_label: str = ""


class QueryResponse(BaseModel):
    answer: str
    citations: list[Citation]
    confidence: float
    top_score: float
    jurisdiction: str
    abstained: bool = False
    abs_flag: dict[str, Any] | None = None
    tkdl_flag: dict[str, Any] | None = None
    model: str = ""
    clarification: bool = False
    suggestions: list[str] = Field(default_factory=list)


class IngestResponse(BaseModel):
    chunks_indexed: int
    india: int
    international: int
    files_scanned: int
    offline_embeddings: bool


class EscalateRequest(BaseModel):
    query: str = Field(min_length=3, max_length=4000)
    jurisdiction: Jurisdiction = "dual"
    contact: str = Field(default="", max_length=200)
    reason: str = Field(default="User requested human IP facilitator", max_length=2000)
    context: dict[str, Any] = Field(default_factory=dict)


class EscalateResponse(BaseModel):
    escalation_id: int
    summary: str


class FeedbackRequest(BaseModel):
    session_id: str = Field(default="", max_length=64)
    query: str = Field(min_length=1, max_length=2000)
    rating: Literal["up", "down"]


class FeedbackResponse(BaseModel):
    feedback_id: int
