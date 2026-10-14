"""Giai đoạn 4: query + EDA trong cùng một lượt qua orchestrator/API; metadata import."""
import io

import pytest
from fastapi.testclient import TestClient

from backend.container import get_container
from backend.main import create_app
from tests.conftest import FakeLLM

CSV = ("ten,diem,lop\n" + "\n".join(f"{n},{d},{l}" for n, d, l in [
    ("An", 8, "A"), ("Binh", 6, "A"), ("Chi", "", "B"), ("Dung", 9, "B"), ("An", 8, "A")])).encode()
TWO_TABLES = None


@pytest.fixture
def env(make_container):
    def _make(**kw):
        c = make_container(**kw)
        app = create_app()
        app.dependency_overrides[get_container] = lambda: c
        return TestClient(app), c
    return _make


def _upload(client, name="diem.csv", content=CSV):
    r = client.post("/api/datasets", files={"file": (name, content)})
    assert r.status_code == 201, r.text
    return r.json()


def _q(client, question, ds=None, **kw):
    body = {"question": question, "dataset_id": ds, "use_mock_sql": True, "use_mock_viz": True, **kw}
    r = client.post("/api/query", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def test_eda_only_turn_has_text_table_plan_and_evidence(env):
    client, _ = env()
    ds = _upload(client)["dataset_id"]
    d = _q(client, "Kiểm tra giá trị thiếu và trùng lặp của bảng diem", ds)
    assert d["status"] == "ok" and d["table"] is None and d["chart"]["status"] == "not_requested"
    assert [s["operation"] for s in d["plan"]["steps"]] == ["data_quality"] and d["plan"]["source"] == "rules"
    e = d["eda"][0]
    assert e["operation"] == "data_quality" and e["table"] == "diem" and e["status"] == "ok"
    assert e["summary"]["duplicate_rows"] == 1 and e["summary"]["columns_with_nulls"] == ["diem"]
    assert e["table_data"]["columns"][0] == "column" and e["queries"] and e["definitions"] and e["exact"]
    assert "1 dòng trùng lặp" in d["answer"]["text"] and d["answer"]["source"] == "template"
    assert d["evidence"]["sql"] == "" and d["evidence"]["sql_source"] == "none"


def test_answer_text_numbers_come_from_payload(env):
    client, _ = env()
    ds = _upload(client)["dataset_id"]
    d = _q(client, "thống kê mô tả cột diem", ds)
    e = d["eda"][0]
    stats = e["summary"]["stats"][0]
    assert stats["count"] == 4 and stats["mean"] == 7.75 and stats["missing"] == 1
    assert "mean 7.75" in d["answer"]["text"]


def test_english_question_gets_english_text(env):
    client, _ = env()
    ds = _upload(client)["dataset_id"]
    d = _q(client, "Give me an overview of the diem table", ds)
    assert d["answer"]["text"].startswith("Table diem has 5 rows")


def test_combined_query_and_eda_in_one_turn(env):
    client, _ = env()
    ds = _upload(client)["dataset_id"]
    d = _q(client, "Cho xem 10 dòng đầu theo lớp và kiểm tra giá trị thiếu của diem", ds)
    assert [s["operation"] for s in d["plan"]["steps"]] == ["query", "data_quality"]
    assert d["status"] == "ok" and len(d["table"]["rows"]) == 5  # kết quả truy vấn vẫn có
    assert d["eda"][0]["operation"] == "data_quality"
    assert "giá trị thiếu" in d["answer"]["text"] and d["evidence"]["sql"]


def test_simple_query_does_not_run_eda(env):
    client, _ = env()
    ds = _upload(client)["dataset_id"]
    d = _q(client, "Cho xem 10 dòng đầu", ds)
    assert d["eda"] == [] and [s["operation"] for s in d["plan"]["steps"]] == ["query"]


def test_ambiguous_target_asks_user_without_running_anything(env, make_container):
    client, c = env()
    two = io.BytesIO()
    import sqlite3, tempfile, pathlib
    p = pathlib.Path(tempfile.mkdtemp()) / "x.db"
    conn = sqlite3.connect(p)
    conn.executescript("CREATE TABLE a (x INT); CREATE TABLE b (y INT); INSERT INTO a VALUES (1); INSERT INTO b VALUES (2);")
    conn.commit(); conn.close()
    ds = _upload(client, "x.db", p.read_bytes())["dataset_id"]
    d = _q(client, "thống kê mô tả", ds)
    assert d["status"] == "needs_clarification" and "a, b" in d["clarification"]
    assert d["eda"] == [] and d["table"] is None
    # tổng quan không cần nêu bảng: áp dụng cho cả dataset
    d = _q(client, "tổng quan dữ liệu", ds)
    assert sorted(e["table"] for e in d["eda"]) == ["a", "b"]


def test_chart_request_with_eda_only_reports_chart_error_but_keeps_results(env):
    client, _ = env()
    ds = _upload(client)["dataset_id"]
    d = _q(client, "tổng quan bảng diem", ds, chart_hint="auto")
    assert d["status"] == "ok" and d["eda"] and d["chart"]["status"] == "error" and d["chart"]["png_base64"] is None


def test_query_failure_keeps_eda_results(env):
    client, c = env(deepseek=FakeLLM(name="deepseek", response="SELECT cot_khong_co FROM diem"))
    ds = _upload(client)["dataset_id"]
    d = client.post("/api/query", json={"question": "Cho xem theo lớp và kiểm tra giá trị thiếu của diem",
                                        "dataset_id": ds, "use_mock_viz": True}).json()
    # LLM trả SQL (không phải JSON) cho planner -> câu hỏi làm rõ; không tự đoán
    assert d["status"] == "needs_clarification" and d["eda"] == []
    d = _q(client, "Cho xem theo lớp và kiểm tra giá trị thiếu của diem", ds)  # mock: quy tắc
    assert d["status"] == "ok" and d["eda"]


def test_show_sql_flag_and_unknown_dataset(env):
    client, _ = env()
    ds = _upload(client)["dataset_id"]
    d = _q(client, "tổng quan bảng diem, hiển thị SQL", ds)
    assert d["plan"]["show_sql"] is True and d["eda"][0]["queries"]
    r = client.post("/api/query", json={"question": "tổng quan", "dataset_id": "0" * 32, "use_mock_sql": True})
    assert r.status_code == 404  # không fallback sang demo


def test_demo_dataset_supports_eda(env):
    client, _ = env()
    d = _q(client, "tương quan giữa các cột số của orders")
    assert d["eda"][0]["operation"] == "correlation" and d["eda"][0]["summary"]["top_pairs"]


# ── metadata import ────────────────────────────────────────────────────────
def test_import_records_source_and_inferred_types_and_notes(env):
    client, _ = env()
    csv = "id,code,score,when,mix,dmy\n1,007,5,2024-01-02,1,01/02/2024\n2,008,N/A,2024-01-03,x,03/02/2024\n3,,7,2024-01-04,2,04/02/2024\n"
    info = _upload(client, "t.csv", csv.encode())
    cols = {c["name"]: c for c in info["tables"][0]["columns"]}
    assert cols["id"]["inferred_type"] == "integer" and cols["id"]["source_type"] == "text"
    assert cols["score"]["type"] == "INTEGER" and any("NA" in n for n in cols["score"]["notes"])
    assert cols["code"]["inferred_type"] == "text" and any("0 đứng đầu" in n for n in cols["code"]["notes"])
    assert cols["when"]["inferred_type"] == "date"
    assert cols["mix"]["inferred_type"] == "text" and any("hỗn hợp" in n for n in cols["mix"]["notes"])
    assert any("mơ hồ" in n for n in cols["dmy"]["notes"])
    assert len(info["warnings"]) == 1 and all(c in info["warnings"][0] for c in ("score", "mix", "code"))  # gộp 1 dòng/bảng


def test_excel_import_metadata(env, tmp_path):
    from openpyxl import Workbook
    import datetime as dt

    wb = Workbook(); ws = wb.active; ws.title = "S"
    ws.append(["n", "mixed", "d", "flag"])
    ws.append([1, 1, dt.date(2024, 1, 1), True])
    ws.append([2, "abc", dt.date(2024, 1, 2), False])
    ws.append(["N/A", 3.5, None, None])
    ws.append([4, "x", None, True])
    buf = io.BytesIO(); wb.save(buf)
    client, _ = env()
    info = _upload(client, "b.xlsx", buf.getvalue())
    cols = {c["name"]: c for c in info["tables"][0]["columns"]}
    assert cols["n"]["inferred_type"] == "integer" and any("NA" in n for n in cols["n"]["notes"])
    assert cols["mixed"]["inferred_type"] == "mixed" and cols["mixed"]["type"] == "TEXT"
    assert cols["d"]["inferred_type"] == "date" and cols["flag"]["inferred_type"] == "boolean"
    assert len(info["warnings"]) == 1 and "mixed" in info["warnings"][0]


def test_sqlite_import_keeps_declared_type_as_source(env, tmp_path):
    import sqlite3
    p = tmp_path / "s.db"
    conn = sqlite3.connect(p)
    conn.executescript("CREATE TABLE t (a INTEGER, b VARCHAR(20), c DOUBLE, d); INSERT INTO t VALUES (1,'x',1.5,NULL);")
    conn.commit(); conn.close()
    info = _upload(client := env()[0], "s.db", p.read_bytes())
    cols = {c["name"]: c for c in info["tables"][0]["columns"]}
    assert (cols["a"]["source_type"], cols["a"]["inferred_type"]) == ("INTEGER", "integer")
    assert (cols["b"]["source_type"], cols["b"]["inferred_type"]) == ("VARCHAR(20)", "text")
    assert cols["c"]["inferred_type"] == "real" and cols["d"]["inferred_type"] == "blob"


def test_overview_is_a_normal_question_with_few_warnings(env):
    client, _ = env()
    csv = "id,code,score,mix,dmy\n1,007,5,1,01/02/2024\n2,008,N/A,x,03/02/2024\n3,,7,2,04/02/2024\n"
    ds = _upload(client, "t.csv", csv.encode())["dataset_id"]
    d = _q(client, "Tổng quan dataset", ds)
    e = d["eda"][0]
    assert e["operation"] == "table_overview" and len(e["warnings"]) <= 1
    notes = {r[0]: r[5] for r in e["table_data"]["rows"]}  # cột import_notes
    assert e["table_data"]["columns"][5] == "import_notes" and notes["score"] and notes["code"]
