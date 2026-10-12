"""Giai đoạn 2: hỏi đáp trên dataset được chọn (dataset_id, prompt theo schema, chỉ đọc, giới hạn)."""
import hashlib
import io
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.container import get_container
from backend.main import create_app
from tests.conftest import FakeLLM, FakeOllama

CSV_A = "ten,diem\nAn,8\nBinh,6\n".encode()
SQLITE_SCRIPT = """
CREATE TABLE khoa (id INTEGER PRIMARY KEY, ten TEXT);
CREATE TABLE sv (id INTEGER PRIMARY KEY, ten TEXT, khoa_id INTEGER REFERENCES khoa(id));
CREATE TABLE doc_lap (x INTEGER, y INTEGER);
CREATE TABLE ghi_chu (sv_ref INTEGER, noi_dung TEXT);
INSERT INTO khoa VALUES (1,'CNTT'),(2,'Toán');
INSERT INTO sv VALUES (1,'An',1),(2,'Binh',2);
INSERT INTO doc_lap VALUES (1,2),(3,4);
INSERT INTO ghi_chu VALUES (1,'tốt');
"""


def _sqlite_bytes(tmp_path) -> bytes:
    path = tmp_path / "src.db"
    conn = sqlite3.connect(path)
    conn.executescript(SQLITE_SCRIPT)
    conn.commit()
    conn.close()
    return path.read_bytes()


@pytest.fixture
def env(make_container, tmp_path):
    def _make(**kw):
        container = make_container(**kw)
        app = create_app()
        app.dependency_overrides[get_container] = lambda: container
        return TestClient(app), container

    return _make


def _upload(client, name, content):
    r = client.post("/api/datasets", files={"file": (name, content)})
    assert r.status_code == 201, r.text
    return r.json()["dataset_id"]


def test_query_runs_on_selected_dataset_not_demo(env):
    client, _ = env()
    ds = _upload(client, "diem.csv", CSV_A)
    d = client.post("/api/query", json={"question": "xem", "dataset_id": ds, "use_mock_sql": True, "use_mock_viz": True}).json()
    assert d["status"] == "ok" and d["sql"] == 'SELECT * FROM "diem" LIMIT 10'
    assert d["table"]["columns"] == ["ten", "diem"] and len(d["table"]["rows"]) == 2


def test_unknown_dataset_is_404_and_never_falls_back_to_demo(env):
    client, _ = env()
    for path, call in (
        ("query", lambda: client.post("/api/query", json={"question": "x", "dataset_id": "0" * 32})),
        ("schema", lambda: client.get("/api/schema", params={"dataset_id": "0" * 32})),
        ("bad", lambda: client.post("/api/query", json={"question": "x", "dataset_id": "../../etc"})),
    ):
        r = call()
        assert r.status_code == 404, path
        assert r.json()["detail"]["code"] == "dataset_not_found"


def test_schema_endpoint_default_and_dataset(env, tmp_path):
    client, _ = env()
    assert "orders" in client.get("/api/schema").json()["tables"]
    ds = _upload(client, "u.db", _sqlite_bytes(tmp_path))
    d = client.get("/api/schema", params={"dataset_id": ds}).json()
    assert set(d["tables"]) == {"khoa", "sv", "doc_lap", "ghi_chu"}
    assert d["relations"] == [{"from_table": "sv", "from_column": "khoa_id", "to_table": "khoa", "to_column": "id"}]


def test_prompt_uses_dataset_schema_without_sales_few_shot(env):
    ds_llm = FakeLLM(name="deepseek", response="SELECT AVG(diem) FROM diem")
    client, _ = env(deepseek=ds_llm)
    ds = _upload(client, "diem.csv", CSV_A)
    d = client.post("/api/query", json={"question": "điểm trung bình", "dataset_id": ds, "use_mock_viz": True}).json()
    assert d["status"] == "ok" and d["sql_source"] == "deepseek"
    assert "CREATE TABLE diem" in d["sql_prompt"] and "diem" in d["sql_prompt"]
    assert "orders" not in d["sql_prompt"] and "revenue" not in d["sql_prompt"]
    assert "FOREIGN KEY" in ds_llm.calls[0]["system"]  # luật JOIN nằm trong system prompt


