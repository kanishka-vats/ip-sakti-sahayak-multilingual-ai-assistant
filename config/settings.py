"""Pydantic BaseSettings loading .env + config/models.yaml.

Never hardcode model names in application logic — import `get_settings()`.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[1]
MODELS_YAML = ROOT / "config" / "models.yaml"

# Legacy aliases resolved against the live Groq model list (verified 2026-09-05).
LLM_ALIASES: dict[str, str] = {}


def _load_yaml(path: Path) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(ROOT / ".env"), extra="allow")

    GROQ_API_KEY: str = Field(default="", description="Groq API key")
    OPENROUTER_API_KEY: str = Field(default="", description="OpenRouter API key")
    TURSO_DATABASE_URL: str = Field(default="", description="Turso libsql URL (optional; falls back to local sqlite)")
    TURSO_AUTH_TOKEN: str = Field(default="", description="Turso auth token (optional)")

    DB_PATH: str = Field(default="db/sih-projdb.db")

    # Resolved from models.yaml (populated post-init)
    llm_provider: str = "groq"
    llm_model: str = "qwen/qwen3-32b"
    llm_temperature: float = 0.1
    llm_max_tokens: int = 1000
    llm_fallbacks: list[str] = Field(default_factory=list)
    embedding_provider: str = "openrouter"
    embedding_model: str = "openai/text-embedding-3-small"
    embedding_dim: int = 384
    embedding_batch_size: int = 32
    embedding_dimensions_param: int | None = None
    embedding_max_input_tokens: int = 512
    top_k: int = 5
    dual_top_k: int = 4
    confidence_threshold: float = 0.65
    disclaimer: str = "Information only; not legal advice. Aligned with DPDP Act & Indian Patent Office rules."
    abstention_message: str = "Insufficient verified statutory basis found in the indexed corpus to answer this query safely."

    def load_models_yaml(self) -> None:
        if not MODELS_YAML.exists():
            return
        data = _load_yaml(MODELS_YAML)
        models = data.get("models", {})
        llm = models.get("llm", {})
        emb = models.get("embedding", {})
        ret = data.get("retrieval", {})
        grd = data.get("guardrails", {})
        raw_name = str(llm.get("name", self.llm_model))
        self.llm_provider = str(llm.get("provider", self.llm_provider))
        self.llm_model = LLM_ALIASES.get(raw_name, raw_name)
        self.llm_temperature = float(llm.get("temperature", self.llm_temperature))
        self.llm_max_tokens = int(llm.get("max_tokens", self.llm_max_tokens))
        self.llm_fallbacks = [LLM_ALIASES.get(m, m) for m in llm.get("fallback", [])]
        self.embedding_provider = str(emb.get("provider", self.embedding_provider))
        self.embedding_model = str(emb.get("name", self.embedding_model))
        self.embedding_dim = int(emb.get("dimension", self.embedding_dim))
        self.embedding_batch_size = int(emb.get("batch_size", self.embedding_batch_size))
        self.embedding_dimensions_param = emb.get("dimensions_param")
        self.embedding_max_input_tokens = int(emb.get("max_input_tokens", self.embedding_max_input_tokens))
        self.top_k = int(ret.get("top_k", self.top_k))
        self.dual_top_k = int(ret.get("dual_top_k", self.dual_top_k))
        self.confidence_threshold = float(ret.get("confidence_threshold", self.confidence_threshold))
        if grd.get("disclaimer"):
            self.disclaimer = str(grd["disclaimer"])
        if grd.get("abstention_message"):
            self.abstention_message = str(grd["abstention_message"])

    @property
    def groq_key(self) -> str:
        return (self.GROQ_API_KEY or os.getenv("GROQ_API_KEY", "")).strip().strip('"').strip("'")

    @property
    def openrouter_key(self) -> str:
        return (self.OPENROUTER_API_KEY or os.getenv("OPENROUTER_API_KEY", "")).strip().strip('"').strip("'")

    @property
    def db_path_abs(self) -> Path:
        p = Path(self.DB_PATH)
        return p if p.is_absolute() else (ROOT / p)

    def model_dump_public(self) -> dict[str, Any]:
        return {
            "llm": {"provider": self.llm_provider, "name": self.llm_model,
                    "temperature": self.llm_temperature, "max_tokens": self.llm_max_tokens,
                    "fallbacks": self.llm_fallbacks},
            "embedding": {"provider": self.embedding_provider, "name": self.embedding_model,
                          "dimension": self.embedding_dim},
            "retrieval": {"top_k": self.top_k, "confidence_threshold": self.confidence_threshold},
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    s = Settings()
    s.load_models_yaml()
    return s


Jurisdiction = Literal["india", "international", "dual"]
