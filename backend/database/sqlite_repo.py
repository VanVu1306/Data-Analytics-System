"""
backend/database/sqlite_repo.py
-------------------------------
Truy cập SQLite: đọc schema, lấy dòng mẫu, thực thi SQL.
Đây là lớp DUY NHẤT nói chuyện trực tiếp với database.
Logic giữ nguyên như bản gốc modules/dail_sql.py.
"""
from __future__ import annotations

import os
import sqlite3
from contextlib import closing
from typing import Dict, List, Optional, Tuple

import pandas as pd


class SQLiteRepository:
    def __init__(self, db_path: str):
        self.db_path = db_path

    def exists(self) -> bool:
        return os.path.exists(self.db_path)

    # ── Schema ─────────────────────────────────────────────────────────────
    def get_schema(self) -> str:
        """
        Schema dạng CRP (Code Representation Prompt):
            CREATE TABLE table_name (
              col1 type,  col2 type,
              PRIMARY KEY (col),
              FOREIGN KEY (col) REFERENCES other_table(col)
            )
        """
        with closing(sqlite3.connect(self.db_path)) as conn:
            cur = conn.cursor()
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
            tables = [r[0] for r in cur.fetchall()]

            schema_parts = []
            for table in tables:
                if table.startswith("sqlite_"):
                    continue

                cur.execute(f"PRAGMA table_info({table})")
                cols = cur.fetchall()  # (cid, name, type, notnull, dflt, pk)

                cur.execute(f"PRAGMA foreign_key_list({table})")
                fks = cur.fetchall()  # (id, seq, table, from, to, ...)

                col_defs = ", ".join([f"{c[1]} {c[2]}" for c in cols])
                pk_cols = [c[1] for c in cols if c[5]]
                fk_defs = [f"FOREIGN KEY ({f[3]}) REFERENCES {f[2]}({f[4]})" for f in fks]

                all_defs = [col_defs]
                if pk_cols:
                    all_defs.append(f"PRIMARY KEY ({', '.join(pk_cols)})")
                all_defs.extend(fk_defs)

                schema_parts.append(f"CREATE TABLE {table} (\n  {',  '.join(all_defs)}\n)")

        return "\n\n".join(schema_parts)

    def get_schema_dict(self) -> Dict[str, List[str]]:
        """{table_name: [col_names]} để hiển thị trong UI sidebar."""
        with closing(sqlite3.connect(self.db_path)) as conn:
            cur = conn.cursor()
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
            tables = [r[0] for r in cur.fetchall() if not r[0].startswith("sqlite_")]
            result = {}
            for t in tables:
                cur.execute(f"PRAGMA table_info({t})")
                result[t] = [c[1] for c in cur.fetchall()]
        return result

    def get_sample_rows(self, table: str, n: int = 3) -> str:
        """Lấy n dòng mẫu của một bảng (chưa được pipeline dùng, giữ từ bản gốc)."""
        try:
            with closing(sqlite3.connect(self.db_path)) as conn:
                df = pd.read_sql_query(f"SELECT * FROM {table} LIMIT {n}", conn)
            return df.to_string(index=False)
        except Exception:
            return ""

    # ── Execute ────────────────────────────────────────────────────────────
    def execute(self, sql: str) -> Tuple[Optional[pd.DataFrame], Optional[str]]:
        """Chạy SQL. Thành công: (df, None). Lỗi: (None, error_str)."""
        try:
            with closing(sqlite3.connect(self.db_path)) as conn:
                df = pd.read_sql_query(sql, conn)
            return df, None
        except Exception as e:
            return None, str(e)
