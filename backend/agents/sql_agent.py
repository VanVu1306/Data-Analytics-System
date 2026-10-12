"""
backend/agents/sql_agent.py
---------------------------
SQLAgent (Stage 1 — DAIL-SQL): câu hỏi tự nhiên -> câu SQL.

Agent điều phối: tool `get_schema` -> prompts -> LLM (DeepSeek) -> làm sạch SQL.
Không có key / chọn mock -> tool `mock_sql`.
"""
from __future__ import annotations

from dataclasses import dataclass

from backend.config import Settings
from backend.llm import LLMClient
from backend.prompts import SQL_SYSTEM_PROMPT, build_dail_prompt
from backend.tools import ToolRegistry, extract_sql


@dataclass
class SQLGeneration:
    sql: str
    prompt: str
    source: str  # "deepseek" | "mock" | "error"


class SQLAgent:
    def __init__(self, llm: LLMClient, tools: ToolRegistry, settings: Settings):
        self._llm = llm
        self._tools = tools
        self._settings = settings

    def generate(self, question: str, use_mock: bool = False) -> SQLGeneration:
        schema = self._tools.get("get_schema").run()
        prompt = build_dail_prompt(question, schema)

        # Mock mode (không có API key hoặc người dùng chọn mock)
        if use_mock or not self._llm.is_available():
            sql = self._tools.get("mock_sql").run(question=question)
            return SQLGeneration(sql=sql, prompt=prompt, source="mock")

        try:
            raw = self._llm.complete(
                prompt,
                system=SQL_SYSTEM_PROMPT,
                temperature=self._settings.sql_temperature,
                max_tokens=self._settings.sql_max_tokens,
            )
            return SQLGeneration(sql=extract_sql(raw), prompt=prompt, source=self._llm.name)
        except Exception as e:
            return SQLGeneration(sql=f"-- Error calling DeepSeek: {e}", prompt=prompt, source="error")