def test_demo_prompt_is_unchanged(env):
    client, _ = env(deepseek=FakeLLM(name="deepseek", response="SELECT 1"))
    d = client.post("/api/query", json={"question": "x"}).json()
    assert "Some example questions" in d["sql_prompt"] and "CREATE TABLE orders" in d["sql_prompt"]


def test_declared_foreign_keys_and_confirmed_relations_reach_prompt(env, tmp_path):
    client, _ = env(deepseek=FakeLLM(name="deepseek", response="SELECT 1"))
    ds = _upload(client, "u.db", _sqlite_bytes(tmp_path))
    prompt = lambda: client.post("/api/query", json={"question": "q", "dataset_id": ds}).json()["sql_prompt"]
    p = prompt()
    assert "FOREIGN KEY (khoa_id) REFERENCES khoa(id)" in p
    assert "REFERENCES sv" not in p  # ghi_chu.sv_ref chưa được xác nhận -> không có quan hệ

    rel = {"from_table": "ghi_chu", "from_column": "sv_ref", "to_table": "sv", "to_column": "id"}
    r = client.post(f"/api/datasets/{ds}/relations", json=rel)
    assert r.status_code == 200 and r.json()["confirmed_relations"] == [rel]
    assert "FOREIGN KEY (sv_ref) REFERENCES sv(id)" in prompt()
    assert client.post(f"/api/datasets/{ds}/relations", json=rel).json()["confirmed_relations"] == [rel]  # idempotent
    assert client.get(f"/api/datasets/{ds}").json()["confirmed_relations"] == [rel]  # được lưu

    assert client.request("DELETE", f"/api/datasets/{ds}/relations", json=rel).json()["confirmed_relations"] == []
    assert "REFERENCES sv" not in prompt()
    assert client.request("DELETE", f"/api/datasets/{ds}/relations", json=rel).status_code == 404


@pytest.mark.parametrize(
    "rel",
    [
        {"from_table": "nope", "from_column": "a", "to_table": "sv", "to_column": "id"},
        {"from_table": "ghi_chu", "from_column": "nope", "to_table": "sv", "to_column": "id"},
        {"from_table": "ghi_chu", "from_column": "sv_ref", "to_table": "sv", "to_column": "nope"},
    ],
)
def test_invalid_relation_rejected(env, tmp_path, rel):
    client, _ = env()
    ds = _upload(client, "u.db", _sqlite_bytes(tmp_path))
    r = client.post(f"/api/datasets/{ds}/relations", json=rel)
    assert r.status_code == 422 and r.json()["detail"]["code"] == "invalid_relation"


def test_clarification_instead_of_guessing_a_join(env, tmp_path):
    llm = FakeLLM(name="deepseek", response="-- CLARIFY: Cột sv_ref của ghi_chu liên kết với bảng nào?")
    client, _ = env(deepseek=llm)
    ds = _upload(client, "u.db", _sqlite_bytes(tmp_path))
    d = client.post("/api/query", json={"question": "ghi chú của từng sinh viên", "dataset_id": ds}).json()
    assert d["status"] == "needs_clarification"
    assert d["clarification"].startswith("Cột sv_ref")
    assert d["table"] is None and d["viz_code"] is None


