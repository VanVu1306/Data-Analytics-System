"""Giai đoạn 4: định tuyến bằng quy tắc, validator và LLM planner."""
import json

import pytest

from backend.tools.planner import PlanError, draft_plan, validate_plan
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


# ── PlannerAgent ───────────────────────────────────────────────────────────
@pytest.fixture
def agent_env(make_container):
    def _make(response="", available=True, raises=None):
        llm = FakeLLM(name="deepseek", available=available, response=response, raises=raises)
        c = make_container(deepseek=llm)
        return c, llm
    return _make


def _plan(c, q, **kw):
    return c.planner_agent.plan(q, **kw)


def test_planner_agent_pure_cases_do_not_call_llm(agent_env):
    c, llm = agent_env()
    assert [s.op for s in _plan(c, "Doanh thu theo vùng").plan.steps] == ["query"]
    assert [s.op for s in _plan(c, "kiểm tra giá trị thiếu của orders").plan.steps] == ["data_quality"]
    assert llm.calls == []


def test_planner_agent_uses_llm_plan_for_combined_questions(agent_env):
    reply = '```json\n' + json.dumps({"steps": [{"op": "query", "question": "doanh thu theo vùng"},
                                                 {"op": "data_quality", "table": "orders"}]}) + '\n```'
    c, llm = agent_env(reply)
    out = _plan(c, "Doanh thu theo vùng và kiểm tra giá trị thiếu của orders", chart_hint="auto", show_sql=True)
    assert out.plan.source == "llm" and [s.op for s in out.plan.steps] == ["query", "data_quality"]
    assert out.plan.steps[0].question == "doanh thu theo vùng"
    assert out.plan.chart.requested and out.plan.show_sql
    assert "orders(" in llm.calls[0]["prompt"] and "Allowed operations" in llm.calls[0]["system"]


@pytest.mark.parametrize(
    "reply",
    ["not json", '{"steps": [{"op": "exec", "code": "x"}]}', '{"steps": [{"op": "data_quality", "table": "ghost"}]}'],
)
def test_planner_agent_invalid_llm_plan_asks_user(agent_env, reply):
    c, _ = agent_env(reply)
    out = _plan(c, "Doanh thu theo vùng và kiểm tra giá trị thiếu của orders")
    assert out.plan is None and out.clarification


def test_planner_agent_llm_clarification_passthrough(agent_env):
    c, _ = agent_env('{"steps": [], "clarification": "Bạn muốn bảng nào?"}')
    assert _plan(c, "Doanh thu theo vùng và kiểm tra giá trị thiếu").clarification == "Bạn muốn bảng nào?"


def test_planner_agent_falls_back_to_rules_without_llm_or_on_error(agent_env):
    for kw, source in (({"available": False}, "rules"), ({"raises": RuntimeError("down")}, "rules_fallback")):
        c, _ = agent_env(**kw)
        out = _plan(c, "Doanh thu theo vùng và kiểm tra giá trị thiếu của orders")
        assert out.plan.source == source and [s.op for s in out.plan.steps] == ["query", "data_quality"]
    c, llm = agent_env("{}")
    out = _plan(c, "Doanh thu theo vùng và kiểm tra giá trị thiếu của orders", use_llm=False)  # mock
    assert out.plan.source == "rules" and llm.calls == []
