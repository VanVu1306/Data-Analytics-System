"""
backend/agents/sql_agent.py
---------------------------
SQLAgent (Stage 1 — DAIL-SQL): câu hỏi tự nhiên -> câu SQL.

Agent điều phối: tool `get_schema` -> prompts -> cloud LLM -> làm sạch SQL.
Không có key / chọn mock -> tool `mock_sql`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from backend.config import Settings
from backend.llm import LLMClient, provider_label
from backend.prompts import (
    CLARIFY_PREFIX,
    DATASET_SQL_SYSTEM_PROMPT,
    SQL_SYSTEM_PROMPT,
    build_dail_prompt,
    build_dataset_prompt,
)
from backend.tools import ToolRegistry, extract_sql


@dataclass
class SQLGeneration:
    sql: str
    prompt: str
    source: str  # "deepseek" | "mock" | "error"
    clarification: Optional[str] = None  # LLM yêu cầu làm rõ câu hỏi/quan hệ thay vì sinh SQL


class SQLAgent:
    def __init__(self, llm: LLMClient, tools: ToolRegistry, settings: Settings):
        self._llm = llm
        self._tools = tools
        self._settings = settings

    def generate(
        self, question: str, use_mock: bool = False, dataset_id: Optional[str] = None
    ) -> SQLGeneration:
        schema = self._tools.get("get_schema").run(dataset_id=dataset_id)
        # dataset_id=None: demo DB (few-shot bán hàng như bản gốc); dataset upload: prompt theo schema.
        if dataset_id is None:
            prompt, system = build_dail_prompt(question, schema), SQL_SYSTEM_PROMPT
        else:
            prompt, system = build_dataset_prompt(question, schema), DATASET_SQL_SYSTEM_PROMPT

        # Mock mode (không có API key hoặc người dùng chọn mock)
        if use_mock or not self._llm.is_available():
            sql = self._tools.get("mock_sql").run(question=question, dataset_id=dataset_id)
            return SQLGeneration(sql=sql, prompt=prompt, source="mock")

        try:
            raw = self._llm.complete(
                prompt,
                system=system,
                temperature=self._settings.sql_temperature,
                max_tokens=self._settings.sql_max_tokens,
            )
            sql = extract_sql(raw)
            clarification = None
            if sql.startswith(CLARIFY_PREFIX):
                clarification = sql[len(CLARIFY_PREFIX):].strip() or "Vui lòng làm rõ câu hỏi."
            return SQLGeneration(sql=sql, prompt=prompt, source=self._llm.name, clarification=clarification)
        except Exception as e:
            provider = provider_label(self._llm.name)
            return SQLGeneration(
                sql=f"-- Error calling {provider}: {e}", prompt=prompt, source="error"
            )
