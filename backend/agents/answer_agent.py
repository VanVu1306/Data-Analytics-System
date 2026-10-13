"""
backend/agents/answer_agent.py
------------------------------
AnswerAgent (Giai đoạn 3): câu hỏi + kết quả truy vấn đã giới hạn -> câu trả lời text ngắn, có căn cứ.

Chuỗi quyết định:
    kết quả rỗng / không dùng LLM / LLM không sẵn sàng  -> tool `template_answer`
    LLM trả lời                                          -> kiểm tra bám số liệu (`answer_is_grounded`)
        bám số liệu                                      -> dùng câu của LLM
        không bám / rỗng / lỗi gọi LLM                   -> tool `template_answer`

Chỉ tên cột và tối đa `answer_max_rows` dòng đầu được gửi cho LLM.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pandas as pd

from backend.config import Settings
from backend.llm import LLMClient
from backend.prompts import ANSWER_SYSTEM_PROMPT, build_answer_prompt
from backend.tools import ToolRegistry, answer_is_grounded, is_vietnamese


@dataclass
class AnswerGeneration:
    text: str
    source: str                           # tên provider LLM | "template"
    fallback_reason: Optional[str] = None  # vì sao phải dùng câu mẫu (nếu có)


class AnswerAgent:
    def __init__(self, llm: LLMClient, tools: ToolRegistry, settings: Settings):
        self._llm = llm
        self._tools = tools
        self._settings = settings

    def generate(
        self,
        question: str,
        df: Optional[pd.DataFrame],
        *,
        truncated: bool = False,
        use_llm: bool = True,
    ) -> AnswerGeneration:
        def template(reason: Optional[str] = None) -> AnswerGeneration:
            text = self._tools.get("template_answer").run(question=question, df=df, truncated=truncated)
            return AnswerGeneration(text=text, source="template", fallback_reason=reason)

        if df is None or df.empty:
            return template()
        if not use_llm or not self._llm.is_available():
            return template()

        max_rows = self._settings.answer_max_rows
        try:
            raw = self._llm.complete(
                build_answer_prompt(question, df, max_rows=max_rows, truncated=truncated),
                system=ANSWER_SYSTEM_PROMPT,
                temperature=self._settings.answer_temperature,
                max_tokens=self._settings.answer_max_tokens,
            )
        except Exception as e:
            return template(f"llm_error: {e}")

        text = (raw or "").strip()
        if not text:
            return template("empty_answer")
        if not answer_is_grounded(text, question, df, max_rows):
            return template("ungrounded_numbers")
        if truncated and not _mentions_truncation(text):
            text += (
                " (Kết quả đã bị cắt bớt theo giới hạn hệ thống nên chưa đầy đủ.)"
                if is_vietnamese(question)
                else " (The result was cut off by a system limit, so it is incomplete.)"
            )
        return AnswerGeneration(text=text, source=self._llm.name)


def _mentions_truncation(text: str) -> bool:
    t = text.lower()
    return any(w in t for w in ("cắt", "truncat", "cut off", "incomplete", "chưa đầy đủ", "mẫu", "sample"))
