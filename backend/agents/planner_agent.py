"""
backend/agents/planner_agent.py
-------------------------------
PlannerAgent (giai đoạn 6): câu hỏi -> `ProcessingPlan` đã xác thực. LLM là cơ chế quyết định chính.

    LLM sẵn sàng (PLANNER_MODE=llm, không dùng mock)
        -> LLM trả MỘT kế hoạch JSON {steps, chart, clarification} (chỉ nhận schema, không nhận dữ liệu)
        -> `validate_llm_plan` kiểm tra JSON, operation, số bước, bảng/cột, tham số, loại biểu đồ
        -> kế hoạch sai / không phải JSON / mơ hồ: hỏi lại người dùng, KHÔNG chạy một phần kế hoạch   (source = "llm")
    LLM không dùng được -> bản nháp bằng quy tắc (regex) rồi cũng qua `validate_plan`
        PLANNER_MODE=rules hoặc mock                 source = "rules"          (fallback_reason: planner_mode_rules | mock)
        LLM chưa cấu hình                            source = "rules"          (fallback_reason: llm_unavailable)
        gọi LLM lỗi (mạng/hết hạn mức)               source = "rules_fallback" (fallback_reason: llm_error: ...)

Ranh giới trách nhiệm: Planner quyết định CẦN LÀM GÌ; SQL Agent sinh SQL; EDA tools tính toán; Viz Agent / chart tools vẽ.
Tùy chọn phụ `chart_hint` và `show_sql` của request luôn được ưu tiên hơn nhận định của LLM.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional

from backend.config import Settings
from backend.llm import LLMClient
from backend.prompts import build_planner_prompt, build_planner_system_prompt
from backend.tools import CHART_GOALS, CHART_TYPES, ToolRegistry, detect_sql_request, is_vi_question, resolve_chart_plan
from backend.tools.planner import PlanError, PlanOutcome, ProcessingPlan, draft_plan, validate_llm_plan, validate_plan


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
        self._system = build_planner_system_prompt(CHART_TYPES, CHART_GOALS)

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
        show = show_sql or detect_sql_request(question)

        reason = self._llm_unavailable_reason(use_llm)
        if reason is None:
            try:
                reply = self._llm.complete(
                    build_planner_prompt(question, schema),
                    system=self._system,
                    temperature=0.0,
                    max_tokens=self._settings.planner_max_tokens,
                )
            except Exception as e:  # không gọi được LLM: dùng quy tắc, đây không phải kế hoạch sai
                return self._from_rules(question, schema, vi, chart_hint, show, "rules_fallback",
                                        f"llm_error: {type(e).__name__}: {e}"[:300])
            return self._from_llm(reply, question, schema, vi, chart_hint, show)
        return self._from_rules(question, schema, vi, chart_hint, show, "rules", reason)

    # ── LLM ────────────────────────────────────────────────────────────────
    def _llm_unavailable_reason(self, use_llm: bool) -> Optional[str]:
        if not use_llm:
            return "mock"
        if self._settings.planner_mode == "rules":
            return "planner_mode_rules"
        if not self._llm.is_available():
            return "llm_unavailable"
        return None

    def _from_llm(self, reply: str, question: str, schema: dict, vi: bool, chart_hint: str, show: bool) -> PlanOutcome:
        try:
            obj = parse_plan_json(reply)
        except ValueError:
            return PlanOutcome(clarification=_unclear(vi))
        if obj.get("clarification"):
            return PlanOutcome(clarification=str(obj["clarification"]))
        try:
            llm_plan = validate_llm_plan(
                obj, question, schema, chart_hint=chart_hint, vi=vi, max_tables=self._settings.eda_max_tables
            )
        except PlanError as e:
            return PlanOutcome(clarification=str(e))
        return PlanOutcome(plan=ProcessingPlan(llm_plan.steps, llm_plan.chart, show, source="llm"))

    # ── Quy tắc (dự phòng) ─────────────────────────────────────────────────
    def _from_rules(
        self, question: str, schema: dict, vi: bool, chart_hint: str, show: bool, source: str, reason: str
    ) -> PlanOutcome:
        draft = draft_plan(question, schema, vi)
        if draft.clarification:
            return PlanOutcome(clarification=draft.clarification)
        try:
            steps = validate_plan(draft.steps, schema, vi=vi, max_tables=self._settings.eda_max_tables)
        except PlanError as e:
            return PlanOutcome(clarification=str(e))
        plan = ProcessingPlan(
            steps, resolve_chart_plan(question, chart_hint), show, source=source, fallback_reason=reason
        )
        return PlanOutcome(plan=plan)


def _unclear(vi: bool) -> str:
    return ("Mình chưa hiểu rõ yêu cầu của bạn (muốn truy vấn kết quả cụ thể, phân tích tổng quan dữ liệu hay vẽ biểu đồ). "
            "Bạn có thể nói rõ hơn (bảng/cột nào, muốn xem gì)?" if vi else
            "I could not work out what you need (a specific query result, a data overview or a chart). "
            "Could you say which table/columns and what you want to see?")
