"""
backend/api/routes.py
---------------------
REST endpoints. Lớp mỏng: nhận request -> gọi orchestrator / DatasetService -> trả response.
Không chứa logic nghiệp vụ. Lỗi nghiệp vụ `DatasetError` được `dataset_error_handler` đổi thành HTTP.
"""
from __future__ import annotations

import base64
import json
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import JSONResponse

from backend.api.schemas import (
    DatasetResponse,
    HealthResponse,
    ModelsResponse,
    QueryRequest,
    QueryResponse,
    RelationRequest,
    SchemaResponse,
    TableData,
)
from backend.container import Container, get_container
from backend.errors import DatasetError, FileTooLargeError
from backend.pipeline import PipelineRequest, PipelineResult

router = APIRouter(prefix="/api")

DB_MISSING_HINT = "Demo DB chưa tạo. Chạy: python scripts/create_demo_db.py"
_STATUS = {
    "unsupported_format": 415,
    "file_too_large": 413,
    "invalid_file": 422,
    "unsafe_content": 422,
    "import_failed": 422,
    "invalid_relation": 422,
    "dataset_not_found": 404,
    "relation_not_found": 404,
}
_CHUNK = 1024 * 1024


def dataset_error_handler(_: Request, exc: DatasetError) -> JSONResponse:
    """Đăng ký trong `main.create_app`: detail = {code, message} ổn định cho frontend."""
    return JSONResponse(
        status_code=_STATUS.get(exc.code, 400),
        content={"detail": {"code": exc.code, "message": str(exc)}},
    )


def _require_default_db(container: Container) -> None:
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
        clarification=result.clarification,
        sql_error=result.sql_error,
        table=table,
        truncated=result.truncated,
        viz_code=result.viz_code,
        viz_prompt=result.viz_prompt,
        viz_source=result.viz_source,
        viz_error=result.viz_error,
        chart_png_base64=png_b64,
    )


# ── Hệ thống ───────────────────────────────────────────────────────────────
@router.get("/health", response_model=HealthResponse)
def health(container: Container = Depends(get_container)) -> HealthResponse:
    return HealthResponse(
        status="ok",
        db_exists=container.repo.exists(),
        llm_provider=container.cloud_llm.name,
        llm_configured=container.cloud_llm.is_available(),
        ollama_running=container.ollama.is_available(),
        ollama_default_model=container.ollama.default_model,
        max_upload_mb=container.settings.max_upload_mb,
    )


@router.get("/models", response_model=ModelsResponse)
def models(container: Container = Depends(get_container)) -> ModelsResponse:
    return ModelsResponse(
        models=container.ollama.list_models(),
        default=container.ollama.default_model,
    )


# ── Dataset ────────────────────────────────────────────────────────────────
@router.post("/datasets", response_model=DatasetResponse, status_code=201)
def upload_dataset(file: UploadFile = File(...), container: Container = Depends(get_container)) -> dict:
    # `def` (không async): import chạy trong threadpool, không chặn event loop.
    service = container.dataset_service
    content = bytearray()
    while chunk := file.file.read(_CHUNK):
        content.extend(chunk)
        if len(content) > service.max_upload_bytes:  # dừng đọc sớm, không nuốt cả tệp lớn
            limit_mb = service.max_upload_bytes / (1024 * 1024)
            raise FileTooLargeError(f"Tệp vượt giới hạn {limit_mb:g} MB.")
    return service.upload(file.filename or "", bytes(content))


@router.get("/datasets/{dataset_id}", response_model=DatasetResponse)
def get_dataset(dataset_id: str, container: Container = Depends(get_container)) -> dict:
    return container.dataset_service.get(dataset_id)


@router.post("/datasets/{dataset_id}/relations", response_model=DatasetResponse)
def add_relation(dataset_id: str, body: RelationRequest, container: Container = Depends(get_container)) -> dict:
    return container.dataset_service.link_tables(dataset_id, **body.model_dump())


@router.delete("/datasets/{dataset_id}/relations", response_model=DatasetResponse)
def remove_relation(dataset_id: str, body: RelationRequest, container: Container = Depends(get_container)) -> dict:
    return container.dataset_service.unlink_tables(dataset_id, **body.model_dump())


# ── Schema & truy vấn ──────────────────────────────────────────────────────
@router.get("/schema", response_model=SchemaResponse)
def schema(
    dataset_id: Optional[str] = Query(default=None),
    container: Container = Depends(get_container),
) -> SchemaResponse:
    if dataset_id is None:
        _require_default_db(container)
    try:
        return SchemaResponse(**container.dataset_service.schema(dataset_id))
    except DatasetError:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Cannot read DB: {e}")


@router.post("/query", response_model=QueryResponse)
def query(request: QueryRequest, container: Container = Depends(get_container)) -> QueryResponse:
    if request.dataset_id is None:
        _require_default_db(container)
    result = container.orchestrator.run(PipelineRequest(**request.model_dump()))
    return _to_response(result)
