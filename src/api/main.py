"""FastAPI application with CORS and lifespan handlers."""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from config.settings import get_settings
from src.api.routes import router as api_router
from src.core.vector_store import VectorStore

ROOT = Path(__file__).resolve().parents[2]


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    store = VectorStore(s.db_path_abs)
    store.init()
    app.state.settings = s
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="IP-SAKTI Sahayak — Multilingual Legal/IP RAG",
        description="Split-jurisdiction IP RAG: Indian vs International law, ABS & TKDL aware.",
        version="1.0.0",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # Restrict to frontend origin in production
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(api_router, prefix="/api")

    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon():
        from fastapi.responses import Response

        svg = (
            "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'>"
            "<text y='.9em' font-size='90'>\u2696\ufe0f</text></svg>"
        )
        return Response(content=svg, media_type="image/svg+xml")

    @app.get("/health")
    async def health():
        s = get_settings()
        store = VectorStore(s.db_path_abs)
        try:
            counts = {"india": store.count("india"),
                      "international": store.count("international")}
        except Exception:
            counts = {"india": 0, "international": 0}
        return {"status": "ok", "models": s.model_dump_public(), "chunks": counts}

    frontend_dir = ROOT / "frontend"
    if frontend_dir.exists():
        app.mount("/", StaticFiles(directory=str(frontend_dir), html=True), name="frontend")
    return app


app = create_app()
