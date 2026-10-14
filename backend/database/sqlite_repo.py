"""
backend/database/sqlite_repo.py
-------------------------------
Truy cập SQLite: đọc schema, lấy dòng mẫu, thực thi SQL.
Đây là lớp DUY NHẤT nói chuyện trực tiếp với database lúc truy vấn.

Truy vấn luôn chạy ở chế độ CHỈ ĐỌC (xem TODO.md, giai đoạn 2):
  * kết nối mở bằng `mode=ro` + authorizer chỉ cho SELECT/đọc dữ liệu
    (INSERT/UPDATE/DELETE/DDL/PRAGMA/ATTACH đều bị từ chối);
  * chỉ một câu lệnh mỗi lần (sqlite3 từ chối nhiều statement);
  * giới hạn thời gian thực thi, số dòng và kích thước kết quả.
Kết quả bị cắt được đánh dấu bằng `df.attrs["truncated"] = True`.
"""
from __future__ import annotations

import os
import re
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import pandas as pd

# (from_table, from_column, to_table, to_column) — quan hệ người dùng đã xác nhận
Relation = Tuple[str, str, str, str]

DEFAULT_MAX_ROWS = 1000
DEFAULT_TIMEOUT_SECONDS = 10.0
DEFAULT_MAX_RESULT_BYTES = 2 * 1024 * 1024


# ── Định danh ──────────────────────────────────────────────────────────────
def quote_ident(name: str) -> str:
    """Trích dẫn định danh SQLite (luôn dùng khi ghép vào câu SQL)."""
    return '"' + name.replace('"', '""') + '"'


_SIMPLE_IDENT = re.compile(r"[^\W\d]\w*")
_KEYWORDS = frozenset(
    "abort action add after all alter analyze and as asc attach autoincrement before begin between by cascade "
    "case cast check collate column commit conflict constraint create cross current database default deferrable "
    "deferred delete desc detach distinct do drop each else end escape except exclusive exists explain fail "
    "filter first following for foreign from full generated glob group groups having if ignore immediate in "
    "index indexed initially inner insert instead intersect into is isnull join key last left like limit match "
    "materialized natural no not nothing notnull null nulls of offset on or order others outer over partition "
    "plan pragma preceding primary query raise range recursive references regexp reindex release rename replace "
    "restrict returning right rollback row rows savepoint select set table temp temporary then ties to "
    "transaction trigger unbounded union unique update using vacuum values view virtual when where window with "
    "without".split()
)


def schema_ident(name: str) -> str:
    """Tên hiển thị trong schema gửi cho LLM: chỉ trích dẫn khi cần (tên có dấu cách, từ khóa...)."""
    if _SIMPLE_IDENT.fullmatch(name) and name.lower() not in _KEYWORDS:
        return name
    return quote_ident(name)


# ── Rào chắn chỉ-đọc ──────────────────────────────────────────────────────
# Mã hành động của SQLite authorizer (https://www.sqlite.org/c3ref/c_alter_table.html)
_OK, _DENY = 0, 1
_READ, _SELECT, _FUNCTION, _RECURSIVE = 20, 21, 31, 33
_DANGEROUS_FUNCTIONS = frozenset({"load_extension", "readfile", "writefile", "edit", "fts3_tokenizer"})


def _read_only_authorizer(action, arg1, arg2, dbname, source):
    if action in (_SELECT, _READ, _RECURSIVE):
        return _OK
    if action == _FUNCTION:
        return _DENY if (arg2 or "").lower() in _DANGEROUS_FUNCTIONS else _OK
    return _DENY


def _friendly_error(exc: Exception) -> str:
    message = str(exc)
    lowered = message.lower()
    if "not authorized" in lowered:
        return "Chỉ cho phép truy vấn đọc (SELECT); câu lệnh ghi/thay đổi schema/PRAGMA/ATTACH bị từ chối."
    if "only execute one statement" in lowered:
        return "Chỉ cho phép một câu lệnh SQL mỗi lần."
    if "interrupted" in lowered:
        return "Truy vấn bị dừng vì vượt quá thời gian thực thi cho phép."
    return message


def _cell(value: object) -> object:
    if isinstance(value, (bytes, bytearray, memoryview)):
        return f"<BLOB {len(bytes(value))} bytes>"
    return value


def _cell_size(value: object) -> int:
    return len(value) if isinstance(value, (str, bytes)) else 8


