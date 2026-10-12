"""Test tool -> service -> API upload dataset (qua HTTP, không cần LLM)."""

import io
import sqlite3

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook

from backend.container import get_container
from backend.database import ImportLimits
from backend.main import create_app


@pytest.fixture
def api(make_container):
    """(store, service, client) trên container thật với LLM giả; dataset lưu trong thư mục tạm."""

    def _make(tmp_path=None, max_upload=1024 * 1024):
        container = make_container()
        container.datasets.limits = ImportLimits(max_upload_bytes=max_upload)
        container.dataset_service.max_upload_bytes = max_upload
        app = create_app()
        app.dependency_overrides[get_container] = lambda: container
        return container.datasets, container.dataset_service, TestClient(app)

    return _make


def _xlsx() -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Bán hàng"
    ws.append(["sp", "sl"])
    ws.append(["áo", 3])
    ws.append(["quần", 5])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_tools_and_service(api, tmp_path):
    store, service, _ = api()
    info = service.upload("d.csv", b"a,b\n1,x\n")
    assert info["tables"][0]["row_count"] == 1
    assert service.get(info["dataset_id"]) == info


def test_upload_xlsx_then_read_back_and_query(api, tmp_path):
    store, _, client = api(tmp_path)
    resp = client.post("/api/datasets", files={"file": ("ban hang.xlsx", _xlsx(), "application/octet-stream")})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "ready" and body["format"] == "excel"
    assert body["tables"][0]["name"] == "Bán_hàng" and body["tables"][0]["row_count"] == 2
    assert [c["name"] for c in body["tables"][0]["columns"]] == ["sp", "sl"]

    assert client.get(f"/api/datasets/{body['dataset_id']}").json() == body

    # DB do server tạo truy vấn được; đường dẫn không lộ ra response
    assert "path" not in resp.text and "datasets" not in resp.text
    conn = sqlite3.connect(store.db_path(body["dataset_id"]))
    try:
        assert conn.execute('SELECT SUM(sl) FROM "Bán_hàng"').fetchone() == (8,)
    finally:
        conn.close()


def test_two_uploads_get_separate_datasets(api, tmp_path):
    _, _, client = api(tmp_path)
    ids = {
        client.post("/api/datasets", files={"file": ("a.csv", b"x\n1\n")}).json()["dataset_id"] for _ in range(2)
    }
    assert len(ids) == 2


@pytest.mark.parametrize(
    "filename, content, status, code",
    [
        ("dump.sql", b"DROP TABLE x;", 415, "unsupported_format"),
        ("data.json", b"[]", 415, "unsupported_format"),
        ("old.xls", b"x", 415, "unsupported_format"),
        ("broken.xlsx", b"not excel", 422, "invalid_file"),
        ("empty.csv", b"", 422, "invalid_file"),
        ("fake.db", b"not sqlite", 422, "invalid_file"),
    ],
)
def test_upload_errors_are_clear(api, tmp_path, filename, content, status, code):
    store, _, client = api(tmp_path)
    resp = client.post("/api/datasets", files={"file": (filename, content)})
    assert resp.status_code == status
    detail = resp.json()["detail"]
    assert detail["code"] == code and detail["message"]
    assert list(store.root.iterdir()) == []  # không để lại tệp tạm/dataset hỏng


def test_oversized_upload_rejected_with_413(api, tmp_path):
    store, _, client = api(tmp_path, max_upload=100)
    resp = client.post("/api/datasets", files={"file": ("big.csv", b"a\n" + b"1\n" * 500)})
    assert resp.status_code == 413 and resp.json()["detail"]["code"] == "file_too_large"
    assert list(store.root.iterdir()) == []


def test_missing_file_field_is_422(api, tmp_path):
    _, _, client = api(tmp_path)
    assert client.post("/api/datasets").status_code == 422


@pytest.mark.parametrize("dataset_id", ["0" * 32, "..%2F..%2Fetc", "not-an-id", "A" * 32])
def test_unknown_or_malicious_dataset_id_is_404(api, tmp_path, dataset_id):
    _, _, client = api(tmp_path)
    resp = client.get(f"/api/datasets/{dataset_id}")
    assert resp.status_code == 404
    detail = resp.json()["detail"]
    if isinstance(detail, dict):  # id đi tới handler; id chứa "/" bị routing từ chối trước đó
        assert detail["code"] == "dataset_not_found"
