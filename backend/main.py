"""
backend/main.py
---------------
Entrypoint FastAPI.

Chạy (chỉ bind localhost cho tới khi có sandbox thật cho code do LLM sinh ra):
    uvicorn backend.main:app --host 127.0.0.1 --port 8000
"""
from fastapi import FastAPI

from backend.api.routes import router


def create_app() -> FastAPI:
    app = FastAPI(
        title="NL2Viz API",
        description="Natural Language -> SQL -> DataFrame -> Chart",
        version="0.1.0",
    )
    app.include_router(router)
    return app


app = create_app()