def test_unrelated_tables_can_be_queried_separately(env, tmp_path):
    client, _ = env(deepseek=FakeLLM(name="deepseek", response="SELECT SUM(x) AS s FROM doc_lap"))
    ds = _upload(client, "u.db", _sqlite_bytes(tmp_path))
    d = client.post("/api/query", json={"question": "tổng x", "dataset_id": ds, "use_mock_viz": True}).json()
    assert d["status"] == "ok" and d["table"]["rows"] == [[4]]


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM orders",
        "UPDATE orders SET revenue = 0",
        "DROP TABLE orders",
        "INSERT INTO orders (order_id) VALUES (999999)",
        "CREATE TABLE evil (a)",
        "PRAGMA writable_schema = 1",
        "ATTACH DATABASE '/tmp/x.db' AS x",
        "SELECT 1; DROP TABLE orders",
        "WITH t AS (SELECT 1) DELETE FROM orders",
        "SELECT load_extension('x')",
    ],
)
def test_only_read_queries_allowed_and_demo_untouched(env, demo_db, sql):
    before = hashlib.sha256(Path(demo_db).read_bytes()).hexdigest()
    client, c = env(deepseek=FakeLLM(name="deepseek", response=sql))
    d = client.post("/api/query", json={"question": "x"}).json()
    assert d["status"] == "sql_error" and d["sql_error"]
    assert hashlib.sha256(Path(demo_db).read_bytes()).hexdigest() == before
    n, err = c.repo.execute("SELECT COUNT(*) AS n FROM orders")
    assert err is None and int(n["n"][0]) == 200


def test_write_attempt_on_uploaded_dataset_is_rejected(env):
    client, c = env(deepseek=FakeLLM(name="deepseek", response="DELETE FROM diem"))
    ds = _upload(client, "diem.csv", CSV_A)
    assert client.post("/api/query", json={"question": "x", "dataset_id": ds}).json()["status"] == "sql_error"
    df, _ = c.datasets.repository(ds).execute("SELECT COUNT(*) AS n FROM diem")
    assert int(df["n"][0]) == 2


def test_row_limit_truncates_and_flags(env):
    client, _ = env(deepseek=FakeLLM(name="deepseek", response="SELECT * FROM orders"))
    client2, c = env(deepseek=FakeLLM(name="deepseek", response="SELECT * FROM orders"), query_max_rows=50)
    d = client2.post("/api/query", json={"question": "x", "use_mock_viz": True}).json()
    assert d["truncated"] is True and len(d["table"]["rows"]) == 50
    d = client.post("/api/query", json={"question": "x", "use_mock_viz": True}).json()
    assert d["truncated"] is False and len(d["table"]["rows"]) == 200


def test_query_timeout(env):
    client, c = env(query_timeout_seconds=0.2)
    slow = "WITH RECURSIVE r(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM r) SELECT COUNT(*) FROM r"
    df, err = c.repo.execute(slow)
    assert df is None and "thời gian" in err


def test_empty_result_on_dataset(env):
    client, _ = env(deepseek=FakeLLM(name="deepseek", response="SELECT * FROM diem WHERE diem > 100"))
    ds = _upload(client, "diem.csv", CSV_A)
    d = client.post("/api/query", json={"question": "x", "dataset_id": ds}).json()
    assert d["status"] == "empty_result"


def test_sql_error_on_dataset(env):
    client, _ = env(deepseek=FakeLLM(name="deepseek", response="SELECT cot_khong_co FROM diem"))
    ds = _upload(client, "diem.csv", CSV_A)
    d = client.post("/api/query", json={"question": "x", "dataset_id": ds}).json()
    assert d["status"] == "sql_error" and "no such column" in d["sql_error"]


def test_datasets_are_isolated_in_queries(env):
    client, _ = env(deepseek=FakeLLM(name="deepseek", response="SELECT * FROM a"))
    d1 = _upload(client, "a.csv", b"v\n1\n")
    d2 = _upload(client, "b.csv", b"w\n2\n")
    assert client.post("/api/query", json={"question": "x", "dataset_id": d1, "use_mock_viz": True}).json()["status"] == "ok"
    r = client.post("/api/query", json={"question": "x", "dataset_id": d2, "use_mock_viz": True}).json()
    assert r["status"] == "sql_error" and "no such table" in r["sql_error"]


def test_special_table_names_are_quoted_in_schema(env):
    client, c = env()
    ds = _upload(client, "Doanh thu 2026.csv", "Tên SP,order\nÁo,1\n".encode())
    schema = c.datasets.repository(ds).get_schema()
    assert 'CREATE TABLE Doanh_thu_2026' in schema and "Tên_SP" in schema
