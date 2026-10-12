"""
backend/tools/sql_tools.py
--------------------------
Tools bọc SQLiteRepository để agents / pipeline gọi theo tên.
"""
from __future__ import annotations

from typing import Optional, Tuple

import pandas as pd

from backend.database import SQLiteRepository
from backend.tools.base import Tool


class GetSchemaTool(Tool):
    name = "get_schema"
    description = "Đọc schema database, trả về chuỗi CREATE TABLE (Code Representation)."

    def __init__(self, repo: SQLiteRepository):
        self._repo = repo

    def run(self) -> str:
        return self._repo.get_schema()


class ExecuteSQLTool(Tool):
    name = "execute_sql"
    description = "Thực thi SQL trên database, trả về (DataFrame, error)."

    def __init__(self, repo: SQLiteRepository):
        self._repo = repo

    def run(self, sql: str) -> Tuple[Optional[pd.DataFrame], Optional[str]]:
        return self._repo.execute(sql)
