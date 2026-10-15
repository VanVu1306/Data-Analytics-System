"""Giai đoạn 6: kế hoạch do LLM lập được Orchestrator thực thi đúng ranh giới Planner / SQL Agent / EDA / Viz."""
import io
import json

import pytest
from fastapi.testclient import TestClient

from backend.container import get_container
from backend.main import create_app
from tests.conftest import FakeLLM

CSV = ("ten,diem,lop\n" + "\n".join(f"{n},{d},{l}" for n, d, l in [
    ("An", 8, "A"), ("Binh", 6, "A"), ("Chi", "", "B"), ("Dung", 9, "B"), ("Em", 7, "B")])).encode()
NO_CHART = {"requested": False, "type": None, "goal": None}
SQL = "SELECT lop, AVG(diem) AS diem_tb FROM diem GROUP BY lop"


def plan(steps, chart=None):
    return json.dumps({"steps": steps, "chart": chart or NO_CHART, "clarification": None})


@pytest.fixture
def env(make_container):
    def _make(planner_response=None, sql=SQL, **kw):
        llm = FakeLLM(name="deepseek", response=sql, planner_response=planner_response)
        c = make_container(deepseek=llm, **kw)
        app = create_app()
        app.dependency_overrides[get_container] = lambda: c
        client = TestClient(app)
        r = client.post("/api/datasets", files={"file": ("diem.csv", io.BytesIO(CSV))})
        assert r.status_code == 201
        return client, llm, r.json()["dataset_id"]
    return _make


def ask(client, ds, question="Điểm trung bình theo lớp", **kw):
    r = client.post("/api/query", json={"question": question, "dataset_id": ds, **kw})
    assert r.status_code == 200, r.text
    return r.json()


def sql_calls(llm):
    return [c for c in llm.calls if "planner" not in (c["system"] or "")]


def test_plain_query_plan(env):
    client, llm, ds = env(plan([{"op": "query"}]))
    d = ask(client, ds)
    assert d["status"] == "ok" and d["plan"]["source"] == "llm" and d["plan"]["fallback_reason"] is None
    assert d["plan"]["chart"] == {"requested": False, "type": None, "goal": None, "origin": "none"}
    assert d["table"]["columns"] == ["lop", "diem_tb"] and d["eda"] == [] and d["chart"]["status"] == "not_requested"
    assert len(llm.planner_calls) == 1


def test_query_subquestion_goes_to_sql_agent(env):
    client, llm, ds = env(plan([{"op": "query", "question": "điểm trung bình theo lớp"}]))
    ask(client, ds, "Điểm trung bình theo lớp và vẽ biểu đồ")
    assert "điểm trung bình theo lớp" in llm.calls[0]["prompt"] and "vẽ biểu đồ" not in llm.calls[0]["prompt"]


def test_combined_query_eda_and_chart(env):
    chart = {"requested": True, "type": "bar chart", "goal": "comparison"}
    client, llm, ds = env(plan([{"op": "query"}, {"op": "data_quality", "table": "diem"}], chart))
    d = ask(client, ds, "Vẽ cột điểm trung bình theo lớp và kiểm tra giá trị thiếu")
    assert d["status"] == "ok" and d["table"] and [e["operation"] for e in d["eda"]] == ["data_quality"]
    assert [s["operation"] for s in d["plan"]["steps"]] == ["query", "data_quality"]
    assert d["plan"]["chart"]["type"] == "bar chart" and d["plan"]["chart"]["origin"] == "prompt"
    assert d["chart"]["status"] == "ok" and d["chart"]["png_base64"] and d["chart"]["meta"]["chart_type"] == "bar chart"
    assert d["answer"]["text"]


def test_eda_only_plan_runs_no_sql_agent(env):
    client, llm, ds = env(plan([{"op": "numeric_summary", "table": "diem"}]))
    d = ask(client, ds, "Cho mình xem phân bố điểm")
    assert d["status"] == "ok" and d["table"] is None and d["eda"][0]["operation"] == "numeric_summary"
    assert sql_calls(llm) == []  # EDA do tool xác định tính, không qua SQL Agent


