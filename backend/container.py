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
from backend.database import DatasetStore, ImportLimits, SQLiteRepository
from backend.llm import DeepSeekClient, OllamaClient
from backend.pipeline import DatasetService, PipelineOrchestrator
from backend.tools import (
    ExecuteSQLTool,
    GetDatasetTool,
    GetSchemaTool,
    ImportDatasetTool,
    LinkTablesTool,
    ListTablesTool,
    MockSQLTool,
    MockVizCodeTool,
    RenderChartTool,
    ToolRegistry,
    UnlinkTablesTool,
)


@dataclass
class Container:
    settings: Settings
    repo: SQLiteRepository          # database demo mặc định (luồng cũ, chỉ để health check)
    datasets: DatasetStore          # registry dataset upload + tra cứu repository theo dataset_id
    dataset_service: DatasetService
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
    max_upload_bytes = settings.max_upload_mb * 1024 * 1024
    datasets = DatasetStore(
        settings.dataset_dir,
        ImportLimits(
            max_upload_bytes=max_upload_bytes,
            timeout_seconds=settings.import_timeout_seconds,
            max_rows_per_table=settings.max_rows_per_table,
        ),
        default_db_path=settings.db_path,
        query_max_rows=settings.query_max_rows,
        query_timeout_seconds=settings.query_timeout_seconds,
    )
    deepseek = deepseek or DeepSeekClient(settings)
    ollama = ollama or OllamaClient(settings)

    repo = datasets.repository()  # demo DB mặc định, cùng giới hạn truy vấn

    tools = ToolRegistry()
    tools.register(GetSchemaTool(datasets))
    tools.register(ListTablesTool(datasets))
    tools.register(ExecuteSQLTool(datasets))
    tools.register(ImportDatasetTool(datasets))
    tools.register(GetDatasetTool(datasets))
    tools.register(LinkTablesTool(datasets))
    tools.register(UnlinkTablesTool(datasets))
    tools.register(MockSQLTool(datasets))
    tools.register(MockVizCodeTool())
    tools.register(RenderChartTool(dpi=settings.chart_dpi))

    sql_agent = SQLAgent(llm=deepseek, tools=tools, settings=settings)
    viz_agent = VizAgent(local_llm=ollama, cloud_llm=deepseek, tools=tools, settings=settings)
    orchestrator = PipelineOrchestrator(sql_agent, viz_agent, tools)
    dataset_service = DatasetService(tools, max_upload_bytes)

    return Container(
        settings=settings,
        repo=repo,
        datasets=datasets,
        dataset_service=dataset_service,
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