class SQLiteRepository:
    def __init__(
        self,
        db_path: str | Path,
        *,
        relations: Iterable[Relation] = (),
        max_rows: int = DEFAULT_MAX_ROWS,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_result_bytes: int = DEFAULT_MAX_RESULT_BYTES,
    ):
        self.db_path = str(db_path)
        self.relations: List[Relation] = [tuple(r) for r in relations]  # type: ignore[misc]
        self.max_rows = max_rows
        self.timeout_seconds = timeout_seconds
        self.max_result_bytes = max_result_bytes

    def exists(self) -> bool:
        return os.path.exists(self.db_path)

    # ── Kết nối ────────────────────────────────────────────────────────────
    def _connect_read_only(self, *, restricted: bool = False) -> sqlite3.Connection:
        """Kết nối `mode=ro`. `restricted=True` (dùng cho SQL do LLM sinh) thêm authorizer và giới hạn thời gian."""
        uri = f"{Path(self.db_path).resolve().as_uri()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        if not restricted:
            return conn  # đọc metadata (PRAGMA) do chính backend phát ra
        deadline = time.monotonic() + self.timeout_seconds
        conn.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 1000)
        conn.set_authorizer(_read_only_authorizer)
        return conn

    # ── Schema ─────────────────────────────────────────────────────────────
    def _table_names(self, cur: sqlite3.Cursor) -> List[str]:
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        return [r[0] for r in cur.fetchall() if not r[0].startswith("sqlite_")]

    def get_schema(self) -> str:
        """
        Schema dạng CRP (Code Representation Prompt):
            CREATE TABLE table_name (
              col1 type,  col2 type,
              PRIMARY KEY (col),
              FOREIGN KEY (col) REFERENCES other_table(col)
            )
        Khóa ngoại gồm cả khai báo sẵn trong SQLite lẫn quan hệ người dùng đã xác nhận.
        """
        with closing(self._connect_read_only()) as conn:
            cur = conn.cursor()
            schema_parts = []
            for table in self._table_names(cur):
                cur.execute(f"PRAGMA table_info({quote_ident(table)})")
                cols = cur.fetchall()  # (cid, name, type, notnull, dflt, pk)

                cur.execute(f"PRAGMA foreign_key_list({quote_ident(table)})")
                fks = [(f[3], f[2], f[4]) for f in cur.fetchall()]  # (from, to_table, to_col)
                fks += [(r[1], r[2], r[3]) for r in self.relations if r[0] == table]

                col_defs = ", ".join(f"{schema_ident(c[1])} {c[2]}" for c in cols)
                pk_cols = [schema_ident(c[1]) for c in cols if c[5]]
                fk_defs, seen = [], set()
                for src, dst_table, dst_col in fks:
                    if (src, dst_table, dst_col) in seen:
                        continue
                    seen.add((src, dst_table, dst_col))
                    target = f"{schema_ident(dst_table)}({schema_ident(dst_col)})" if dst_col else schema_ident(dst_table)
                    fk_defs.append(f"FOREIGN KEY ({schema_ident(src)}) REFERENCES {target}")

                all_defs = [col_defs]
                if pk_cols:
                    all_defs.append(f"PRIMARY KEY ({', '.join(pk_cols)})")
                all_defs.extend(fk_defs)

                schema_parts.append(f"CREATE TABLE {schema_ident(table)} (\n  {',  '.join(all_defs)}\n)")

        return "\n\n".join(schema_parts)

    def get_schema_dict(self) -> Dict[str, List[str]]:
        """{table_name: [col_names]} để hiển thị trong UI sidebar."""
        with closing(self._connect_read_only()) as conn:
            cur = conn.cursor()
            result = {}
            for t in self._table_names(cur):
                cur.execute(f"PRAGMA table_info({quote_ident(t)})")
                result[t] = [c[1] for c in cur.fetchall()]
        return result

    def get_sample_rows(self, table: str, n: int = 3) -> str:
        """Lấy n dòng mẫu của một bảng (chưa được pipeline dùng, giữ từ bản gốc)."""
        df, _ = self.execute(f"SELECT * FROM {quote_ident(table)} LIMIT {int(n)}")
        return "" if df is None else df.to_string(index=False)

    def get_table_columns(self) -> Dict[str, List[Dict[str, object]]]:
        """{table: [{name, type, pk}]} theo kiểu khai báo trong SQLite (dùng cho planner/EDA)."""
        with closing(self._connect_read_only()) as conn:
            cur = conn.cursor()
            out: Dict[str, List[Dict[str, object]]] = {}
            for t in self._table_names(cur):
                cur.execute(f"PRAGMA table_info({quote_ident(t)})")
                out[t] = [{"name": c[1], "type": c[2] or "", "pk": bool(c[5])} for c in cur.fetchall()]
        return out

    # ── Execute ────────────────────────────────────────────────────────────
    def execute(self, sql: str) -> Tuple[Optional[pd.DataFrame], Optional[str]]:
        """Chạy SQL chỉ-đọc. Thành công: (df, None). Lỗi: (None, error_str)."""
        return self.fetch(sql, max_rows=self.max_rows, max_bytes=self.max_result_bytes)

    def fetch(
        self, sql: str, *, max_rows: Optional[int] = None, max_bytes: Optional[int] = None
    ) -> Tuple[Optional[pd.DataFrame], Optional[str]]:
        """Như `execute` nhưng giới hạn do người gọi chọn (EDA cần nhiều dòng hơn truy vấn của người dùng).

        Vẫn qua kết nối chỉ-đọc + authorizer + giới hạn thời gian từng câu lệnh.
        """
        max_rows = self.max_rows if max_rows is None else max_rows
        max_bytes = (32 * 1024 * 1024) if max_bytes is None else max_bytes
        try:
            with closing(self._connect_read_only(restricted=True)) as conn:
                cur = conn.execute(sql)
                if cur.description is None:
                    return None, "Câu lệnh không trả về dữ liệu; chỉ cho phép truy vấn SELECT."
                columns = [d[0] for d in cur.description]

                rows, total_bytes, truncated = [], 0, False
                while not truncated:
                    batch = cur.fetchmany(500)
                    if not batch:
                        break
                    for row in batch:
                        if len(rows) >= max_rows:
                            truncated = True
                            break
                        row = tuple(_cell(v) for v in row)
                        rows.append(row)
                        total_bytes += sum(_cell_size(v) for v in row)
                        if total_bytes > max_bytes:
                            truncated = True
                            break

            df = pd.DataFrame.from_records(rows, columns=columns, coerce_float=True)
            if truncated:
                df.attrs["truncated"] = True
            return df, None
        except Exception as e:
            return None, _friendly_error(e)
