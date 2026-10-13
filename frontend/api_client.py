"""
frontend/api_client.py
----------------------
Client HTTP gọi backend NL2Viz. Mọi giao tiếp với backend đi qua file này.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
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


@dataclass
class Schema:
    tables: Dict[str, List[str]]
    relations: List[Dict[str, Any]]


@dataclass
class QueryResult:
    status: str
    question: str
    full_question: str
    sql: str
    sql_prompt: str
    sql_source: str
    sql_error: Optional[str]
    df: Optional[pd.DataFrame]
    viz_code: Optional[str]
    viz_prompt: Optional[str]
    viz_source: Optional[str]
    viz_error: Optional[str]
    chart_png: Optional[bytes]
    clarification: Optional[str] = None
    truncated: bool = False


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
        chart_hint: str = "auto",
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
            "use_mock_sql": use_mock_sql,
            "use_ollama": use_ollama,
            "use_mock_viz": use_mock_viz,
            "ollama_model": ollama_model,
        }
        d = self._request("POST", "/api/query", QUERY_TIMEOUT, json=payload)

        df = None
        if d.get("table") is not None:
            df = pd.DataFrame(d["table"]["rows"], columns=d["table"]["columns"])
        png = base64.b64decode(d["chart_png_base64"]) if d.get("chart_png_base64") else None

        return QueryResult(
            status=d["status"],
            question=d["question"],
            full_question=d["full_question"],
            sql=d["sql"],
            sql_prompt=d["sql_prompt"],
            sql_source=d["sql_source"],
            sql_error=d.get("sql_error"),
            df=df,
            viz_code=d.get("viz_code"),
            viz_prompt=d.get("viz_prompt"),
            viz_source=d.get("viz_source"),
            viz_error=d.get("viz_error"),
            chart_png=png,
            clarification=d.get("clarification"),
            truncated=d.get("truncated", False),
        )
