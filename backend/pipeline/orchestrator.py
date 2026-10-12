"""
backend/pipeline/orchestrator.py
--------------------------------
Orchestrator điều phối toàn bộ pipeline, quyết định thứ tự và điều kiện dừng:

    Stage 1  SQLAgent.generate        câu hỏi  -> SQL
    Stage 2  tool `execute_sql`       SQL      -> DataFrame
    Stage 3  VizAgent.generate_code   df + câu hỏi -> code matplotlib
             tool `render_chart`      code     -> PNG

Orchestrator không biết chi tiết LLM/DB; nó chỉ nối agents và tools.
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


@dataclass
class PipelineRequest:
    question: str
    chart_hint: str = "auto"
    use_mock_sql: bool = False
    use_ollama: bool = True
    use_mock_viz: bool = False
    ollama_model: Optional[str] = None


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
        sql_gen = self._sql_agent.generate(question, use_mock=request.use_mock_sql)
        result = PipelineResult(
            status=PipelineStatus.OK,
            question=question,
            full_question=full_question,
            sql=sql_gen.sql,
            sql_prompt=sql_gen.prompt,
            sql_source=sql_gen.source,
        )

        # ── Stage 2: SQL -> DataFrame ──────────────────────────────────────────
        df, sql_error = self._tools.get("execute_sql").run(sql=sql_gen.sql)
        if sql_error:
            result.status = PipelineStatus.SQL_ERROR
            result.sql_error = sql_error
            return result
        if df is None or df.empty:
            result.status = PipelineStatus.EMPTY_RESULT
            return result
        result.df = df

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
