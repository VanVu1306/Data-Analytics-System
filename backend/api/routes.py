"""
backend/api/routes.py
---------------------
REST endpoints. Lớp mỏng: nhận request -> gọi orchestrator / DatasetService -> trả response.
Không chứa logic nghiệp vụ. Lỗi nghiệp vụ `DatasetError` được `dataset_error_handler` đổi thành HTTP.
"""
from __future__ import annotations

import base64
import dataclasses
import json
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import JSONResponse

from backend.api.schemas import (
    AnswerInfo,
    ChartMetaInfo,
    ChartResult,
    DatasetResponse,
    EdaResultInfo,
    Evidence,
    HealthResponse,
    ModelsResponse,
    PlanChartInfo,
    PlanInfo,
    PlanStepInfo,
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


def _table_data(df) -> TableData:
    split = json.loads(df.to_json(orient="split", date_format="iso"))
    return TableData(columns=[str(c) for c in split["columns"]], rows=split["data"])


def _eda_info(p) -> EdaResultInfo:
    return EdaResultInfo(
        operation=p.operation,
        table=p.table,
        columns=p.columns,
        status=p.status,
        error=p.error,
        summary=json.loads(json.dumps(p.summary, default=str)),
        table_data=_table_data(p.data) if p.data is not None else None,
        warnings=p.warnings,
        definitions=p.definitions,
        exact=p.exact,
        sampled=p.sampled,
        sample_info=p.sample_info,
        truncated=p.truncated,
        queries=p.queries,
        elapsed_ms=p.elapsed_ms,
        text=p.text,
    )


def _to_response(result: PipelineResult) -> QueryResponse:
    table = _table_data(result.df) if result.df is not None else None

    answer = (
        AnswerInfo(text=result.answer, source=result.answer_source or "template", fallback_reason=result.answer_fallback_reason)
        if result.answer
        else None
    )
    evidence = Evidence(
        sql=result.sql,
        sql_source=result.sql_source,
        sql_prompt=result.sql_prompt,
        sql_error=result.sql_error,
        show_sql=result.show_sql,
        row_count=len(result.df) if result.df is not None else None,
        column_count=len(result.df.columns) if result.df is not None else None,
        truncated=result.truncated,
        elapsed_ms=result.elapsed_ms,
    )
    chart = ChartResult(
        status=result.chart_status.value,
        requested_by=result.chart_origin,
        chart_type=result.chart_type,
        png_base64=base64.b64encode(result.chart_png).decode("ascii") if result.chart_png else None,
        error=result.chart_error,
        code=result.viz_code,
        prompt=result.viz_prompt,
        source=result.viz_source,
        meta=ChartMetaInfo(**dataclasses.asdict(result.chart_meta)) if result.chart_meta is not None else None,
    )
    plan = None
    if result.plan is not None:
        plan = PlanInfo(
            source=result.plan.source,
            steps=[PlanStepInfo(**s.to_dict()) for s in result.plan.steps],
            chart=PlanChartInfo(
                requested=result.plan.chart.requested,
                type=result.plan.chart.chart_type,
                goal=result.plan.chart.goal,
                origin=result.plan.chart.origin,
            ),
            chart_requested=result.plan.chart.requested,
            show_sql=result.plan.show_sql,
            fallback_reason=result.plan.fallback_reason,
        )
    return QueryResponse(
        status=result.status.value,
        question=result.question,
        answer=answer,
        clarification=result.clarification,
        table=table,
        evidence=evidence,
        chart=chart,
        plan=plan,
        eda=[_eda_info(p) for p in result.eda],
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
        viz_mode=container.settings.viz_mode,
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
