"""
backend/pipeline/orchestrator.py
--------------------------------
Orchestrator điều phối một luồng hỏi đáp duy nhất, quyết định thứ tự và điều kiện dừng:

    Stage 1  SQLAgent.generate        câu hỏi  -> SQL (hoặc yêu cầu làm rõ)
    Stage 2  tool `execute_sql`       SQL      -> DataFrame (chỉ đọc, có giới hạn)
    Stage 3  AnswerAgent.generate     df       -> câu trả lời text ngắn, bám số liệu   (luôn chạy khi có kết quả)
    Stage 4  (tùy chọn) biểu đồ       chỉ khi người dùng yêu cầu (trong câu hỏi hoặc qua tùy chọn `chart_hint`)
             VizAgent.generate_code   df + câu hỏi -> code matplotlib
             tool `render_chart`      code -> PNG

Biểu đồ là bước phụ: lỗi ở Stage 4 chỉ ghi vào `chart_status/chart_error`, không làm mất câu trả lời và bảng.
Mọi stage chạy trên dataset do `dataset_id` chọn (None = demo DB); id không tồn tại -> DatasetNotFoundError,
không fallback sang database khác.

Orchestrator không biết chi tiết LLM/DB; nó chỉ nối agents và tools.

File này cũng chứa `DatasetService`: điểm vào cho API khi làm việc với dataset (upload, tra cứu, schema,
quan hệ). `api` không được gọi trực tiếp `tools`, nên service đứng giữa và trả về dict thuần JSON.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum
from typing import Optional

import pandas as pd

from backend.agents import AnswerAgent, SQLAgent, VizAgent
from backend.tools import ToolRegistry, detect_sql_request, resolve_chart_plan


class PipelineStatus(str, Enum):
    OK = "ok"
    SQL_ERROR = "sql_error"          # SQL không sinh được / chạy lỗi / bị từ chối
    EMPTY_RESULT = "empty_result"    # SQL chạy được nhưng không có dòng nào
    NEEDS_CLARIFICATION = "needs_clarification"  # câu hỏi cần làm rõ / cần quan hệ giữa bảng


class ChartStatus(str, Enum):
    NOT_REQUESTED = "not_requested"
    OK = "ok"
    ERROR = "error"


@dataclass
class PipelineRequest:
    question: str
    chart_hint: str = "detect"        # detect | none | auto | bar chart | line chart | pie chart | scatter plot
    show_sql: bool = False            # người dùng bật hiển thị SQL (ngoài việc hỏi trực tiếp trong câu hỏi)
    use_mock_sql: bool = False        # không gọi cloud LLM: SQL mẫu + câu trả lời mẫu
    use_ollama: bool = True
    use_mock_viz: bool = False
    ollama_model: Optional[str] = None
    dataset_id: Optional[str] = None  # None = demo DB (luồng cũ)


@dataclass
class PipelineResult:
    status: PipelineStatus
    question: str
    # Stage 1 — SQL (bằng chứng)
    sql: str
    sql_prompt: str
    sql_source: str
    show_sql: bool = False           # nên mở sẵn phần "Bằng chứng / SQL"
    # Stage 2 — truy vấn
    sql_error: Optional[str] = None
    df: Optional[pd.DataFrame] = None
    truncated: bool = False          # kết quả bị cắt theo giới hạn số dòng/kích thước
    elapsed_ms: Optional[int] = None  # thời gian thực thi SQL
    clarification: Optional[str] = None
    # Stage 3 — câu trả lời text
    answer: Optional[str] = None
    answer_source: Optional[str] = None
    answer_fallback_reason: Optional[str] = None
    # Stage 4 — biểu đồ (tách riêng khỏi truy vấn)
    chart_status: ChartStatus = ChartStatus.NOT_REQUESTED
    chart_origin: str = "none"       # none | option | prompt
    chart_type: Optional[str] = None
    viz_code: Optional[str] = None
    viz_prompt: Optional[str] = None
    viz_source: Optional[str] = None
    chart_error: Optional[str] = None
    chart_png: Optional[bytes] = None


class PipelineOrchestrator:
    def __init__(self, sql_agent: SQLAgent, viz_agent: VizAgent, tools: ToolRegistry, answer_agent: AnswerAgent):
        self._sql_agent = sql_agent
        self._viz_agent = viz_agent
        self._answer_agent = answer_agent
        self._tools = tools

    def run(self, request: PipelineRequest) -> PipelineResult:
        question = request.question
        chart_plan = resolve_chart_plan(question, request.chart_hint)
        show_sql = request.show_sql or detect_sql_request(question)

        # ── Stage 1: câu hỏi -> SQL (dùng câu hỏi gốc, không kèm gợi ý chart) ──
        sql_gen = self._sql_agent.generate(
            question, use_mock=request.use_mock_sql, dataset_id=request.dataset_id
        )
        result = PipelineResult(
            status=PipelineStatus.OK,
            question=question,
            sql=sql_gen.sql,
            sql_prompt=sql_gen.prompt,
            sql_source=sql_gen.source,
            show_sql=show_sql,
        )

        if sql_gen.clarification:
            result.status = PipelineStatus.NEEDS_CLARIFICATION
            result.clarification = sql_gen.clarification
            result.sql = ""  # dòng "-- CLARIFY: ..." không phải SQL để hiển thị
            return result

        if sql_gen.source == "error":  # lỗi gọi LLM: không đem câu chú thích đi chạy như SQL
            result.status = PipelineStatus.SQL_ERROR
            result.sql_error = sql_gen.sql.lstrip("- ").strip()
            result.sql = ""
            return result

        # ── Stage 2: SQL -> DataFrame ──────────────────────────────────────────
        started = time.perf_counter()
        df, sql_error = self._tools.get("execute_sql").run(sql=sql_gen.sql, dataset_id=request.dataset_id)
        result.elapsed_ms = int((time.perf_counter() - started) * 1000)
        if sql_error:
            result.status = PipelineStatus.SQL_ERROR
            result.sql_error = sql_error
            return result
        if df is None or df.empty:
            result.status = PipelineStatus.EMPTY_RESULT
            self._answer(result, request, None)
            return result
        result.df = df
        result.truncated = bool(df.attrs.get("truncated"))

        # ── Stage 3: câu trả lời text (luôn có khi truy vấn thành công) ────────
        self._answer(result, request, df)

        # ── Stage 4: biểu đồ — chỉ khi được yêu cầu; lỗi không ảnh hưởng kết quả ──
        if chart_plan.requested:
            result.chart_origin = chart_plan.origin
            result.chart_type = chart_plan.chart_type
            self._chart(result, request, df, chart_plan.chart_type)
        return result

    # ── Helpers ────────────────────────────────────────────────────────────
    def _answer(self, result: PipelineResult, request: PipelineRequest, df: Optional[pd.DataFrame]) -> None:
        use_llm = not request.use_mock_sql and result.sql_source not in ("mock", "error")
        try:
            gen = self._answer_agent.generate(result.question, df, truncated=result.truncated, use_llm=use_llm)
        except Exception as e:  # câu trả lời không bao giờ làm hỏng cả request
            text = self._tools.get("template_answer").run(question=result.question, df=df, truncated=result.truncated)
            result.answer, result.answer_source, result.answer_fallback_reason = text, "template", f"error: {e}"
            return
        result.answer = gen.text
        result.answer_source = gen.source
        result.answer_fallback_reason = gen.fallback_reason

    def _chart(
        self, result: PipelineResult, request: PipelineRequest, df: pd.DataFrame, chart_type: Optional[str]
    ) -> None:
        question = result.question
        viz_question = f"{question} (as a {chart_type})" if chart_type else question
        try:
            viz = self._viz_agent.generate_code(
                viz_question,
                df,
                use_mock=request.use_mock_viz,
                use_ollama=request.use_ollama,
                model=request.ollama_model,
            )
            result.viz_code, result.viz_prompt, result.viz_source = viz.code, viz.prompt, viz.source
            render = self._tools.get("render_chart").run(code=viz.code, df=df)
        except Exception as e:
            result.chart_status = ChartStatus.ERROR
            result.chart_error = f"{type(e).__name__}: {e}"
            return
        if render.error or not render.png:
            result.chart_status = ChartStatus.ERROR
            result.chart_error = render.error or "Không tạo được biểu đồ."
        else:
            result.chart_status = ChartStatus.OK
            result.chart_png = render.png


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
