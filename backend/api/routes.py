"""
backend/api/routes.py
---------------------
REST endpoints. Lớp mỏng: nhận request -> gọi orchestrator/repo -> trả response.
Không chứa logic nghiệp vụ.
"""
from __future__ import annotations

import base64
import json

from fastapi import APIRouter, Depends, HTTPException

from backend.api.schemas import (
    HealthResponse,
    ModelsResponse,
    QueryRequest,
    QueryResponse,
    SchemaResponse,
    TableData,
)
from backend.container import Container, get_container
from backend.pipeline import PipelineRequest, PipelineResult

router = APIRouter(prefix="/api")

DB_MISSING_HINT = "Demo DB chưa tạo. Chạy: python scripts/create_demo_db.py"


def _require_db(container: Container) -> None:
    # Kiểm tra trước, vì sqlite3.connect() sẽ âm thầm tạo file DB rỗng nếu chưa có.
    if not container.repo.exists():
        raise HTTPException(status_code=503, detail=DB_MISSING_HINT)


def _to_response(result: PipelineResult) -> QueryResponse:
    table = None
    if result.df is not None:
        split = json.loads(result.df.to_json(orient="split", date_format="iso"))
        table = TableData(columns=[str(c) for c in split["columns"]], rows=split["data"])

    png_b64 = base64.b64encode(result.chart_png).decode("ascii") if result.chart_png else None

    return QueryResponse(
        status=result.status.value,
        question=result.question,
        full_question=result.full_question,
        sql=result.sql,
        sql_prompt=result.sql_prompt,
        sql_source=result.sql_source,
        sql_error=result.sql_error,
        table=table,
        viz_code=result.viz_code,
        viz_prompt=result.viz_prompt,
        viz_source=result.viz_source,
        viz_error=result.viz_error,
        chart_png_base64=png_b64,
    )


@router.get("/health", response_model=HealthResponse)
def health(container: Container = Depends(get_container)) -> HealthResponse:
    return HealthResponse(
        status="ok",
        db_exists=container.repo.exists(),
        deepseek_configured=container.settings.has_deepseek_key,
        ollama_running=container.ollama.is_available(),
        ollama_default_model=container.ollama.default_model,
    )


@router.get("/schema", response_model=SchemaResponse)
def schema(container: Container = Depends(get_container)) -> SchemaResponse:
    _require_db(container)
    try:
        return SchemaResponse(tables=container.repo.get_schema_dict())
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Cannot read DB: {e}")


@router.get("/models", response_model=ModelsResponse)
def models(container: Container = Depends(get_container)) -> ModelsResponse:
    return ModelsResponse(
        models=container.ollama.list_models(),
        default=container.ollama.default_model,
    )


@router.post("/query", response_model=QueryResponse)
def query(request: QueryRequest, container: Container = Depends(get_container)) -> QueryResponse:
    _require_db(container)
    result = container.orchestrator.run(PipelineRequest(**request.model_dump()))
    return _to_response(result)
