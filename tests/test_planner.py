"""Giai đoạn 4 + 6: quy tắc dự phòng, validator, hợp đồng kế hoạch JSON của LLM Planner (steps / chart / clarification)."""
import json

import pytest

from backend.tools.intent import canonical_chart_type
from backend.tools.planner import PlanError, draft_plan, resolve_planned_chart, validate_llm_plan, validate_plan
from tests.conftest import FakeLLM

SCHEMA = {
    "orders": [
        {"name": "order_id", "type": "INTEGER", "pk": True},
        {"name": "revenue", "type": "REAL", "pk": False},
        {"name": "region", "type": "TEXT", "pk": False},
    ],
    "customers": [
        {"name": "customer_id", "type": "INTEGER", "pk": True},
        {"name": "name", "type": "TEXT", "pk": False},
    ],
}
ONE = {"orders": SCHEMA["orders"]}


def ops(draft):
    return [(s["op"], s.get("table")) for s in draft.steps]


def test_plain_data_questions_are_query_and_skip_llm():
    for q in ["Doanh thu theo vùng", "top 5 products by revenue", "How many orders in 2024?", "Vẽ biểu đồ cột doanh thu"]:
        d = draft_plan(q, SCHEMA)
        assert ops(d) == [("query", None)] and not d.needs_llm and not d.clarification


@pytest.mark.parametrize(
    "q,op",
    [
        ("Cho tôi tổng quan về bảng orders", "table_overview"),
        ("kiểm tra giá trị thiếu của orders", "data_quality"),
        ("orders có dòng trùng lặp không?", "data_quality"),
        ("thống kê mô tả cột revenue của orders", "numeric_summary"),
        ("top giá trị phổ biến của cột region trong orders", "categorical_summary"),
        ("tương quan giữa các cột số của orders", "correlation"),
        ("tong quan bang orders", "table_overview"),  # không dấu
        ("describe the orders table", "numeric_summary"),
    ],
)
def test_clear_eda_intents_route_by_rules(q, op):
    d = draft_plan(q, SCHEMA)
    assert [s["op"] for s in d.steps] == [op] and d.steps[0]["table"] == "orders" and not d.needs_llm


def test_column_mentions_are_resolved_and_not_treated_as_query():
    d = draft_plan("thống kê mô tả cột revenue của orders", SCHEMA)
    assert d.steps == [{"op": "numeric_summary", "table": "orders", "columns": ["revenue"]}] and not d.needs_llm


def test_multiple_eda_ops_in_one_turn():
    d = draft_plan("tổng quan, kiểm tra giá trị thiếu và tương quan của bảng orders", SCHEMA)
    assert [s["op"] for s in d.steps] == ["table_overview", "data_quality", "correlation"] and not d.needs_llm


def test_overview_without_table_covers_all_tables_but_stats_ask_which():
    assert ops(draft_plan("tổng quan dữ liệu", SCHEMA)) == [("table_overview", "orders"), ("table_overview", "customers")]
    d = draft_plan("thống kê mô tả", SCHEMA)
    assert d.clarification and "orders" in d.clarification and not d.steps
    assert ops(draft_plan("thống kê mô tả", ONE)) == [("numeric_summary", "orders")]  # một bảng: không cần hỏi


def test_column_name_picks_table():
    d = draft_plan("thống kê mô tả revenue", SCHEMA)
    assert ops(d) == [("numeric_summary", "orders")]


def test_distribution_splits_numeric_and_categorical():
    d = draft_plan("phân phối của revenue và region trong orders", SCHEMA)
    assert [(s["op"], s["columns"]) for s in d.steps] == [
        ("numeric_summary", ["revenue"]), ("categorical_summary", ["region"])]


def test_combined_question_needs_llm():
    d = draft_plan("Doanh thu theo vùng và kiểm tra giá trị thiếu của orders", SCHEMA)
    assert d.needs_llm and [s["op"] for s in d.steps] == ["query", "data_quality"]


# ── validator ──────────────────────────────────────────────────────────────
def test_validator_accepts_and_canonicalizes_names():
    steps = validate_plan(
        [{"op": "numeric_summary", "table": "ORDERS", "columns": ["Revenue"]},
         {"op": "categorical_summary", "table": "orders", "params": {"top_k": 5, "junk": 1}}],
        SCHEMA,
    )
    assert steps[0].table == "orders" and steps[0].columns == ("revenue",)
    assert steps[1].params == {"top_k": 5}  # tham số lạ bị loại


