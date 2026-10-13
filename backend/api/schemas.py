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
    llm_provider: str
    llm_configured: bool
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
ChartHint = Literal["detect", "none", "auto", "bar chart", "line chart", "pie chart", "scatter plot"]


class QueryRequest(BaseModel):
    question: str = Field(min_length=1)
    dataset_id: Optional[str] = None      # None = demo DB; id sai -> 404 (không fallback)
    # Tùy chọn phụ về biểu đồ. Mặc định "detect": chỉ vẽ khi câu hỏi yêu cầu.
    # none = không vẽ; auto = vẽ và tự chọn loại; hoặc một loại cụ thể.
    chart_hint: ChartHint = "detect"
    show_sql: bool = False                # mở sẵn phần "Bằng chứng / SQL" (câu hỏi nhắc tới SQL cũng tự bật)
    use_mock_sql: bool = False            # không gọi cloud LLM (SQL mẫu + câu trả lời mẫu)
    use_ollama: bool = True
    use_mock_viz: bool = False
    ollama_model: Optional[str] = None


class TableData(BaseModel):
    columns: List[str]
    rows: List[List[Any]]


class AnswerInfo(BaseModel):
    """Câu trả lời text cho người dùng, chỉ dựa trên kết quả truy vấn."""

    text: str
    source: str                           # tên provider LLM | "template"
    fallback_reason: Optional[str] = None  # vì sao dùng câu mẫu thay vì câu của LLM


class Evidence(BaseModel):
    """Bằng chứng: SQL đã thực thi + thông tin thực thi (không thay thế câu trả lời)."""

    sql: str
    sql_source: str
    sql_prompt: str
    sql_error: Optional[str] = None
    show_sql: bool = False                # gợi ý frontend mở sẵn phần bằng chứng
    row_count: Optional[int] = None
    column_count: Optional[int] = None
    truncated: bool = False
    elapsed_ms: Optional[int] = None


class ChartResult(BaseModel):
    """Kết quả nhánh biểu đồ, tách riêng khỏi truy vấn: lỗi ở đây không ảnh hưởng answer/table."""

    status: Literal["not_requested", "ok", "error"] = "not_requested"
    requested_by: Literal["none", "option", "prompt"] = "none"
    chart_type: Optional[str] = None      # None = tự chọn
    png_base64: Optional[str] = None
    error: Optional[str] = None
    code: Optional[str] = None
    prompt: Optional[str] = None
    source: Optional[str] = None


class QueryResponse(BaseModel):
    # Trạng thái của phần truy vấn/trả lời. Trạng thái biểu đồ nằm ở `chart.status`.
    status: Literal["ok", "sql_error", "empty_result", "needs_clarification"]
    question: str
    answer: Optional[AnswerInfo] = None   # có khi truy vấn thành công (kể cả kết quả rỗng)
    clarification: Optional[str] = None
    table: Optional[TableData] = None
    evidence: Evidence
    chart: ChartResult