def test_invalid_plan_asks_user_and_executes_nothing(env):
    bad = plan([{"op": "query"}, {"op": "data_quality", "table": "ghost"}], {"requested": True})
    client, llm, ds = env(bad)
    d = ask(client, ds)
    assert d["status"] == "needs_clarification" and d["clarification"] and d["table"] is None
    assert d["eda"] == [] and d["chart"]["status"] == "not_requested" and llm.calls == []  # không chạy một phần


def test_llm_clarification_is_returned(env):
    client, llm, ds = env('{"steps": [], "chart": {"requested": false}, "clarification": "Bạn muốn xem cột nào?"}')
    d = ask(client, ds, "Phân tích giúp mình")
    assert d["status"] == "needs_clarification" and d["clarification"] == "Bạn muốn xem cột nào?" and llm.calls == []


@pytest.mark.parametrize("hint,expect_status", [("none", "not_requested"), ("pie chart", "ok")])
def test_chart_hint_beats_llm_chart(env, hint, expect_status):
    chart = {"requested": True, "type": "bar chart", "goal": "comparison"}
    client, _, ds = env(plan([{"op": "query"}], chart))
    d = ask(client, ds, chart_hint=hint)
    assert d["chart"]["status"] == expect_status and d["table"]
    if hint != "none":
        assert d["chart"]["chart_type"] == "pie chart" and d["plan"]["chart"]["origin"] == "option"


def test_unfit_chart_fails_alone_and_keeps_answer_table_eda(env):
    chart = {"requested": True, "type": "heatmap"}
    client, _, ds = env(plan([{"op": "query"}, {"op": "data_quality", "table": "diem"}], chart))
    d = ask(client, ds)
    assert d["status"] == "ok" and d["chart"]["status"] == "error" and d["chart"]["error"]
    assert d["answer"]["text"] and d["table"] and d["eda"] and d["eda"][0]["status"] == "ok"


def test_chart_from_overview_eda_not_feasible_gives_reason(env):
    client, _, ds = env(plan([{"op": "table_overview", "table": "diem"}], {"requested": True}))
    d = ask(client, ds, "Tổng quan bảng diem rồi vẽ biểu đồ")
    assert d["status"] == "ok" and d["eda"] and d["chart"]["status"] == "error" and d["chart"]["error"]


def test_code_mode_plan_chart_without_query_reports_reason_and_keeps_eda(env):
    client, _, ds = env(plan([{"op": "correlation", "table": "diem"}], {"requested": True, "type": "heatmap"}),
                        viz_mode="code")
    d = ask(client, ds, "Vẽ heatmap tương quan")
    assert d["chart"]["status"] == "error" and "VIZ_MODE=code" in d["chart"]["error"] and d["eda"]


def test_code_mode_uses_viz_agent_for_llm_chart_plan(env):
    client, _, ds = env(plan([{"op": "query"}], {"requested": True, "type": "bar chart"}), viz_mode="code")
    d = ask(client, ds, "Vẽ biểu đồ điểm theo lớp", use_mock_viz=True)
    assert d["chart"]["status"] == "ok" and d["chart"]["source"] == "mock" and d["chart"]["requested_by"] == "prompt"


def test_llm_planner_failure_falls_back_to_rules_and_still_answers(make_container):
    llm = FakeLLM(name="deepseek", response=SQL, planner_raises=RuntimeError("quota"))
    c = make_container(deepseek=llm)
    app = create_app()
    app.dependency_overrides[get_container] = lambda: c
    client = TestClient(app)
    ds = client.post("/api/datasets", files={"file": ("diem.csv", io.BytesIO(CSV))}).json()["dataset_id"]
    d = ask(client, ds, "Điểm trung bình theo lớp")
    assert d["status"] == "ok" and d["plan"]["source"] == "rules_fallback"
    assert d["plan"]["fallback_reason"].startswith("llm_error") and d["table"]


def test_mock_mode_never_calls_planner_llm(env):
    client, llm, ds = env(plan([{"op": "query"}]))
    d = ask(client, ds, "Điểm trung bình theo lớp", use_mock_sql=True)
    assert d["plan"]["source"] == "rules" and d["plan"]["fallback_reason"] == "mock" and llm.planner_calls == []