@pytest.mark.parametrize(
    "raw",
    [
        [],
        "drop table",
        [{"op": "python", "code": "print(1)"}],
        [{"op": "numeric_summary", "table": "ghost"}],
        [{"op": "numeric_summary", "table": "orders", "columns": ["nope"]}],
        [{"op": "categorical_summary", "table": "orders", "params": {"top_k": 999}}],
        [{"op": "correlation", "table": "orders", "params": {"method": "kendall"}}],
        [{"op": "query"}, {"op": "query"}],
        [{"op": "numeric_summary"}],  # nhiều bảng, không nêu bảng/cột -> mơ hồ
        [{"op": "numeric_summary", "table": "orders", "columns": "revenue"}],
    ],
)
def test_validator_rejects_invalid_plans_with_clarification(raw):
    with pytest.raises(PlanError) as e:
        validate_plan(raw, SCHEMA)
    assert str(e.value)


def test_validator_limits_steps_and_dedupes():
    many = [{"op": "table_overview", "table": "orders"}] * 3 + [{"op": "data_quality", "table": t} for t in SCHEMA]
    assert len(validate_plan(many, SCHEMA)) == 3
    with pytest.raises(PlanError):
        validate_plan([{"op": "numeric_summary", "table": "orders", "columns": [c["name"]]} for c in SCHEMA["orders"]] * 1
                      + [{"op": "correlation", "table": "orders"}] * 1 + [{"op": "data_quality", "table": "customers"}] * 1
                      + [{"op": "table_overview", "table": "customers"}] * 1, SCHEMA, max_steps=3)


def test_overview_all_tables_respects_max_tables():
    big = {f"t{i}": [{"name": "a", "type": "INT", "pk": False}] for i in range(12)}
    assert len(validate_plan([{"op": "table_overview"}], big, max_tables=5)) == 5


# ── Hợp đồng kế hoạch LLM (giai đoạn 6) ─────────────────────────────────────
NO_CHART = {"requested": False, "type": None, "goal": None}


def plan_json(steps, chart=None, **extra):
    return json.dumps({"steps": steps, "chart": NO_CHART if chart is None else chart, "clarification": None, **extra})


def test_canonical_chart_type_aliases():
    assert canonical_chart_type("Bar") == "bar chart" and canonical_chart_type("box_plot") == "box plot"
    assert canonical_chart_type("PIE CHART") == "pie chart" and canonical_chart_type("heat map") == "heatmap"
    assert canonical_chart_type("radar") is None and canonical_chart_type(5) is None


def test_validate_llm_plan_accepts_full_contract():
    out = validate_llm_plan(
        {"steps": [{"op": "query", "question": "doanh thu theo vùng"},
                   {"op": "correlation", "table": "Orders", "columns": ["revenue"], "params": {"method": "Spearman"}}],
         "chart": {"requested": True, "type": "pie", "goal": "Composition"}},
        "q", SCHEMA)
    assert [s.op for s in out.steps] == ["query", "correlation"] and out.steps[0].question == "doanh thu theo vùng"
    assert out.steps[1].table == "orders" and out.steps[1].params == {"method": "spearman"}
    assert out.chart.requested and out.chart.chart_type == "pie chart" and out.chart.goal == "composition"
    assert out.chart.origin == "prompt"


def test_chart_type_and_goal_may_be_undetermined():
    c = validate_llm_plan({"steps": [{"op": "query"}], "chart": {"requested": True, "type": None, "goal": None}},
                          "q", SCHEMA).chart
    assert c.requested and c.chart_type is None and c.goal is None


@pytest.mark.parametrize(
    "chart",
    [{"requested": True, "type": "radar"}, {"requested": True, "goal": "magic"}, {"type": "bar chart"},
     {"requested": "yes"}, "bar chart", [], {"requested": True, "type": ["bar"]}],
)
def test_invalid_chart_part_is_rejected(chart):
    with pytest.raises(PlanError):
        validate_llm_plan({"steps": [{"op": "query"}], "chart": chart}, "q", SCHEMA)


def test_invalid_steps_reject_whole_plan_even_if_chart_ok():
    with pytest.raises(PlanError):
        validate_llm_plan({"steps": [{"op": "query"}, {"op": "data_quality", "table": "ghost"}],
                           "chart": {"requested": True}}, "q", SCHEMA)
    with pytest.raises(PlanError):
        validate_llm_plan([], "q", SCHEMA)


