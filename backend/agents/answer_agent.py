"""
backend/agents/answer_agent.py
------------------------------
AnswerAgent (Giai đoạn 3 + 7): một agent DUY NHẤT tổng hợp câu trả lời text cho query-only, EDA-only và query + EDA.

Đầu vào: câu hỏi + `AnswerEvidence` (kết quả query đã giới hạn và/hoặc các `EdaPayload` theo thứ tự kế hoạch).
Agent chỉ DIỄN GIẢI bằng chứng đã có: không chạy truy vấn/phân tích, không tự tính; bảng query, SQL, bảng EDA và biểu đồ
là các phần hiển thị riêng của response.

Chuỗi quyết định (mọi nhánh dùng chung, `AnswerGeneration.source` / `fallback_reason` ghi lại kết quả):

    không có số liệu để diễn giải (0 dòng, mọi EDA lỗi...)  -> tool `answer_template`          lý do: no_data
    không dùng LLM (mock)                                    -> tool `answer_template`          lý do: mock
    LLM chưa cấu hình / không sẵn sàng                       -> tool `answer_template`          lý do: llm_unavailable
    LLM lỗi / trả lời rỗng                                   -> tool `answer_template`          lý do: llm_error: ... | empty_answer
    LLM trả lời nhưng có số không có trong bằng chứng        -> tool `answer_template`          lý do: ungrounded_numbers
    LLM trả lời bám bằng chứng                               -> câu của LLM (nguồn = tên provider), tự nối thêm các lưu ý
                                                                bị bỏ sót (kết quả cắt bớt, ước tính trên mẫu, operation lỗi)

Chỉ tên cột, số liệu tóm tắt và tối đa `answer_max_rows` dòng đầu của mỗi bảng được gửi cho LLM.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from backend.config import Settings
from backend.llm import LLMClient
from backend.prompts import ANSWER_SYSTEM_PROMPT, build_answer_prompt
from backend.tools import (
    AnswerEvidence,
    ToolRegistry,
    answer_is_grounded,
    build_evidence_context,
    is_vietnamese,
    missing_caveats,
)

# Lý do dùng câu mẫu (cố định, dùng chung cho mọi loại bằng chứng)
REASON_NO_DATA = "no_data"
REASON_MOCK = "mock"
REASON_LLM_UNAVAILABLE = "llm_unavailable"
REASON_EMPTY = "empty_answer"
REASON_UNGROUNDED = "ungrounded_numbers"


@dataclass
class AnswerGeneration:
    text: str
    source: str                           # tên provider LLM | "template"
    fallback_reason: Optional[str] = None  # vì sao phải dùng câu mẫu (None nếu dùng câu của LLM)


class AnswerAgent:
    def __init__(self, llm: LLMClient, tools: ToolRegistry, settings: Settings):
        self._llm = llm
        self._tools = tools
        self._settings = settings

    def generate(self, question: str, evidence: AnswerEvidence, *, use_llm: bool = True) -> AnswerGeneration:
        def template(reason: str) -> AnswerGeneration:
            text = self._tools.get("answer_template").run(question=question, evidence=evidence)
            return AnswerGeneration(text=text, source="template", fallback_reason=reason)

        if not evidence.interpretable:
            return template(REASON_NO_DATA)
        if not use_llm:
            return template(REASON_MOCK)
        if not self._llm.is_available():
            return template(REASON_LLM_UNAVAILABLE)

        max_rows = self._settings.answer_max_rows
        try:
            context = build_evidence_context(evidence, max_rows)
            raw = self._llm.complete(
                build_answer_prompt(question, context.text),
                system=ANSWER_SYSTEM_PROMPT,
                temperature=self._settings.answer_temperature,
                max_tokens=self._settings.answer_max_tokens,
            )
        except Exception as e:
            return template(f"llm_error: {type(e).__name__}: {e}"[:300])

        text = (raw or "").strip()
        if not text:
            return template(REASON_EMPTY)
        if not answer_is_grounded(text, question, context):
            return template(REASON_UNGROUNDED)
        notes = missing_caveats(text, evidence, is_vietnamese(question))
        if notes:
            text = " ".join([text, *notes])
        return AnswerGeneration(text=text, source=self._llm.name)
