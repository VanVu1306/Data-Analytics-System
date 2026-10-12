"""
backend/pipeline/orchestrator.py
--------------------------------
Orchestrator điều phối toàn bộ pipeline, quyết định thứ tự và điều kiện dừng:

    Stage 1  SQLAgent.generate        câu hỏi  -> SQL (hoặc yêu cầu làm rõ)
    Stage 2  tool `execute_sql`       SQL      -> DataFrame (chỉ đọc, có giới hạn)
    Stage 3  VizAgent.generate_code   df + câu hỏi -> code matplotlib
             tool `render_chart`      code     -> PNG

Mọi stage chạy trên dataset do `dataset_id` chọn (None = demo DB); id không tồn tại -> DatasetNotFoundError,
không fallback sang database khác.

Orchestrator không biết chi tiết LLM/DB; nó chỉ nối agents và tools.

File này cũng chứa `DatasetService`: điểm vào cho API khi làm việc với dataset (upload, tra cứu, schema,
quan hệ). `api` không được gọi trực tiếp `tools`, nên service đứng giữa và trả về dict thuần JSON.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

import pandas as pd

from backend.agents import SQLAgent, VizAgent
from backend.tools import ToolRegistry


class PipelineStatus(str, Enum):
    OK = "ok"
    SQL_ERROR = "sql_error"          # SQL chạy lỗi
    EMPTY_RESULT = "empty_result"    # SQL chạy được nhưng không có dòng nào
    VIZ_ERROR = "viz_error"          # code vẽ biểu đồ lỗi / không ra biểu đồ
    NEEDS_CLARIFICATION = "needs_clarification"  # câu hỏi cần làm rõ / cần quan hệ giữa bảng


@dataclass
class PipelineRequest:
    question: str
    chart_hint: str = "auto"
    use_mock_sql: bool = False
    use_ollama: bool = True
    use_mock_viz: bool = False
    ollama_model: Optional[str] = None
    dataset_id: Optional[str] = None  # None = demo DB (luồng cũ)


@dataclass
class PipelineResult:
    status: PipelineStatus
    question: str
    full_question: str
    # Stage 1
    sql: str
    sql_prompt: str
    sql_source: str
    # Stage 2
    sql_error: Optional[str] = None
    df: Optional[pd.DataFrame] = None
    truncated: bool = False          # kết quả bị cắt theo giới hạn số dòng/kích thước
    clarification: Optional[str] = None
    # Stage 3
    viz_code: Optional[str] = None
    viz_prompt: Optional[str] = None
    viz_source: Optional[str] = None
    viz_error: Optional[str] = None
    chart_png: Optional[bytes] = None


class PipelineOrchestrator:
    def __init__(self, sql_agent: SQLAgent, viz_agent: VizAgent, tools: ToolRegistry):
        self._sql_agent = sql_agent
        self._viz_agent = viz_agent
        self._tools = tools

    def run(self, request: PipelineRequest) -> PipelineResult:
        question = request.question
        full_question = question
        if request.chart_hint != "auto":
            full_question = f"{question} (as a {request.chart_hint})"

        # ── Stage 1: câu hỏi -> SQL (dùng câu hỏi gốc, không kèm gợi ý chart) ──
        sql_gen = self._sql_agent.generate(
            question, use_mock=request.use_mock_sql, dataset_id=request.dataset_id
        )
        result = PipelineResult(
            status=PipelineStatus.OK,
            question=question,
            full_question=full_question,
            sql=sql_gen.sql,
            sql_prompt=sql_gen.prompt,
            sql_source=sql_gen.source,
        )

        if sql_gen.clarification:
            result.status = PipelineStatus.NEEDS_CLARIFICATION
            result.clarification = sql_gen.clarification
            return result

        # ── Stage 2: SQL -> DataFrame ──────────────────────────────────────────
        df, sql_error = self._tools.get("execute_sql").run(sql=sql_gen.sql, dataset_id=request.dataset_id)
        if sql_error:
            result.status = PipelineStatus.SQL_ERROR
            result.sql_error = sql_error
            return result
        if df is None or df.empty:
            result.status = PipelineStatus.EMPTY_RESULT
            return result
        result.df = df
        result.truncated = bool(df.attrs.get("truncated"))

        # ── Stage 3: DataFrame -> code -> biểu đồ ──────────────────────────────
        viz = self._viz_agent.generate_code(
            full_question,
            df,
            use_mock=request.use_mock_viz,
            use_ollama=request.use_ollama,
            model=request.ollama_model,
        )
        result.viz_code = viz.code
        result.viz_prompt = viz.prompt
        result.viz_source = viz.source

        render = self._tools.get("render_chart").run(code=viz.code, df=df)
        result.chart_png = render.png
        if render.error:
            result.status = PipelineStatus.VIZ_ERROR
            result.viz_error = render.error
        return result


class DatasetService:
    """Upload / tra cứu dataset và schema, qua tools."""

    def __init__(self, tools: ToolRegistry, max_upload_bytes: int) -> None:
        self._tools = tools
        self.max_upload_bytes = max_upload_bytes

    def upload(self, filename: str, content: bytes) -> dict:
        return self._tools.get("import_dataset").run(filename=filename, content=content)

    def get(self, dataset_id: str) -> dict:
        return self._tools.get("get_dataset").run(dataset_id=dataset_id)

    def schema(self, dataset_id: Optional[str] = None) -> dict:
        """{"tables": {tên: [cột]}, "relations": [...]} — relations gồm khóa ngoại và quan hệ đã xác nhận."""
        tables = self._tools.get("list_tables").run(dataset_id=dataset_id)
        relations: list = []
        if dataset_id is not None:
            info = self.get(dataset_id)
            relations = info["relations"] + info["confirmed_relations"]
        return {"tables": tables, "relations": relations}

    def link_tables(self, dataset_id: str, **relation: str) -> dict:
        return self._tools.get("link_tables").run(dataset_id=dataset_id, **relation)

    def unlink_tables(self, dataset_id: str, **relation: str) -> dict:
        return self._tools.get("unlink_tables").run(dataset_id=dataset_id, **relation)