def test_chart_hint_has_priority_over_llm_chart():
    llm = {"requested": True, "type": "bar chart", "goal": "comparison"}
    assert resolve_planned_chart(llm, "q", "none") == resolve_planned_chart(NO_CHART, "q", "none")
    assert not resolve_planned_chart(llm, "q", "none").requested
    c = resolve_planned_chart(llm, "q", "pie chart")  # loại cụ thể của tùy chọn phụ thắng LLM
    assert c.requested and c.chart_type == "pie chart" and c.origin == "option" and c.goal == "comparison"
    c = resolve_planned_chart(NO_CHART, "q", "auto")  # auto: vẽ dù LLM cho là không yêu cầu, loại do hệ thống chọn
    assert c.requested and c.chart_type is None and c.origin == "option"
    c = resolve_planned_chart({"requested": True, "type": "histogram"}, "q", "auto")
    assert c.chart_type == "histogram"
    assert not resolve_planned_chart(NO_CHART, "q", "detect").requested


def test_missing_chart_block_falls_back_to_intent_rules():
    assert resolve_planned_chart(None, "Vẽ biểu đồ tròn doanh thu", "detect").chart_type == "pie chart"
    assert not resolve_planned_chart(None, "Doanh thu theo vùng", "detect").requested


# ── PlannerAgent ───────────────────────────────────────────────────────────
@pytest.fixture
def agent_env(make_container):
    def _make(planner_response=None, available=True, raises=None, planner_raises=None, **settings):
        llm = FakeLLM(name="deepseek", available=available, planner_response=planner_response,
                      raises=raises, planner_raises=planner_raises)
        return make_container(deepseek=llm, **settings), llm
    return _make


def _plan(c, q, **kw):
    return c.planner_agent.plan(q, **kw)


def test_llm_plans_every_question_when_available(agent_env):
    reply = plan_json([{"op": "query"}])
    c, llm = agent_env(reply)
    out = _plan(c, "Doanh thu theo vùng")
    assert out.plan.source == "llm" and out.plan.fallback_reason is None and len(llm.planner_calls) == 1
    # kể cả câu hỏi có từ khóa EDA rõ ràng: LLM quyết định, quy tắc không chen vào
    c, llm = agent_env(plan_json([{"op": "query", "question": "orders có dòng trùng không"}]))
    out = _plan(c, "kiểm tra giá trị thiếu của orders")
    assert [s.op for s in out.plan.steps] == ["query"] and out.plan.source == "llm"


def test_planner_prompt_has_schema_and_allowlists_but_no_data_or_rule_draft(agent_env):
    c, llm = agent_env(plan_json([{"op": "query"}]))
    _plan(c, "Doanh thu theo vùng")
    call = llm.planner_calls[0]
    assert "- orders(order_id INTEGER" in call["prompt"] and "customers(" in call["prompt"]
    assert "Rule-based draft" not in call["prompt"]
    for word in ("table_overview", "data_quality", "numeric_summary", "categorical_summary", "correlation",
                 "violin plot", "composition", "clarification"):
        assert word in call["system"]
    assert call["temperature"] == 0.0 and call["max_tokens"] == c.settings.planner_max_tokens


def test_combined_query_eda_chart_plan(agent_env):
    reply = plan_json([{"op": "query", "question": "doanh thu theo vùng"}, {"op": "data_quality", "table": "orders"}],
                      {"requested": True, "type": "bar chart", "goal": "comparison"})
    c, _ = agent_env(reply)
    out = _plan(c, "Vẽ cột doanh thu theo vùng và kiểm tra giá trị thiếu của orders", show_sql=True)
    p = out.plan
    assert [s.op for s in p.steps] == ["query", "data_quality"] and p.steps[0].question == "doanh thu theo vùng"
    assert p.chart.requested and p.chart.chart_type == "bar chart" and p.chart.goal == "comparison"
    assert p.chart.origin == "prompt" and p.show_sql


def test_eda_only_plan_with_chart(agent_env):
    c, _ = agent_env(plan_json([{"op": "correlation", "table": "orders"}], {"requested": True, "type": "heatmap"}))
    p = _plan(c, "vẽ heatmap tương quan của orders").plan
    assert [s.op for s in p.steps] == ["correlation"] and p.chart.chart_type == "heatmap"


