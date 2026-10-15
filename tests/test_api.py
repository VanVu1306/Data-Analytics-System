"""Test end-to-end qua HTTP (FastAPI TestClient) với LLM giả."""
import base64

import pytest
from fastapi.testclient import TestClient

from backend.container import get_container
from backend.main import create_app
from tests.conftest import FakeLLM, FakeOllama

VALID_CODE = "import matplotlib.pyplot as plt\nplt.figure(figsize=(10, 6))\nplt.plot([1, 2, 3])\nplt.tight_layout()"


@pytest.fixture
def client_for(make_container):
    def _make(**kw):
        kw.setdefault("viz_mode", "code")  # các test cũ kiểm tra đường code-gen; chế độ spec có test riêng
        container = make_container(**kw)
        app = create_app()
        app.dependency_overrides[get_container] = lambda: container
        return TestClient(app), container

    return _make


def test_health_and_schema_and_models(client_for):
    client, _ = client_for(ollama=FakeOllama(models=["llama3.2", "llama3.2:1b"]))
    h = client.get("/api/health").json()
    assert h == {
        "status": "ok",
        "db_exists": True,
        "llm_provider": "deepseek",
        "llm_configured": False,
        "ollama_running": True,
        "ollama_default_model": "llama3.2",
        "max_upload_mb": 50,
        "viz_mode": "code",
    }
    assert "orders" in client.get("/api/schema").json()["tables"]
    assert client.get("/api/models").json() == {"models": ["llama3.2", "llama3.2:1b"], "default": "llama3.2"}


def test_query_default_returns_answer_and_table_without_chart(client_for):
    client, _ = client_for()
    r = client.post("/api/query", json={"question": "Show monthly revenue in 2024"})
    assert r.status_code == 200
    d = r.json()
    assert d["status"] == "ok"
    assert d["evidence"]["sql_source"] == "mock"
    assert d["table"]["columns"] == ["month", "monthly_revenue"]
    assert len(d["table"]["rows"]) == 12
    assert d["answer"]["text"] and d["answer"]["source"] == "template"
    assert d["evidence"]["row_count"] == 12 and d["evidence"]["column_count"] == 2
    assert d["evidence"]["elapsed_ms"] is not None
    # mặc định không vẽ: không gọi nhánh biểu đồ
    assert d["chart"]["status"] == "not_requested" and d["chart"]["png_base64"] is None
    assert d["chart"]["code"] is None and d["chart"]["requested_by"] == "none"


def test_chart_requested_in_prompt(client_for):
    client, _ = client_for()
    d = client.post("/api/query", json={"question": "Vẽ biểu đồ doanh thu theo tháng năm 2024"}).json()
    assert d["status"] == "ok" and d["chart"]["status"] == "ok" and d["chart"]["requested_by"] == "prompt"
    assert base64.b64decode(d["chart"]["png_base64"]).startswith(b"\x89PNG")
    assert "plt.figure" in d["chart"]["code"] and d["chart"]["source"] == "mock"
    assert d["answer"]["text"] and d["table"] is not None


def test_chart_option_auto_and_none(client_for):
    client, _ = client_for()
    d = client.post("/api/query", json={"question": "Revenue by region", "chart_hint": "auto"}).json()
    assert d["chart"]["status"] == "ok" and d["chart"]["requested_by"] == "option" and d["chart"]["chart_type"] is None
    # "none" thắng cả khi câu hỏi yêu cầu vẽ
    d = client.post("/api/query", json={"question": "Plot revenue by region", "chart_hint": "none"}).json()
    assert d["chart"]["status"] == "not_requested" and d["table"] is not None


