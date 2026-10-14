"""
backend/agents/planner_agent.py
-------------------------------
PlannerAgent (giai đoạn 4): câu hỏi -> `ProcessingPlan` đã xác thực.

    không có từ khóa EDA                       -> [query]                       (quy tắc, không gọi LLM)
    từ khóa EDA rõ ràng, không có phần truy vấn -> các bước EDA                  (quy tắc, không gọi LLM)
    EDA + truy vấn (kết hợp)                    -> LLM trả kế hoạch JSON         (nếu LLM sẵn sàng và không dùng mock)
        LLM trả kế hoạch sai / không hợp lệ / mơ hồ -> hỏi lại người dùng (không tự đoán)
        LLM không gọi được (lỗi mạng/hết hạn mức)    -> dùng bản nháp của quy tắc (source = "rules_fallback")
    không có LLM / mock                          -> bản nháp của quy tắc          (source = "rules")
Mọi kế hoạch, dù từ nguồn nào, đều đi qua `validate_plan`.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from backend.config import Settings
from backend.llm import LLMClient
from backend.prompts import PLANNER_SYSTEM_PROMPT, build_planner_prompt
from backend.tools import ToolRegistry, detect_sql_request, is_vi_question, resolve_chart_plan
from backend.tools.planner import PlanDraft, PlanError, PlanOutcome, ProcessingPlan, draft_plan, validate_plan


def parse_plan_json(raw: str) -> Dict[str, Any]:
    """Lấy object JSON đầu tiên trong phản hồi (chịu được hàng rào ```json). Không hợp lệ -> ValueError."""
    text = (raw or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I)
    start = text.find("{")
    if start < 0:
        raise ValueError("no JSON object")
    obj, _ = json.JSONDecoder().raw_decode(text[start:])
    if not isinstance(obj, dict):
        raise ValueError("plan is not an object")
    return obj


class PlannerAgent:
    def __init__(self, llm: LLMClient, tools: ToolRegistry, settings: Settings):
        self._llm = llm
        self._tools = tools
        self._settings = settings

    def plan(
        self,
        question: str,
        *,
        dataset_id: Optional[str] = None,
        chart_hint: str = "detect",
        show_sql: bool = False,
        use_llm: bool = True,
    ) -> PlanOutcome:
        schema = self._tools.get("describe_schema").run(dataset_id=dataset_id)
        vi = is_vi_question(question)
        draft = draft_plan(question, schema, vi)
        if draft.clarification:
            return PlanOutcome(clarification=draft.clarification)

        source = "rules"
        raw_steps: List[Dict[str, Any]] = draft.steps
        if draft.needs_llm and use_llm and self._llm.is_available():
            try:
                reply = self._llm.complete(
                    build_planner_prompt(question, schema, draft.steps),
                    system=PLANNER_SYSTEM_PROMPT,
                    temperature=0.0,
                    max_tokens=600,
                )
            except Exception:  # không gọi được LLM: dùng quy tắc, đây không phải kế hoạch sai
                source = "rules_fallback"
            else:
                try:
                    obj = parse_plan_json(reply)
                except ValueError:
                    return PlanOutcome(clarification=_unclear(vi))
                if obj.get("clarification"):
                    return PlanOutcome(clarification=str(obj["clarification"]))
                raw_steps, source = obj.get("steps"), "llm"
        try:
            steps = validate_plan(raw_steps, schema, vi=vi, max_tables=self._settings.eda_max_tables)
        except PlanError as e:
            return PlanOutcome(clarification=str(e))
        plan = ProcessingPlan(
            steps=steps,
            chart=resolve_chart_plan(question, chart_hint),
            show_sql=show_sql or detect_sql_request(question),
            source=source,
        )
        return PlanOutcome(plan=plan)


def _unclear(vi: bool) -> str:
    return ("Mình chưa hiểu rõ bạn muốn truy vấn một kết quả cụ thể hay phân tích tổng quan dữ liệu. "
            "Bạn có thể nói rõ hơn (bảng/cột nào, muốn xem gì)?" if vi else
            "I'm not sure whether you want a specific query result or a data overview. "
            "Could you say which table/columns and what you want to see?")