def test_chart_hint_overrides_llm_plan(agent_env):
    reply = plan_json([{"op": "query"}], {"requested": True, "type": "bar chart", "goal": "trend"})
    c, _ = agent_env(reply)
    assert not _plan(c, "Doanh thu theo vùng", chart_hint="none").plan.chart.requested
    p = _plan(c, "Doanh thu theo vùng", chart_hint="line chart").plan
    assert p.chart.chart_type == "line chart" and p.chart.origin == "option"
    c, _ = agent_env(plan_json([{"op": "query"}]))
    assert _plan(c, "Doanh thu theo vùng", chart_hint="auto").plan.chart.requested
    assert not _plan(c, "Doanh thu theo vùng").plan.chart.requested


def test_show_sql_comes_from_option_or_question(agent_env):
    c, _ = agent_env(plan_json([{"op": "query"}]))
    assert not _plan(c, "Doanh thu theo vùng").plan.show_sql
    assert _plan(c, "Doanh thu theo vùng", show_sql=True).plan.show_sql
    assert _plan(c, "Cho xem câu lệnh SQL doanh thu theo vùng").plan.show_sql


@pytest.mark.parametrize(
    "reply",
    [
        "not json",
        "[]",
        '{"steps": [{"op": "exec", "code": "x"}]}',
        '{"steps": [{"op": "data_quality", "table": "ghost"}]}',
        '{"steps": [{"op": "numeric_summary", "table": "orders", "columns": ["nope"]}]}',
        '{"steps": [{"op": "query"}, {"op": "query"}]}',
        '{"steps": [{"op": "categorical_summary", "table": "orders", "params": {"top_k": 999}}]}',
        '{"steps": []}',
        '{"steps": "query"}',
        '{"steps": [{"op": "query"}], "chart": {"requested": true, "type": "radar"}}',
        '{"steps": [{"op": "query"}], "chart": {"requested": true, "goal": "magic"}}',
        '{"steps": [{"op": "query"}, {"op": "data_quality", "table": "ghost"}], "chart": {"requested": false}}',
    ],
)
def test_invalid_llm_plan_asks_user_and_runs_nothing(agent_env, reply):
    c, _ = agent_env(reply)
    out = _plan(c, "Doanh thu theo vùng và kiểm tra giá trị thiếu của orders")
    assert out.plan is None and out.clarification


def test_llm_clarification_passthrough(agent_env):
    c, _ = agent_env('{"steps": [], "chart": {"requested": false}, "clarification": "Bạn muốn bảng nào?"}')
    out = _plan(c, "Thống kê giúp tôi")
    assert out.plan is None and out.clarification == "Bạn muốn bảng nào?"


def test_llm_error_falls_back_to_rules(agent_env):
    c, llm = agent_env(planner_raises=RuntimeError("down"))
    out = _plan(c, "Doanh thu theo vùng và kiểm tra giá trị thiếu của orders", chart_hint="auto")
    p = out.plan
    assert p.source == "rules_fallback" and p.fallback_reason.startswith("llm_error: RuntimeError")
    assert [s.op for s in p.steps] == ["query", "data_quality"] and p.chart.requested


def test_llm_unavailable_mock_and_rules_mode_use_rules(agent_env):
    q = "Doanh thu theo vùng và kiểm tra giá trị thiếu của orders"
    c, llm = agent_env(available=False)
    p = _plan(c, q).plan
    assert p.source == "rules" and p.fallback_reason == "llm_unavailable" and llm.planner_calls == []
    c, llm = agent_env(plan_json([{"op": "query"}]))
    p = _plan(c, q, use_llm=False).plan  # mock
    assert p.source == "rules" and p.fallback_reason == "mock" and llm.planner_calls == []
    c, llm = agent_env(plan_json([{"op": "query"}]), planner_mode="rules")
    p = _plan(c, "kiểm tra giá trị thiếu của orders").plan
    assert p.source == "rules" and p.fallback_reason == "planner_mode_rules" and llm.planner_calls == []
    assert [s.op for s in p.steps] == ["data_quality"]


def test_rules_fallback_still_asks_when_ambiguous(agent_env):
    c, _ = agent_env(available=False)
    out = _plan(c, "thống kê mô tả")
    assert out.plan is None and "orders" in out.clarification
