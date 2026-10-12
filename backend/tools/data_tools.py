"""
backend/tools/data_tools.py
---------------------------
Tools thao tác dữ liệu, đều nhận `dataset_id` tùy chọn (None = database demo mặc định):
  * get_schema / list_tables / execute_sql  — đọc schema, chạy SQL chỉ-đọc;
  * import_dataset / get_dataset            — nhập và tra cứu dataset upload;
  * link_tables / unlink_tables             — khai báo quan hệ giữa các bảng.
Mọi tool tra repository qua `DatasetStore`, client không bao giờ đưa đường dẫn vào.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import pandas as pd

from backend.database import DatasetStore
from backend.tools.base import Tool


class GetSchemaTool(Tool):
    name = "get_schema"
    description = "Đọc schema của dataset, trả về chuỗi CREATE TABLE (Code Representation) kèm khóa ngoại."

    def __init__(self, store: DatasetStore):
        self._store = store

    def run(self, dataset_id: Optional[str] = None) -> str:
        return self._store.repository(dataset_id).get_schema()


class ListTablesTool(Tool):
    name = "list_tables"
    description = "Trả về {tên_bảng: [cột...]} của dataset."

    def __init__(self, store: DatasetStore):
        self._store = store

    def run(self, dataset_id: Optional[str] = None) -> Dict[str, List[str]]:
        return self._store.repository(dataset_id).get_schema_dict()


class ExecuteSQLTool(Tool):
    name = "execute_sql"
    description = "Thực thi SQL chỉ-đọc trên dataset (giới hạn thời gian/số dòng), trả về (DataFrame, error)."

    def __init__(self, store: DatasetStore):
        self._store = store

    def run(self, sql: str, dataset_id: Optional[str] = None) -> Tuple[Optional[pd.DataFrame], Optional[str]]:
        return self._store.repository(dataset_id).execute(sql)


class ImportDatasetTool(Tool):
    name = "import_dataset"
    description = (
        "Nhận nội dung một tệp upload (CSV, Excel .xlsx, SQLite .db/.sqlite), kiểm tra và chuẩn hóa "
        "thành một SQLite database riêng. Trả về dataset_id, các bảng, số dòng và cảnh báo."
    )

    def __init__(self, store: DatasetStore):
        self._store = store

    def run(self, *, filename: str, content: bytes) -> dict:
        return self._store.create(filename, content).to_dict()


class GetDatasetTool(Tool):
    name = "get_dataset"
    description = "Trả về metadata (bảng, cột, số dòng, khóa ngoại, quan hệ đã xác nhận, cảnh báo) theo dataset_id."

    def __init__(self, store: DatasetStore):
        self._store = store

    def run(self, *, dataset_id: str) -> dict:
        return self._store.get(dataset_id).to_dict()


class LinkTablesTool(Tool):
    name = "link_tables"
    description = "Khai báo quan hệ (khóa ngoại) do người dùng xác nhận giữa hai bảng trong cùng dataset."

    def __init__(self, store: DatasetStore):
        self._store = store

    def run(self, *, dataset_id: str, from_table: str, from_column: str, to_table: str, to_column: str) -> dict:
        return self._store.add_relation(dataset_id, from_table, from_column, to_table, to_column).to_dict()


class UnlinkTablesTool(Tool):
    name = "unlink_tables"
    description = "Xóa một quan hệ đã khai báo thủ công giữa hai bảng."

    def __init__(self, store: DatasetStore):
        self._store = store

    def run(self, *, dataset_id: str, from_table: str, from_column: str, to_table: str, to_column: str) -> dict:
        return self._store.remove_relation(dataset_id, from_table, from_column, to_table, to_column).to_dict()
