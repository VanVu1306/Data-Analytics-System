"""
backend/container.py
--------------------
Composition root: nơi DUY NHẤT khởi tạo và nối các thành phần
(settings -> repo -> llm clients -> tools -> agents -> orchestrator).
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Optional

from backend.agents import SQLAgent, VizAgent
from backend.config import Settings, get_settings
from backend.database import SQLiteRepository
from backend.llm import DeepSeekClient, OllamaClient
from backend.pipeline import PipelineOrchestrator
from backend.tools import (
    ExecuteSQLTool,
    GetSchemaTool,
    MockSQLTool,
    MockVizCodeTool,
    RenderChartTool,
    ToolRegistry,
)


@dataclass
class Container:
    settings: Settings
    repo: SQLiteRepository
    deepseek: DeepSeekClient
    ollama: OllamaClient
    tools: ToolRegistry
    sql_agent: SQLAgent
    viz_agent: VizAgent
    orchestrator: PipelineOrchestrator


def build_container(
    settings: Settings,
    *,
    deepseek: Optional[DeepSeekClient] = None,
    ollama: Optional[OllamaClient] = None,
) -> Container:
    """Dựng toàn bộ đồ thị phụ thuộc. Có thể truyền LLM client giả khi test."""
    repo = SQLiteRepository(settings.db_path)
    deepseek = deepseek or DeepSeekClient(settings)
    ollama = ollama or OllamaClient(settings)

    tools = ToolRegistry()
    tools.register(GetSchemaTool(repo))
    tools.register(ExecuteSQLTool(repo))
    tools.register(MockSQLTool())
    tools.register(MockVizCodeTool())
    tools.register(RenderChartTool(dpi=settings.chart_dpi))

    sql_agent = SQLAgent(llm=deepseek, tools=tools, settings=settings)
    viz_agent = VizAgent(local_llm=ollama, cloud_llm=deepseek, tools=tools, settings=settings)
    orchestrator = PipelineOrchestrator(sql_agent, viz_agent, tools)

    return Container(
        settings=settings,
        repo=repo,
        deepseek=deepseek,
        ollama=ollama,
        tools=tools,
        sql_agent=sql_agent,
        viz_agent=viz_agent,
        orchestrator=orchestrator,
    )


@lru_cache(maxsize=1)
def get_container() -> Container:
    return build_container(get_settings())
