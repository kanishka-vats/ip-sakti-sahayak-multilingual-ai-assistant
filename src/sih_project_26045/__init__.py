"""IP-SAKTI Sahayak — package entrypoint (SIH 26045).

Run the server with:  uv run sih-sahayak
(or directly:         uv run uvicorn src.api.main:app --port 8000)
"""
from __future__ import annotations

__version__ = "1.0.0"


def main() -> None:
    import uvicorn

    uvicorn.run("src.api.main:app", host="127.0.0.1", port=8000)
