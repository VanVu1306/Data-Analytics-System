"""
frontend/api_client.py
----------------------
Client HTTP gọi backend NL2Viz. Mọi giao tiếp với backend đi qua file này.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import pandas as pd
import requests

from frontend.config import API_BASE_URL, HEALTH_TIMEOUT, QUERY_TIMEOUT, UPLOAD_TIMEOUT


class APIError(RuntimeError):
    """Backend không truy cập được hoặc trả lỗi."""


@dataclass
class Health:
    max_upload_mb: int
    db_exists: bool
    llm_provider: str
    llm_configured: bool
    ollama_running: bool
    ollama_default_model: str
    viz_mode: str = "spec"                # spec | code


@dataclass
class Schema:
    tables: Dict[str, List[str]]
    relations: List[Dict[str, Any]]


@dataclass
class Answer:
    text: str
    source: str                           # tên provider LLM | "template"
    fallback_reason: Optional[str] = None


@dataclass
class Evidence:
    """Bằng chứng: SQL đã thực thi + thông tin thực thi."""

    sql: str
    sql_source: str
    sql_prompt: str
    sql_error: Optional[str] = None
    show_sql: bool = False
    row_count: Optional[int] = None
    column_count: Optional[int] = None
    truncated: bool = False
    elapsed_ms: Optional[int] = None


@dataclass
class Chart:
    """Nhánh biểu đồ, tách riêng khỏi truy vấn."""

    status: str = "not_requested"         # not_requested | ok | error
    requested_by: str = "none"            # none | option | prompt
    chart_type: Optional[str] = None
    png: Optional[bytes] = None
    error: Optional[str] = None
    code: Optional[str] = None
    prompt: Optional[str] = None
    source: Optional[str] = None
    meta: Optional[Dict[str, Any]] = None  # nguồn dữ liệu, số dòng gốc/đã dùng, phương pháp, cảnh báo (chế độ spec)


@dataclass
class PlanStep:
    operation: str                        # query | table_overview | data_quality | numeric_summary | ...
    table: Optional[str] = None
    columns: List[str] = field(default_factory=list)
    params: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Plan:
    """Kế hoạch xử lý mà hệ thống đã thực hiện."""

    source: str                           # rules | llm | rules_fallback
    steps: List[PlanStep] = field(default_factory=list)
    chart_requested: bool = False
    show_sql: bool = False


@dataclass
class EdaResult:
    """Kết quả một operation EDA: bảng + số liệu có cấu trúc + cảnh báo + thông tin lấy mẫu."""

    operation: str
    table: str
    columns: List[str] = field(default_factory=list)
    status: str = "ok"                    # ok | not_applicable | error
    error: Optional[str] = None
    summary: Dict[str, Any] = field(default_factory=dict)
    df: Optional[pd.DataFrame] = None
    warnings: List[str] = field(default_factory=list)
    definitions: List[str] = field(default_factory=list)
    exact: bool = True
    sampled: bool = False
    sample_info: Optional[Dict[str, Any]] = None
    truncated: bool = False
    queries: List[str] = field(default_factory=list)
    elapsed_ms: int = 0
    text: Optional[str] = None


@dataclass
class QueryResult:
    status: str                           # ok | sql_error | empty_result | needs_clarification | eda_error
    question: str
    evidence: Evidence
    chart: Chart
    answer: Optional[Answer] = None
    clarification: Optional[str] = None
    df: Optional[pd.DataFrame] = None
    plan: Optional[Plan] = None
    eda: List[EdaResult] = field(default_factory=list)


class NL2VizClient:
    def __init__(self, base_url: str = API_BASE_URL):
        self._base = base_url.rstrip("/")

    # ── low-level ──────────────────────────────────────────────────────────
    def _request(self, method: str, path: str, timeout: int, **kwargs) -> Dict[str, Any]:
        url = f"{self._base}{path}"
        try:
            r = requests.request(method, url, timeout=timeout, **kwargs)
        except requests.exceptions.ConnectionError:
            raise APIError(
                f"Không kết nối được backend tại {self._base}. "
                "Hãy chạy: uvicorn backend.main:app --host 127.0.0.1 --port 8000"
            )
        except requests.exceptions.Timeout:
            raise APIError(f"Backend không phản hồi sau {timeout}s ({path}).")
        if r.status_code >= 400:
            try:
                detail = r.json().get("detail", r.text)
            except ValueError:
                detail = r.text
            if isinstance(detail, dict):  # {"code", "message"} từ lỗi nghiệp vụ dataset
                detail = detail.get("message", detail)
            elif isinstance(detail, list):  # lỗi validate 422 của FastAPI
                detail = "; ".join(str(d.get("msg", d)) for d in detail)
            raise APIError(f"{detail}")
        return r.json()

    # ── endpoints ──────────────────────────────────────────────────────────
    def health(self) -> Health:
        d = self._request("GET", "/api/health", HEALTH_TIMEOUT)
        return Health(
            max_upload_mb=d.get("max_upload_mb", 50),
            db_exists=d["db_exists"],
            llm_provider=d["llm_provider"],
            llm_configured=d["llm_configured"],
            ollama_running=d["ollama_running"],
            ollama_default_model=d["ollama_default_model"],
            viz_mode=d.get("viz_mode", "spec"),
        )

    def schema(self, dataset_id: Optional[str] = None) -> Schema:
        params = {"dataset_id": dataset_id} if dataset_id else None
        d = self._request("GET", "/api/schema", HEALTH_TIMEOUT, params=params)
        return Schema(tables=d["tables"], relations=d.get("relations", []))

    # ── dataset upload ─────────────────────────────────────────────────────
    def upload_dataset(self, filename: str, content: bytes) -> Dict[str, Any]:
        files = {"file": (filename, content, "application/octet-stream")}
        return self._request("POST", "/api/datasets", UPLOAD_TIMEOUT, files=files)

    def get_dataset(self, dataset_id: str) -> Dict[str, Any]:
        return self._request("GET", f"/api/datasets/{dataset_id}", HEALTH_TIMEOUT)

    def add_relation(self, dataset_id: str, relation: Dict[str, str]) -> Dict[str, Any]:
        return self._request("POST", f"/api/datasets/{dataset_id}/relations", HEALTH_TIMEOUT, json=relation)

    def remove_relation(self, dataset_id: str, relation: Dict[str, str]) -> Dict[str, Any]:
        return self._request("DELETE", f"/api/datasets/{dataset_id}/relations", HEALTH_TIMEOUT, json=relation)

    def models(self) -> List[str]:
        return self._request("GET", "/api/models", HEALTH_TIMEOUT)["models"]

    def query(
        self,
        question: str,
        *,
        chart_hint: str = "detect",
        show_sql: bool = False,
        use_mock_sql: bool = False,
        use_ollama: bool = True,
        use_mock_viz: bool = False,
        ollama_model: Optional[str] = None,
        dataset_id: Optional[str] = None,
    ) -> QueryResult:
        payload = {
            "question": question,
            "dataset_id": dataset_id,
            "chart_hint": chart_hint,
            "show_sql": show_sql,
            "use_mock_sql": use_mock_sql,
            "use_ollama": use_ollama,
            "use_mock_viz": use_mock_viz,
            "ollama_model": ollama_model,
        }
        d = self._request("POST", "/api/query", QUERY_TIMEOUT, json=payload)

        df = None
        if d.get("table") is not None:
            df = pd.DataFrame(d["table"]["rows"], columns=d["table"]["columns"])

        ev, ch = d["evidence"], d["chart"]
        plan = None
        if d.get("plan"):
            pl = d["plan"]
            plan = Plan(
                source=pl["source"],
                steps=[PlanStep(**st) for st in pl["steps"]],
                chart_requested=pl.get("chart_requested", False),
                show_sql=pl.get("show_sql", False),
            )
        eda = []
        for e in d.get("eda", []):
            td = e.get("table_data")
            eda.append(EdaResult(
                operation=e["operation"], table=e["table"], columns=e.get("columns", []), status=e["status"],
                error=e.get("error"), summary=e.get("summary", {}),
                df=pd.DataFrame(td["rows"], columns=td["columns"]) if td else None,
                warnings=e.get("warnings", []), definitions=e.get("definitions", []), exact=e.get("exact", True),
                sampled=e.get("sampled", False), sample_info=e.get("sample_info"), truncated=e.get("truncated", False),
                queries=e.get("queries", []), elapsed_ms=e.get("elapsed_ms", 0), text=e.get("text"),
            ))
        return QueryResult(
            plan=plan,
            eda=eda,
            status=d["status"],
            question=d["question"],
            answer=Answer(**d["answer"]) if d.get("answer") else None,
            clarification=d.get("clarification"),
            df=df,
            evidence=Evidence(**ev),
            chart=Chart(
                status=ch["status"],
                requested_by=ch["requested_by"],
                chart_type=ch.get("chart_type"),
                png=base64.b64decode(ch["png_base64"]) if ch.get("png_base64") else None,
                error=ch.get("error"),
                code=ch.get("code"),
                prompt=ch.get("prompt"),
                source=ch.get("source"),
                meta=ch.get("meta"),
            ),
        )
