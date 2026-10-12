"""
backend/api/schemas.py
----------------------
Hợp đồng (contract) giữa backend và frontend: request/response của REST API.
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


class SchemaResponse(BaseModel):
    tables: Dict[str, List[str]]


class ModelsResponse(BaseModel):
    models: List[str]
    default: str


class QueryRequest(BaseModel):
    question: str = Field(min_length=1)
    chart_hint: str = "auto"              # auto | bar chart | line chart | pie chart | scatter plot
    use_mock_sql: bool = False
    use_ollama: bool = True
    use_mock_viz: bool = False
    ollama_model: Optional[str] = None


class TableData(BaseModel):
    columns: List[str]
    rows: List[List[Any]]


class QueryResponse(BaseModel):
    status: Literal["ok", "sql_error", "empty_result", "viz_error"]
    question: str
    full_question: str
    # Stage 1
    sql: str
    sql_prompt: str
    sql_source: str
    # Stage 2
    sql_error: Optional[str] = None
    table: Optional[TableData] = None
    # Stage 3
    viz_code: Optional[str] = None
    viz_prompt: Optional[str] = None
    viz_source: Optional[str] = None
    viz_error: Optional[str] = None
    chart_png_base64: Optional[str] = None