def test_specific_chart_type_from_option_and_from_prompt(client_for):
    client, _ = client_for()
    d = client.post("/api/query", json={"question": "Revenue by region", "chart_hint": "pie chart"}).json()
    assert d["chart"]["chart_type"] == "pie chart" and "ax.pie" in d["chart"]["code"]
    assert "(as a pie chart)" in d["chart"]["prompt"]
    # chart type chỉ ảnh hưởng nhánh biểu đồ, không lọt vào prompt SQL
    assert "pie chart" not in d["evidence"]["sql_prompt"]

    # loại nêu trong câu hỏi; "tháng" không được kéo về line chart khi người dùng nói rõ biểu đồ cột
    d = client.post("/api/query", json={"question": "Vẽ biểu đồ cột doanh thu theo tháng"}).json()
    assert d["chart"]["chart_type"] == "bar chart" and "ax.bar(" in d["chart"]["code"]


def test_invalid_chart_hint_rejected(client_for):
    client, _ = client_for()
    assert client.post("/api/query", json={"question": "x", "chart_hint": "3d"}).status_code == 422


def test_sql_flag_only_when_requested(client_for):
    client, _ = client_for()
    assert client.post("/api/query", json={"question": "Revenue by region"}).json()["evidence"]["show_sql"] is False
    d = client.post("/api/query", json={"question": "Revenue by region", "show_sql": True}).json()
    assert d["evidence"]["show_sql"] is True
    d = client.post("/api/query", json={"question": "Cho tôi xem câu SQL doanh thu theo vùng"}).json()
    assert d["evidence"]["show_sql"] is True and d["evidence"]["sql"].startswith("SELECT")


def test_ollama_model_selection_reaches_llm(client_for):
    ol = FakeOllama(response=VALID_CODE)
    client, _ = client_for(ollama=ol)
    d = client.post(
        "/api/query",
        json={"question": "Revenue by region", "chart_hint": "auto", "ollama_model": "llama3.2:1b"},
    ).json()
    assert d["status"] == "ok" and d["chart"]["source"] == "ollama"
    assert ol.calls[0]["model"] == "llama3.2:1b"


def test_sql_error_status_keeps_evidence_and_has_no_answer(client_for):
    ds = FakeLLM(name="deepseek", response="SELECT * FROM nope")
    client, _ = client_for(deepseek=ds)
    d = client.post("/api/query", json={"question": "x", "chart_hint": "auto"}).json()
    assert d["status"] == "sql_error" and "no such table" in d["evidence"]["sql_error"]
    assert d["evidence"]["sql"] == "SELECT * FROM nope" and d["evidence"]["sql_prompt"]
    assert d["table"] is None and d["answer"] is None
    assert d["chart"]["status"] == "not_requested" and d["chart"]["code"] is None


def test_llm_call_failure_is_a_sql_error_not_executed_as_sql(client_for):
    client, _ = client_for(deepseek=FakeLLM(name="deepseek", raises=RuntimeError("quota")))
    d = client.post("/api/query", json={"question": "x"}).json()
    assert d["status"] == "sql_error" and "Error calling DeepSeek: quota" in d["evidence"]["sql_error"]
    assert d["evidence"]["sql"] == "" and d["evidence"]["sql_source"] == "error"


def test_empty_result_status_has_text_answer(client_for):
    ds = FakeLLM(name="deepseek", response="SELECT * FROM orders WHERE 1 = 0")
    client, _ = client_for(deepseek=ds)
    d = client.post("/api/query", json={"question": "x", "chart_hint": "auto"}).json()
    assert d["status"] == "empty_result" and d["table"] is None
    assert "không có" in d["answer"]["text"] or "no rows" in d["answer"]["text"]
    assert d["chart"]["status"] == "not_requested"  # không có dữ liệu để vẽ


def test_chart_failure_keeps_answer_table_and_sql(client_for):
    bad = "import matplotlib.pyplot as plt\nplt.figure()\nraise ValueError('boom')"
    client, _ = client_for(ollama=FakeOllama(response=bad))
    d = client.post("/api/query", json={"question": "Plot revenue by region"}).json()
    assert d["status"] == "ok"  # truy vấn vẫn thành công
    assert d["chart"]["status"] == "error" and "boom" in d["chart"]["error"]
    assert d["chart"]["png_base64"] is None and d["chart"]["code"] == bad
    assert d["table"] is not None and d["answer"]["text"] and d["evidence"]["sql"].startswith("SELECT")


