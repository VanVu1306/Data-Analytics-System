"""
backend/api/schemas.py
----------------------
Hợp đồng (contract) giữa backend và frontend: request/response của REST API
(health, schema, query và dataset upload).
"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str
    db_exists: bool
    deepseek_configured: bool
    ollama_running: bool
    ollama_default_model: str
    max_upload_mb: int = 50


class RelationInfo(BaseModel):
    from_table: str
    from_column: str
    to_table: str
    to_column: Optional[str] = None


class RelationRequest(BaseModel):
    from_table: str = Field(min_length=1)
    from_column: str = Field(min_length=1)
    to_table: str = Field(min_length=1)
    to_column: str = Field(min_length=1)


class SchemaResponse(BaseModel):
    tables: Dict[str, List[str]]
    relations: List[RelationInfo] = Field(default_factory=list)


class ModelsResponse(BaseModel):
    models: List[str]
    default: str


# ── Dataset upload ─────────────────────────────────────────────────────────
class ColumnInfo(BaseModel):
    name: str
    type: str


class TableInfo(BaseModel):
    name: str  # tên bảng trong SQLite (đã chuẩn hóa)
    source_name: str  # tên sheet / tệp / bảng gốc
    row_count: int
    columns: List[ColumnInfo]


class DatasetResponse(BaseModel):
    dataset_id: str
    filename: str
    format: str  # csv | excel | sqlite
    size_bytes: int
    created_at: str
    status: str
    tables: List[TableInfo]
    relations: List[RelationInfo] = Field(default_factory=list)  # khóa ngoại khai báo sẵn
    confirmed_relations: List[RelationInfo] = Field(default_factory=list)  # người dùng xác nhận
    warnings: List[str] = Field(default_factory=list)


# ── Query ──────────────────────────────────────────────────────────────────
class QueryRequest(BaseModel):
    question: str = Field(min_length=1)
    dataset_id: Optional[str] = None      # None = demo DB; id sai -> 404 (không fallback)
    chart_hint: str = "auto"              # auto | bar chart | line chart | pie chart | scatter plot
    use_mock_sql: bool = False
    use_ollama: bool = True
    use_mock_viz: bool = False
    ollama_model: Optional[str] = None


class TableData(BaseModel):
    columns: List[str]
    rows: List[List[Any]]


class QueryResponse(BaseModel):
    status: Literal["ok", "sql_error", "empty_result", "viz_error", "needs_clarification"]
    question: str
    full_question: str
    # Stage 1
    sql: str
    sql_prompt: str
    sql_source: str
    clarification: Optional[str] = None
    # Stage 2
    sql_error: Optional[str] = None
    table: Optional[TableData] = None
    truncated: bool = False
    # Stage 3
    viz_code: Optional[str] = None
    viz_prompt: Optional[str] = None
    viz_source: Optional[str] = None
    viz_error: Optional[str] = None
    chart_png_base64: Optional[str] = None
