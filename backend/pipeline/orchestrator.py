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

from dataclasses import field

from backend.agents import AnswerAgent, PlannerAgent, SQLAgent, VizAgent
from backend.tools import ChartError, EdaPayload, ToolRegistry, detect_sql_request, is_vi_question, resolve_chart_plan
from backend.tools.planner import OP_QUERY, PlanStep, ProcessingPlan


class PipelineStatus(str, Enum):
    OK = "ok"
    SQL_ERROR = "sql_error"          # SQL không sinh được / chạy lỗi / bị từ chối
    EMPTY_RESULT = "empty_result"    # SQL chạy được nhưng không có dòng nào
    NEEDS_CLARIFICATION = "needs_clarification"  # câu hỏi cần làm rõ / cần quan hệ giữa bảng
    EDA_ERROR = "eda_error"          # kế hoạch chỉ có EDA và mọi operation đều lỗi/không áp dụng được


class ChartStatus(str, Enum):
    NOT_REQUESTED = "not_requested"
    OK = "ok"
    ERROR = "error"


@dataclass
class PipelineRequest:
    question: str
    chart_hint: str = "detect"        # detect | none | auto | một loại trong CHART_TYPES
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
    chart_meta: Optional[dict] = None   # nguồn dữ liệu, số dòng gốc/đã dùng, phương pháp, cảnh báo, nhãn trục (chế độ spec)
    # Giai đoạn 4 — kế hoạch xử lý và kết quả EDA
    plan: Optional[ProcessingPlan] = None
    eda: list = field(default_factory=list)   # list[EdaPayload], theo thứ tự trong kế hoạch