def test_chart_stage_exception_is_isolated(client_for, monkeypatch):
    client, c = client_for()

    def explode(*a, **k):
        raise RuntimeError("viz agent down")

    monkeypatch.setattr(c.viz_agent, "generate_code", explode)
    d = client.post("/api/query", json={"question": "Plot revenue by region"}).json()
    assert d["status"] == "ok" and d["chart"]["status"] == "error" and "viz agent down" in d["chart"]["error"]
    assert d["table"] is not None and d["answer"] is not None


def test_llm_answer_is_used_when_grounded(client_for):
    ds = FakeLLM(name="deepseek")
    client, c = client_for(deepseek=ds)
    n = c.repo.execute("SELECT COUNT(*) AS n FROM customers")[0]["n"][0]
    # lần gọi 1: SQL; lần gọi 2: câu trả lời (lời gọi của planner được FakeLLM xử lý riêng)
    ds.script("SELECT COUNT(*) AS n FROM customers", f"Có {n} khách hàng.")
    ds.available = True
    d = client.post("/api/query", json={"question": "Có bao nhiêu khách hàng?"}).json()
    assert d["status"] == "ok" and d["answer"]["source"] == "deepseek"
    assert d["answer"]["text"] == f"Có {n} khách hàng." and d["answer"]["fallback_reason"] is None
    assert len(ds.calls) == 2 and "ONLY the evidence" in ds.calls[1]["system"]
    assert "n\n" in ds.calls[1]["prompt"]  # kết quả (CSV) có trong prompt trả lời


def test_ungrounded_llm_answer_falls_back_to_template(client_for):
    ds = FakeLLM(name="deepseek").script(
        "SELECT COUNT(*) AS n FROM customers", "Có 9999 khách hàng, tăng 25% so với năm trước.")
    client, c = client_for(deepseek=ds)
    d = client.post("/api/query", json={"question": "Có bao nhiêu khách hàng?"}).json()
    assert d["answer"]["source"] == "template" and d["answer"]["fallback_reason"] == "ungrounded_numbers"
    assert "9999" not in d["answer"]["text"] and "25" not in d["answer"]["text"]


def test_answer_llm_error_falls_back_to_template(client_for):
    ds = FakeLLM(name="deepseek").script("SELECT COUNT(*) AS n FROM customers", RuntimeError("timeout"))
    client, _ = client_for(deepseek=ds)
    d = client.post("/api/query", json={"question": "How many customers?"}).json()
    assert d["status"] == "ok" and d["answer"]["source"] == "template"
    assert d["answer"]["fallback_reason"].startswith("llm_error")


def test_mock_sql_never_sends_results_to_llm(client_for):
    ds = FakeLLM(name="deepseek", response="SELECT 1")
    client, _ = client_for(deepseek=ds)
    d = client.post("/api/query", json={"question": "Revenue by region", "use_mock_sql": True}).json()
    assert d["answer"]["source"] == "template" and ds.calls == []


def test_answer_prompt_sends_only_limited_rows(client_for):
    ds = FakeLLM(name="deepseek").script("SELECT order_id FROM orders", "Có 200 dòng.")
    client, _ = client_for(deepseek=ds, answer_max_rows=5)
    client.post("/api/query", json={"question": "List orders"})
    answer_prompt = ds.calls[1]["prompt"]
    assert "Rows in result: 200; columns: 1; rows shown below: 5" in answer_prompt
    assert len(answer_prompt.strip().splitlines()) < 15


def test_empty_question_rejected(client_for):
    client, _ = client_for()
    assert client.post("/api/query", json={"question": ""}).status_code == 422


def test_missing_db_returns_503_and_does_not_create_file(client_for, tmp_path):
    missing = str(tmp_path / "nope.db")
    client, _ = client_for(db_path=missing)
    assert client.get("/api/health").json()["db_exists"] is False
    assert client.get("/api/schema").status_code == 503
    r = client.post("/api/query", json={"question": "x"})
    assert r.status_code == 503 and "create_demo_db" in r.json()["detail"]
    assert not (tmp_path / "nope.db").exists()
