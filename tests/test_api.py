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
    }
    assert "orders" in client.get("/api/schema").json()["tables"]
    assert client.get("/api/models").json() == {"models": ["llama3.2", "llama3.2:1b"], "default": "llama3.2"}


def test_query_full_pipeline_in_mock_mode(client_for):
    client, _ = client_for()
    r = client.post("/api/query", json={"question": "Show monthly revenue in 2024"})
    assert r.status_code == 200
    d = r.json()
    assert d["status"] == "ok"
    assert d["sql_source"] == "mock" and d["viz_source"] == "mock"
    assert d["table"]["columns"] == ["month", "monthly_revenue"]
    assert len(d["table"]["rows"]) == 12
    assert base64.b64decode(d["chart_png_base64"]).startswith(b"\x89PNG")
    assert "plt.figure" in d["viz_code"]


def test_chart_hint_only_affects_stage3(client_for):
    client, _ = client_for()
    d = client.post("/api/query", json={"question": "Revenue by region", "chart_hint": "pie chart"}).json()
    assert d["full_question"] == "Revenue by region (as a pie chart)"
    assert "Revenue by region (as a pie chart)" not in d["sql_prompt"]
    assert "(as a pie chart)" in d["viz_prompt"]
    assert "ax.pie" in d["viz_code"]


def test_ollama_model_selection_reaches_llm(client_for):
    ol = FakeOllama(response=VALID_CODE)
    client, _ = client_for(ollama=ol)
    d = client.post(
        "/api/query",
        json={"question": "Revenue by region", "ollama_model": "llama3.2:1b"},
    ).json()
    assert d["status"] == "ok" and d["viz_source"] == "ollama"
    assert ol.calls[0]["model"] == "llama3.2:1b"


def test_sql_error_status_keeps_stage1_output(client_for):
    ds = FakeLLM(name="deepseek", response="SELECT * FROM nope")
    client, _ = client_for(deepseek=ds)
    d = client.post("/api/query", json={"question": "x"}).json()
    assert d["status"] == "sql_error" and "no such table" in d["sql_error"]
    assert d["sql"] == "SELECT * FROM nope" and d["sql_prompt"]
    assert d["table"] is None and d["viz_code"] is None


def test_empty_result_status(client_for):
    ds = FakeLLM(name="deepseek", response="SELECT * FROM orders WHERE 1 = 0")
    client, _ = client_for(deepseek=ds)
    d = client.post("/api/query", json={"question": "x"}).json()
    assert d["status"] == "empty_result" and d["table"] is None and d["viz_code"] is None


def test_viz_error_status(client_for):
    bad = "import matplotlib.pyplot as plt\nplt.figure()\nraise ValueError('boom')"
    client, _ = client_for(ollama=FakeOllama(response=bad))
    d = client.post("/api/query", json={"question": "Revenue by region"}).json()
    assert d["status"] == "viz_error" and "boom" in d["viz_error"]
    assert d["chart_png_base64"] is None
    assert d["table"] is not None and d["viz_code"] == bad


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