class PipelineOrchestrator:
    def __init__(
        self,
        sql_agent: SQLAgent,
        viz_agent: VizAgent,
        tools: ToolRegistry,
        answer_agent: AnswerAgent,
        planner_agent: Optional[PlannerAgent] = None,
        viz_mode: str = "code",
    ):
        self._viz_mode = viz_mode  # "spec": bộ vẽ xác định (allowlist) | "code": LLM sinh code matplotlib (cũ)
        self._sql_agent = sql_agent
        self._viz_agent = viz_agent
        self._answer_agent = answer_agent
        self._planner = planner_agent
        self._tools = tools

    def run(self, request: PipelineRequest) -> PipelineResult:
        """Kế hoạch (query / EDA / kết hợp) -> chạy từng bước -> hợp nhất phản hồi."""
        plan = self._plan(request)
        if isinstance(plan, str):  # câu hỏi làm rõ từ planner
            return PipelineResult(
                status=PipelineStatus.NEEDS_CLARIFICATION, question=request.question, sql="", sql_prompt="",
                sql_source="planner", clarification=plan,
            )
        query_step = next((s for s in plan.steps if s.op == OP_QUERY), None)
        if query_step is not None:
            result = self._run_query(request, plan, query_step)
        else:
            result = PipelineResult(
                status=PipelineStatus.OK, question=request.question, sql="", sql_prompt="", sql_source="none",
                show_sql=plan.show_sql,
            )
        result.plan = plan
        if plan.eda_steps:
            self._run_eda(result, request, plan)
        if self._viz_mode == "spec":
            if plan.chart.requested:
                self._chart_spec(result, request, plan)
        elif query_step is None and plan.chart.requested:  # chế độ code: không vẽ được từ kết quả EDA
            result.chart_origin, result.chart_type = plan.chart.origin, plan.chart.chart_type
            result.chart_status = ChartStatus.ERROR
            result.chart_error = (
                "Chế độ vẽ bằng code (VIZ_MODE=code) chỉ vẽ được biểu đồ cho kết quả truy vấn; "
                "hãy dùng VIZ_MODE=spec để vẽ từ kết quả EDA."
            )
        return result

    # ── Kế hoạch ───────────────────────────────────────────────────────────
    def _plan(self, request: PipelineRequest):
        if self._planner is None:  # không có planner: luồng truy vấn như trước
            return ProcessingPlan(
                [PlanStep(OP_QUERY)], resolve_chart_plan(request.question, request.chart_hint),
                request.show_sql or detect_sql_request(request.question),
            )
        outcome = self._planner.plan(
            request.question,
            dataset_id=request.dataset_id,
            chart_hint=request.chart_hint,
            show_sql=request.show_sql,
            use_llm=not request.use_mock_sql,
        )
        return outcome.clarification if outcome.clarification else outcome.plan

    # ── Bước EDA ───────────────────────────────────────────────────────────
    def _run_eda(self, result: PipelineResult, request: PipelineRequest, plan: ProcessingPlan) -> None:
        for step in plan.eda_steps:
            payload: EdaPayload = self._tools.get(f"eda_{step.op}").run(
                table=step.table, columns=list(step.columns) or None, dataset_id=request.dataset_id, **step.params
            )  # dataset không tồn tại -> DatasetNotFoundError, không fallback
            payload.text = self._tools.get("eda_answer").run(payload=payload, question=result.question)
            result.eda.append(payload)
        texts = [p.text for p in result.eda if p.text]
        if texts:
            joined = "\n\n".join(texts)
            result.answer = f"{result.answer}\n\n{joined}" if result.answer else joined
            result.answer_source = result.answer_source or "template"
        if plan.has_query:
            return  # trạng thái do phần truy vấn quyết định
        if not any(p.status == "ok" for p in result.eda):
            result.status = PipelineStatus.EDA_ERROR

    # ── Bước truy vấn ──────────────────────────────────────────────────────
    def _run_query(self, request: PipelineRequest, plan: ProcessingPlan, step: PlanStep) -> PipelineResult:
        question = request.question
        sql_question = step.question or question  # câu hỏi con (phần truy vấn) nếu planner tách được
        chart_plan = plan.chart
        show_sql = plan.show_sql

        # ── Stage 1: câu hỏi -> SQL (dùng câu hỏi gốc, không kèm gợi ý chart) ──
        sql_gen = self._sql_agent.generate(
            sql_question, use_mock=request.use_mock_sql, dataset_id=request.dataset_id
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
        if chart_plan.requested and self._viz_mode == "code":
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

    def _chart_spec(self, result: PipelineResult, request: PipelineRequest, plan: ProcessingPlan) -> None:
        """Biểu đồ xác định: chọn loại theo mục tiêu + kiểu dữ liệu, dùng kết quả truy vấn/EDA đã có.
        Lỗi chỉ ghi vào chart_*; câu trả lời, bảng truy vấn và kết quả EDA được giữ nguyên."""
        eda_ok = [p for p in result.eda if p.status == "ok"]
        if result.df is None and not eda_ok:
            return  # truy vấn lỗi/rỗng và không có EDA: không có dữ liệu để vẽ (trạng thái chính đã nói rõ)
        result.chart_origin = plan.chart.origin
        result.chart_type = plan.chart.chart_type
        result.viz_source = "spec"
        try:
            spec = self._tools.get("build_chart_spec").run(
                plan=plan.chart, df=result.df, eda=result.eda, dataset_id=request.dataset_id,
                vi=is_vi_question(result.question),
            )
            result.chart_type = spec.chart_type
            result.chart_meta = spec.meta
            render = self._tools.get("render_chart_spec").run(spec=spec)
        except ChartError as e:
            result.chart_status, result.chart_error = ChartStatus.ERROR, str(e)
            return
        except Exception as e:  # lỗi bất ngờ ở biểu đồ không được làm mất phản hồi
            result.chart_status, result.chart_error = ChartStatus.ERROR, f"{type(e).__name__}: {e}"
            return
        if render.error or not render.png:
            result.chart_status, result.chart_error = ChartStatus.ERROR, render.error or "Không tạo được biểu đồ."
        else:
            result.chart_status, result.chart_png = ChartStatus.OK, render.png

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
