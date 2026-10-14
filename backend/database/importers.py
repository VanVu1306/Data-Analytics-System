"""Import dữ liệu người dùng upload (CSV / Excel / SQLite) thành SQLite chuẩn hóa.

Mọi thứ liên quan tới *đọc tệp upload và ghi ra một SQLite mới* nằm trong module này:
kiểu dữ liệu & giới hạn, chuẩn hóa tên, rào chắn SQLite, ghi bảng, và ba importer
(`import_csv`, `import_excel`, `import_sqlite`). `DatasetStore` (dataset_store.py)
chỉ gọi ba hàm import này.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import math
import re
import sqlite3
import time
import unicodedata
import zipfile
from dataclasses import dataclass, field
from itertools import islice
from pathlib import Path
from typing import Sequence

from openpyxl import load_workbook

from backend.database.sqlite_repo import quote_ident
from backend.errors import (
    FileTooLargeError,
    ImportFailedError,
    InvalidFileError,
    UnsafeContentError,
)

# ===========================================================================
# Kiểu dữ liệu & giới hạn import
# Kiểu dữ liệu dùng chung cho các importer.
# ===========================================================================

@dataclass(frozen=True)
class ImportLimits:
    """Giới hạn import. Giá trị thật được `config.py` cung cấp qua container."""

    max_upload_bytes: int = 50 * 1024 * 1024
    max_uncompressed_bytes: int = 300 * 1024 * 1024  # chống zip bomb (.xlsx)
    max_db_bytes: int = 500 * 1024 * 1024  # dung lượng SQLite tạo ra
    max_sheets: int = 50
    max_rows_per_table: int = 200_000  # CSV / mỗi sheet Excel
    max_columns: int = 500
    timeout_seconds: float = 60.0


@dataclass
class ColumnMeta:
    name: str
    type: str  # kiểu lưu trong SQLite (khai báo trong CREATE TABLE)
    source_type: str | None = None  # kiểu quan sát được ở nguồn (csv: "text"; excel: number/text/date...; sqlite: kiểu khai báo)
    inferred_type: str | None = None  # kiểu sau chuẩn hóa: integer|real|text|date|datetime|boolean|mixed|empty|blob|numeric
    notes: list[str] = field(default_factory=list)  # ghi chú chuẩn hóa của cột (NULL, kiểu hỗn hợp, ngày...)


@dataclass
class ColumnReport:
    """Kết quả phân tích một cột khi ghi bảng: dùng để điền ColumnMeta."""

    sql_type: str
    kind: str
    source_type: str
    notes: list[str] = field(default_factory=list)


@dataclass
class TableMeta:
    name: str  # tên bảng trong SQLite (đã chuẩn hóa)
    source_name: str  # tên sheet / tên tệp / tên bảng gốc
    row_count: int
    columns: list[ColumnMeta]


@dataclass
class RelationMeta:
    """Khóa ngoại khai báo sẵn trong SQLite upload."""

    from_table: str
    from_column: str
    to_table: str
    to_column: str | None


@dataclass
class ImportResult:
    tables: list[TableMeta]
    warnings: list[str] = field(default_factory=list)
    relations: list[RelationMeta] = field(default_factory=list)


# ===========================================================================
# Chuẩn hóa tên bảng/cột
# Chuẩn hóa tên bảng/cột về một quy tắc nhất quán.
#
# Quy tắc:
#   * giữ chữ cái Unicode (tiếng Việt có dấu được giữ nguyên), mọi ký tự khác
#     chữ/số/gạch dưới -> "_", gộp nhiều "_" liên tiếp, cắt "_" ở hai đầu;
#   * rỗng -> tên dự phòng (`sheet_N`, `column_N`...);
#   * bắt đầu bằng chữ số -> thêm tiền tố;  tên bắt đầu bằng `sqlite_` bị SQLite
#     cấm -> thêm tiền tố;
#   * tối đa 64 ký tự;
#   * trùng tên (không phân biệt hoa/thường) -> thêm hậu tố `_2`, `_3`...
# ===========================================================================

_NON_WORD = re.compile(r"\W+", re.UNICODE)
_MAX_LEN = 64


def sanitize_identifier(raw: object, fallback: str, prefix: str = "t_") -> str:
    text = unicodedata.normalize("NFC", "" if raw is None else str(raw)).strip()
    text = _NON_WORD.sub("_", text).strip("_")
    if not text:
        text = fallback
    if text[0].isdigit() or text.lower().startswith("sqlite_"):
        text = prefix + text
    return text[:_MAX_LEN]


class NameAllocator:
    """Cấp tên duy nhất (không phân biệt hoa/thường) trong một phạm vi."""

    def __init__(self) -> None:
        self._used: set[str] = set()

    def allocate(self, raw: object, fallback: str, prefix: str = "t_") -> str:
        base = sanitize_identifier(raw, fallback, prefix)
        candidate, n = base, 2
        while candidate.casefold() in self._used:
            suffix = f"_{n}"
            candidate = base[: _MAX_LEN - len(suffix)] + suffix
            n += 1
        self._used.add(candidate.casefold())
        return candidate


# ===========================================================================
# Rào chắn SQLite khi import
# Rào chắn SQLite cho việc import dữ liệu do người dùng cung cấp.
#
# Dùng `sqlite3.Connection.set_authorizer`. Mã hành động là hằng số cố định của
# SQLite (https://www.sqlite.org/c3ref/c_alter_table.html) — khai báo cứng để
# không phụ thuộc phiên bản Python.
# ===========================================================================

_OK, _DENY = 0, 1

_CREATE_TABLE, _INSERT, _UPDATE = 2, 18, 23
_READ, _SELECT, _TRANSACTION, _PRAGMA = 20, 21, 22, 19
_FUNCTION, _SAVEPOINT, _RECURSIVE = 31, 32, 33

_DANGEROUS_FUNCTIONS = frozenset({"load_extension", "readfile", "writefile", "edit", "fts3_tokenizer"})
_INTERNAL_TABLES = frozenset(
    {"sqlite_master", "sqlite_schema", "sqlite_temp_master", "sqlite_temp_schema", "sqlite_sequence"}
)
_SOURCE_PRAGMAS = frozenset({"table_info", "table_xinfo"})


def target_authorizer(action, arg1, arg2, dbname, source):
    """DB đích (do backend tạo): chỉ cho CREATE TABLE và INSERT dữ liệu."""
    if action in (_SELECT, _READ, _TRANSACTION, _SAVEPOINT, _RECURSIVE, _CREATE_TABLE):
        return _OK
    if action == _FUNCTION:
        return _DENY if (arg2 or "").lower() in _DANGEROUS_FUNCTIONS else _OK
    if action in (_INSERT, _UPDATE):
        table = (arg1 or "").lower()
        # CREATE TABLE tự ghi vào sqlite_master; bảng sqlite_* khác bị cấm.
        return _OK if (not table.startswith("sqlite_") or table in _INTERNAL_TABLES) else _DENY
    return _DENY  # DROP, ALTER, ATTACH, PRAGMA, TRIGGER, VIEW, VIRTUAL TABLE, DELETE...


def source_authorizer(action, arg1, arg2, dbname, source):
    """DB nguồn (file SQLite người dùng upload): chỉ đọc."""
    if action in (_SELECT, _READ, _TRANSACTION, _RECURSIVE):
        return _OK
    if action == _FUNCTION:
        return _DENY if (arg2 or "").lower() in _DANGEROUS_FUNCTIONS else _OK
    if action == _PRAGMA:
        return _OK if (arg1 or "").lower() in _SOURCE_PRAGMAS else _DENY
    return _DENY


def open_target(db_path: Path, limits: ImportLimits) -> sqlite3.Connection:
    """Tạo DB đích với giới hạn dung lượng, thời gian và authorizer."""
    conn = sqlite3.connect(db_path, isolation_level=None)
    page_size = conn.execute("PRAGMA page_size").fetchone()[0]
    conn.execute(f"PRAGMA max_page_count={max(1, limits.max_db_bytes // page_size)}")
    deadline = time.monotonic() + limits.timeout_seconds
    conn.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 10_000)
    conn.set_authorizer(target_authorizer)
    return conn


def seal_target(conn: sqlite3.Connection) -> None:
    """Bỏ rào chắn sau khi import xong để đọc metadata (PRAGMA...)."""
    conn.set_authorizer(None)
    conn.set_progress_handler(None, 0)


def translate_sqlite_error(exc: sqlite3.Error, context: str) -> Exception:
    """Đổi lỗi sqlite3 thành lỗi nghiệp vụ có thông điệp dễ hiểu."""
    message = str(exc).lower()
    if "not authorized" in message:
        return UnsafeContentError(f"{context}: có thao tác không được phép (trigger, ATTACH, PRAGMA...).")
    if "interrupted" in message:
        return ImportFailedError(f"{context}: quá thời gian xử lý cho phép.")
    if "database or disk is full" in message:
        return FileTooLargeError("Dữ liệu sau khi import vượt giới hạn dung lượng cho phép.")
    if "not a database" in message or "malformed" in message:
        return InvalidFileError(f"{context}: file không phải database SQLite hợp lệ hoặc đã bị hỏng.")
    return ImportFailedError(f"{context}: {exc}")


# ===========================================================================
# Suy luận kiểu, ghi bảng, đọc metadata
# Ghi bảng vào SQLite đích + đọc metadata; dùng chung cho CSV/Excel/SQLite.
# ===========================================================================

_BATCH = 5000
_INT64_MIN, _INT64_MAX = -(2**63), 2**63 - 1

_INT_RE = re.compile(r"^[+-]?\d+$")
_FLOAT_RE = re.compile(r"^[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$")
_LEADING_ZERO_RE = re.compile(r"^[+-]?0\d")  # "007", "0123": mã/ID, giữ dạng chữ


# --------------------------------------------------------------------------- #
# Suy luận kiểu
# --------------------------------------------------------------------------- #
def _to_text(value: object) -> str:
    if isinstance(value, dt.datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, (dt.date, dt.time)):
        return value.isoformat()
    return str(value)


def infer_column(values: Sequence[object]) -> tuple[str, list[object]]:
    """Trả về (kiểu SQLite, giá trị đã chuyển đổi) cho một cột.

    Cột thuần bool/int -> INTEGER; có float -> REAL; thuần ngày/giờ -> TEXT
    dạng ISO (chỉ ngày nếu mọi giá trị đều 00:00:00); còn lại (kể cả lẫn kiểu)
    -> TEXT. Số nguyên vượt int64 -> TEXT. NaN/inf -> NULL.
    """
    present = [v for v in values if v is not None]
    if not present:
        return "TEXT", list(values)

    if all(isinstance(v, (bool, int, float)) for v in present):
        if any(isinstance(v, float) for v in present):
            out = [None if v is None or not math.isfinite(v) else float(v) for v in values]
            return "REAL", out
        if all(_INT64_MIN <= int(v) <= _INT64_MAX for v in present):
            return "INTEGER", [None if v is None else int(v) for v in values]
        return "TEXT", [None if v is None else str(v) for v in values]

    if all(isinstance(v, (dt.datetime, dt.date)) for v in present):
        midnight = dt.time(0, 0)
        date_only = all(not isinstance(v, dt.datetime) or v.time() == midnight for v in present)
        if date_only:
            out = [None if v is None else (v.date() if isinstance(v, dt.datetime) else v).isoformat() for v in values]
        else:
            out = [None if v is None else _to_text(v) for v in values]
        return "TEXT", out

    return "TEXT", [None if v is None else _to_text(v) for v in values]


def coerce_text_column(values: Sequence[str | None]) -> list[object]:
    """CSV: chuyển cột chuỗi thành int/float nếu *mọi* giá trị đều là số.

    Giá trị có số 0 đứng đầu (mã, số điện thoại) giữ nguyên dạng chữ.
    """
    present = [v for v in values if v is not None]
    if present and not any(_LEADING_ZERO_RE.match(v) for v in present):
        if all(_INT_RE.match(v) for v in present):
            ints = [None if v is None else int(v) for v in values]
            if all(i is None or _INT64_MIN <= i <= _INT64_MAX for i in ints):
                return ints
        elif all(_INT_RE.match(v) or _FLOAT_RE.match(v) for v in present):
            return [None if v is None else float(v) for v in values]
    return list(values)


# --------------------------------------------------------------------------- #
# Phân tích cột: NULL-token, kiểu hỗn hợp, ngày tháng
# --------------------------------------------------------------------------- #
_NULL_TOKENS = frozenset({"na", "n/a", "#n/a", "null", "nan", "none", "nil"})
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?)?$")
_AMBIGUOUS_DATE_RE = re.compile(r"^\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}$")


def _is_number(v: object) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _is_null_token(v: object) -> bool:
    return isinstance(v, str) and v.strip().casefold() in _NULL_TOKENS


def normalize_null_tokens(values: Sequence[object]) -> tuple[list[object], list[str]]:
    """Cột số có lẫn 'NA', 'N/A', 'null'... -> chuyển các token này thành NULL (chỉ khi phần còn lại đều là số).

    Không đụng tới cột văn bản: 'none'/'na' có thể là giá trị thật. Trả về (giá trị mới, ghi chú).
    """
    tokens = [v for v in values if _is_null_token(v)]
    if not tokens:
        return list(values), []
    rest = [v for v in values if v is not None and not _is_null_token(v)]
    numeric = rest and all(_is_number(v) or (isinstance(v, str) and (_INT_RE.match(v) or _FLOAT_RE.match(v))) for v in rest)
    if not numeric:
        return list(values), []
    out = [None if _is_null_token(v) else v for v in values]
    return out, [f"{len(tokens)} giá trị kiểu 'NA'/'N/A'/'null' trong cột số đã chuyển thành NULL."]


def _kind_of(v: object) -> str:
    if isinstance(v, bool):
        return "boolean"
    if isinstance(v, int):
        return "integer"
    if isinstance(v, float):
        return "real"
    if isinstance(v, dt.datetime):
        return "datetime"
    if isinstance(v, dt.date):
        return "date"
    if isinstance(v, dt.time):
        return "time"
    return "text"


def analyze_column(values: Sequence[object], notes: list[str] | None = None, source_hint: str | None = None) -> ColumnReport:
    """Phân loại cột trước khi chuyển đổi: kiểu quan sát, kiểu suy luận và ghi chú.

    `values` là giá trị gốc (trước `infer_column`). `source_hint`: kiểu nguồn cố định (CSV -> "text").
    """
    notes = list(notes or [])
    sql_type, _ = infer_column(values)
    present = [v for v in values if v is not None]
    if not present:
        return ColumnReport("TEXT", "empty", source_hint or "empty", notes)

    kinds = {_kind_of(v) for v in present}
    observed = source_hint or ",".join(sorted(k if k != "integer" and k != "real" else "number" for k in kinds))
    numeric_kinds = {"integer", "real"}
    if kinds <= numeric_kinds | {"boolean"} and kinds & numeric_kinds and "boolean" in kinds:
        kind = "mixed"
    elif kinds == {"boolean"}:
        kind = "boolean"
        notes.append("Giá trị đúng/sai đã lưu dạng INTEGER 0/1.")
    elif kinds <= numeric_kinds:
        kind = "real" if sql_type == "REAL" else ("integer" if sql_type == "INTEGER" else "text")
        if sql_type == "TEXT":
            notes.append("Số nguyên vượt phạm vi int64 nên được lưu dạng TEXT.")
    elif kinds <= {"date", "datetime"}:
        kind = "datetime" if any(isinstance(v, dt.datetime) and v.time() != dt.time(0, 0) for v in present) else "date"
        notes.append("Ngày/giờ được lưu dạng TEXT theo ISO 8601.")
    elif kinds == {"text"}:
        texts = [str(v) for v in present]
        if all(_ISO_DATE_RE.match(t) for t in texts):
            kind = "datetime" if any(len(t) > 10 for t in texts) else "date"
            notes.append("Giá trị dạng ngày ISO (YYYY-MM-DD); lưu dạng TEXT, không đổi định dạng.")
        else:
            kind = "text"
            if all(_AMBIGUOUS_DATE_RE.match(t) for t in texts):
                notes.append("Có vẻ là ngày dạng d/m/y hoặc m/d/y (mơ hồ); giữ nguyên TEXT, không chuyển đổi.")
    else:
        kind = "mixed"
    if kind == "mixed":
        counts = {}
        for v in present:
            counts[_kind_of(v)] = counts.get(_kind_of(v), 0) + 1
        detail = ", ".join(f"{n} {k}" for k, n in sorted(counts.items()))
        notes.append(f"Kiểu dữ liệu hỗn hợp ({detail}); toàn bộ được lưu dạng TEXT.")
    return ColumnReport(sql_type, kind, observed, notes)


# --------------------------------------------------------------------------- #
# Ghi bảng
# --------------------------------------------------------------------------- #
def write_table(
    conn: sqlite3.Connection,
    table: str,
    headers: Sequence[str],
    rows: Sequence[Sequence[object]],
    *,
    column_notes: dict[int, list[str]] | None = None,
    source_hint: str | None = None,
) -> list[ColumnReport]:
    """Tạo bảng `table` và ghi `rows` (mỗi dòng được đệm None cho đủ cột). Trả về báo cáo từng cột."""
    column_notes = column_notes or {}
    width = len(headers)
    types: list[str] = []
    columns: list[list[object]] = []
    reports: list[ColumnReport] = []
    for j in range(width):
        column = [row[j] if j < len(row) else None for row in rows]
        sql_type, converted = infer_column(column)
        types.append(sql_type)
        columns.append(converted)
        reports.append(analyze_column(column, column_notes.get(j), source_hint))

    column_sql = ", ".join(f"{quote_ident(h)} {t}" for h, t in zip(headers, types))
    conn.execute("BEGIN")
    conn.execute(f"CREATE TABLE {quote_ident(table)} ({column_sql})")
    insert = f"INSERT INTO {quote_ident(table)} VALUES ({', '.join('?' * width)})"
    iterator = iter(zip(*columns)) if rows else iter(())
    while batch := list(islice(iterator, _BATCH)):
        conn.executemany(insert, batch)
    conn.execute("COMMIT")
    return reports


def report_warnings(table: str, headers: Sequence[str], reports: Sequence[ColumnReport], limit: int = 5) -> list[str]:
    """Một cảnh báo gộp cho mỗi bảng (không một dòng cho mỗi cột); chi tiết nằm trong `notes` của từng cột."""
    keys = ("hỗn hợp", "NA", "0 đứng đầu", "mơ hồ")
    flagged = [h for h, r in zip(headers, reports) if any(k in n for n in r.notes for k in keys)]
    if not flagged:
        return []
    shown = ", ".join(flagged[:limit]) + (f" … (+{len(flagged) - limit})" if len(flagged) > limit else "")
    return [f"Bảng '{table}': {len(flagged)} cột được chuẩn hóa đặc biệt ({shown}). Xem ghi chú theo cột trong 'Tổng quan dataset'."]


# --------------------------------------------------------------------------- #
# Metadata
# --------------------------------------------------------------------------- #
_AFFINITY_KIND = (("int", "integer"), ("real", "real"), ("floa", "real"), ("doub", "real"), ("char", "text"),
                  ("clob", "text"), ("text", "text"), ("blob", "blob"), ("num", "numeric"), ("dec", "numeric"),
                  ("bool", "numeric"), ("date", "numeric"))


def declared_kind(declared: str) -> str:
    """Kiểu suy luận từ kiểu khai báo của SQLite (quy tắc affinity)."""
    d = (declared or "").lower()
    if not d:
        return "blob"
    for key, kind in _AFFINITY_KIND:
        if key in d:
            return kind
    return "numeric"


def describe_db(
    conn: sqlite3.Connection,
    source_names: dict[str, str] | None = None,
    reports: dict[str, list[ColumnReport]] | None = None,
) -> tuple[list[TableMeta], list[RelationMeta]]:
    """Đọc bảng, cột, số dòng và khóa ngoại của DB đã import (bỏ authorizer trước)."""
    source_names = source_names or {}
    names = [
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name NOT LIKE 'sqlite\\_%' ESCAPE '\\' ORDER BY rowid"
        )
    ]
    tables: list[TableMeta] = []
    relations: list[RelationMeta] = []
    for name in names:
        q = quote_ident(name)
        rep = (reports or {}).get(name)
        columns = []
        for i, r in enumerate(conn.execute(f"PRAGMA table_info({q})").fetchall()):
            if rep is not None and i < len(rep):
                columns.append(ColumnMeta(r[1], r[2] or "", rep[i].source_type, rep[i].kind, list(rep[i].notes)))
            else:  # SQLite upload: kiểu nguồn = kiểu khai báo
                columns.append(ColumnMeta(r[1], r[2] or "", r[2] or "", declared_kind(r[2] or ""), []))
        count = conn.execute(f"SELECT COUNT(*) FROM {q}").fetchone()[0]
        tables.append(TableMeta(name, source_names.get(name, name), count, columns))
        for fk in conn.execute(f"PRAGMA foreign_key_list({q})"):
            relations.append(RelationMeta(name, fk[3], fk[2], fk[4]))
    return tables, relations


# ===========================================================================
# CSV -> một bảng SQLite
# CSV -> một bảng SQLite.
# ===========================================================================

_SNIFF_BYTES = 64 * 1024


def _decode(raw: bytes, warnings: list[str]) -> str:
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16")
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        pass
    try:
        text = raw.decode("cp1258")  # CSV xuất từ Windows tiếng Việt
        warnings.append("Tệp không phải UTF-8; đã đọc bằng bảng mã Windows-1258. Hãy kiểm tra dấu tiếng Việt.")
        return text
    except UnicodeDecodeError:
        warnings.append("Không xác định được bảng mã; đã đọc bằng Latin-1, ký tự có dấu có thể bị sai.")
        return raw.decode("latin-1")


def _dialect(text: str) -> type[csv.Dialect] | csv.Dialect:
    try:
        return csv.Sniffer().sniff(text[:_SNIFF_BYTES], delimiters=",;\t|")
    except csv.Error:
        return csv.excel


def _cell(value: str) -> str | None:
    value = value.replace("\x00", "").strip()
    return value or None


def import_csv(src: Path, db_path: Path, limits: ImportLimits, source_name: str) -> ImportResult:
    raw = Path(src).read_bytes()
    is_utf16 = raw.startswith((b"\xff\xfe", b"\xfe\xff"))
    if raw.startswith((b"PK\x03\x04", b"SQLite format 3\x00")) or (b"\x00" in raw[:8192] and not is_utf16):
        raise InvalidFileError("Nội dung không phải CSV dạng văn bản (có vẻ là tệp nhị phân).")

    warnings: list[str] = []
    text = _decode(raw, warnings)
    try:
        parsed = [r for r in csv.reader(io.StringIO(text, newline=""), _dialect(text)) if any(c.strip() for c in r)]
    except csv.Error as exc:
        raise InvalidFileError(f"Không đọc được CSV: {exc}") from exc

    if not parsed:
        raise InvalidFileError("Tệp CSV không có dữ liệu.")
    header, body = parsed[0], parsed[1:]
    if len(body) > limits.max_rows_per_table:
        raise InvalidFileError(f"CSV có {len(body):,} dòng, vượt giới hạn {limits.max_rows_per_table:,} dòng.")
    width = max(len(r) for r in parsed)
    if width > limits.max_columns:
        raise InvalidFileError(f"CSV có {width} cột, vượt giới hạn {limits.max_columns} cột.")
    if width > len(header):
        warnings.append("Một số dòng có nhiều cột hơn dòng tiêu đề; các cột thừa được đặt tên tự động.")

    cols = NameAllocator()
    headers = [
        cols.allocate(header[j] if j < len(header) else None, f"column_{j + 1}", prefix="c_") for j in range(width)
    ]
    cleaned = [[_cell(c) for c in row] for row in body]
    columns: list[list[object]] = []
    column_notes: dict[int, list[str]] = {}
    for j in range(width):
        raw_col = [row[j] if j < len(row) else None for row in cleaned]
        notes: list[str] = []
        blanks = sum(1 for row in body if j < len(row) and not row[j].replace("\x00", "").strip())
        if blanks:
            notes.append(f"{blanks} ô trống đã chuyển thành NULL.")
        raw_col, null_notes = normalize_null_tokens(raw_col)
        notes += null_notes
        converted = coerce_text_column(raw_col)
        present = [v for v in raw_col if v is not None]
        if present and any(isinstance(v, str) for v in converted if v is not None) and all(
            _INT_RE.match(v) or _FLOAT_RE.match(v) for v in present
        ):
            notes.append("Giữ dạng chữ vì có số 0 đứng đầu (mã/ID/số điện thoại).")
        if present and any(isinstance(v, str) for v in converted if v is not None):
            numeric_like = sum(1 for v in present if _INT_RE.match(v) or _FLOAT_RE.match(v))
            if 0 < numeric_like < len(present) and numeric_like != len(present):
                notes.append(f"Kiểu hỗn hợp: gồm cả số ({numeric_like}) và chữ ({len(present) - numeric_like}); giữ TEXT.")
        columns.append(converted)
        column_notes[j] = notes
    rows = [list(r) for r in zip(*columns)] if cleaned else []

    table = NameAllocator().allocate(Path(source_name).stem, "data")
    conn = open_target(db_path, limits)
    try:
        reports = write_table(conn, table, headers, rows, column_notes=column_notes, source_hint="text")
        seal_target(conn)
        tables, relations = describe_db(conn, {table: Path(source_name).stem}, {table: reports})
        warnings += report_warnings(table, headers, reports)
    except sqlite3.Error as exc:
        raise translate_sqlite_error(exc, "Import CSV") from exc
    finally:
        conn.close()
    return ImportResult(tables, warnings, relations)


# ===========================================================================
# Excel (.xlsx/.xlsm) -> mỗi sheet một bảng
# Excel (.xlsx/.xlsm) -> mỗi sheet một bảng trong cùng một SQLite.
# ===========================================================================

_ERROR_LITERAL = re.compile(r"^#(DIV/0!|N/A|NAME\?|NULL!|NUM!|REF!|VALUE!)$")
_HEADER_LOOKAHEAD = 10


def _check_zip(path: Path, limits: ImportLimits) -> None:
    with open(path, "rb") as fh:
        if fh.read(4) != b"PK\x03\x04":
            raise InvalidFileError(
                "Tệp không phải .xlsx hợp lệ (có thể là .xls cũ, bị đặt mật khẩu hoặc đã hỏng). "
                "Hãy lưu lại dưới dạng .xlsx không mật khẩu."
            )
    try:
        with zipfile.ZipFile(path) as zf:
            names = set(zf.namelist())
            if "xl/workbook.xml" not in names:
                raise InvalidFileError("Tệp không phải workbook Excel (.xlsx) hợp lệ.")
            if sum(i.file_size for i in zf.infolist()) > limits.max_uncompressed_bytes:
                raise FileTooLargeError("Nội dung Excel sau giải nén vượt giới hạn cho phép.")
    except zipfile.BadZipFile as exc:
        raise InvalidFileError("Tệp Excel bị hỏng (không giải nén được).") from exc


def _clean_cell(value: object, counter: dict | None = None) -> object:
    if isinstance(value, str):
        value = value.replace("\x00", "").strip()
        if not value:
            if counter is not None:
                counter["blank"] = counter.get("blank", 0) + 1
            return None
        if _ERROR_LITERAL.match(value):
            if counter is not None:
                counter["error"] = counter.get("error", 0) + 1
            return None
        return value
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _read_sheet(ws, limits: ImportLimits, counter: dict | None = None) -> list[list[object]]:
    rows: list[list[object]] = []
    reset = getattr(ws, "reset_dimensions", None)
    if reset:  # một số trình xuất khai báo sai <dimension> khiến chế độ read-only chỉ đọc 1 ô
        reset()
    for raw in ws.iter_rows(values_only=True):
        row = [_clean_cell(v, counter) for v in raw]
        while row and row[-1] is None:
            row.pop()
        if not row or all(v is None for v in row):
            continue
        if len(row) > limits.max_columns:
            raise InvalidFileError(f"Sheet có hơn {limits.max_columns} cột, vượt giới hạn cho phép.")
        rows.append(row)
        if len(rows) > limits.max_rows_per_table + _HEADER_LOOKAHEAD:
            raise InvalidFileError(f"Sheet vượt giới hạn {limits.max_rows_per_table:,} dòng.")
    return rows


def _header_index(rows: list[list[object]]) -> int:
    """Bỏ qua dòng tiêu đề lớn/ghi chú phía trên: lấy dòng đầu tiên đủ 'rộng'."""
    counts = [sum(v is not None for v in r) for r in rows[:_HEADER_LOOKAHEAD]]
    threshold = max(1, math.ceil(max(counts) * 0.5))
    return next(i for i, c in enumerate(counts) if c >= threshold)


def import_excel(src: Path, db_path: Path, limits: ImportLimits) -> ImportResult:
    src = Path(src)
    _check_zip(src, limits)
    try:
        workbook = load_workbook(src, read_only=True, data_only=True)
    except Exception as exc:  # openpyxl ném nhiều loại lỗi khác nhau cho tệp hỏng
        raise InvalidFileError(f"Không đọc được tệp Excel: {exc}") from exc

    warnings: list[str] = []
    table_names = NameAllocator()
    source_names: dict[str, str] = {}
    all_reports: dict[str, list[ColumnReport]] = {}
    conn = open_target(db_path, limits)
    try:
        sheets = workbook.worksheets
        if len(sheets) > limits.max_sheets:
            raise InvalidFileError(f"Workbook có {len(sheets)} sheet, vượt giới hạn {limits.max_sheets}.")
        for ws in sheets:
            title = ws.title
            if getattr(ws, "sheet_state", "visible") != "visible":
                warnings.append(f"Sheet ẩn '{title}' được bỏ qua.")
                continue
            counter: dict = {}
            rows = _read_sheet(ws, limits, counter)
            if counter.get("error"):
                warnings.append(f"Sheet '{title}': {counter['error']} ô lỗi công thức (#DIV/0!, #N/A...) đã chuyển thành NULL.")
            if not rows:
                warnings.append(f"Sheet '{title}' trống nên được bỏ qua.")
                continue
            start = _header_index(rows)
            if start:
                warnings.append(f"Sheet '{title}': bỏ qua {start} dòng phía trên dòng tiêu đề.")
            header, body = rows[start], rows[start + 1 :]
            if not body:
                warnings.append(f"Sheet '{title}' chỉ có dòng tiêu đề, không có dữ liệu nên được bỏ qua.")
                continue

            width = max(len(r) for r in rows[start:])
            header = header + [None] * (width - len(header))
            keep = [j for j in range(width) if header[j] is not None or any(j < len(r) and r[j] is not None for r in body)]
            col_names = NameAllocator()
            headers = [col_names.allocate(header[j], f"column_{j + 1}", prefix="c_") for j in keep]
            data = [[r[j] if j < len(r) else None for j in keep] for r in body]

            table = table_names.allocate(title, f"sheet_{len(source_names) + 1}")
            col_notes: dict[int, list[str]] = {}
            for k in range(len(keep)):
                col = [r[k] for r in data]
                col, null_notes = normalize_null_tokens(col)
                for r, v in zip(data, col):
                    r[k] = v
                col_notes[k] = null_notes
            reports = write_table(conn, table, headers, data, column_notes=col_notes)
            all_reports[table] = reports
            warnings += report_warnings(table, headers, reports)
            source_names[table] = title
        if not source_names:
            raise InvalidFileError("Tệp Excel không có sheet nào chứa dữ liệu.")
        seal_target(conn)
        tables, relations = describe_db(conn, source_names, all_reports)
    except sqlite3.Error as exc:
        raise translate_sqlite_error(exc, "Import Excel") from exc
    finally:
        conn.close()
        workbook.close()
    return ImportResult(tables, warnings, relations)


# ===========================================================================
# SQLite upload (.db/.sqlite) -> DB mới do backend tạo
# SQLite upload (.db/.sqlite) -> DB mới do backend tạo.
#
# Không dùng trực tiếp file người dùng gửi: mở nó ở chế độ chỉ đọc, rồi *tạo lại*
# từng bảng (câu CREATE TABLE gốc, nên giữ khóa ngoại/ràng buộc) trong DB mới qua
# authorizer chỉ cho CREATE TABLE + INSERT. View, trigger, bảng ảo và index bị bỏ
# (trigger/bảng ảo có thể chạy mã khi truy vấn về sau); tên bảng/cột giữ nguyên.
# ===========================================================================

_MAGIC = b"SQLite format 3\x00"


def import_sqlite(src: Path, db_path: Path, limits: ImportLimits) -> ImportResult:
    src = Path(src)
    with open(src, "rb") as fh:
        if fh.read(16) != _MAGIC:
            raise InvalidFileError("Tệp không phải database SQLite hợp lệ (sai header).")

    warnings: list[str] = []
    source = sqlite3.connect(f"{src.resolve().as_uri()}?mode=ro&immutable=1", uri=True)
    target = None
    try:
        try:
            source.execute("PRAGMA trusted_schema=OFF")
            status = source.execute("PRAGMA quick_check(1)").fetchone()
            if not status or status[0] != "ok":
                raise InvalidFileError("Database SQLite bị hỏng (kiểm tra toàn vẹn không đạt).")
            source.set_authorizer(source_authorizer)

            schema = source.execute(
                "SELECT name, sql FROM sqlite_master WHERE type = 'table' "
                "AND name NOT LIKE 'sqlite\\_%' ESCAPE '\\' ORDER BY rowid"
            ).fetchall()
            skipped = dict(
                source.execute(
                    "SELECT type, COUNT(*) FROM sqlite_master WHERE type IN ('view', 'trigger') GROUP BY type"
                ).fetchall()
            )
        except sqlite3.Error as exc:
            raise translate_sqlite_error(exc, "Đọc database SQLite") from exc

        real_tables = []
        for name, sql in schema:
            if sql and sql.lstrip().upper().startswith("CREATE VIRTUAL"):
                warnings.append(f"Bảng ảo '{name}' không được hỗ trợ nên bị bỏ qua.")
            elif sql:
                real_tables.append((name, sql))
        if not real_tables:
            raise InvalidFileError("Database SQLite không có bảng dữ liệu nào.")
        for kind, label in (("view", "view"), ("trigger", "trigger")):
            if skipped.get(kind):
                warnings.append(f"Bỏ qua {skipped[kind]} {label} trong database (chỉ import bảng dữ liệu).")

        for name, _sql in real_tables:
            count = source.execute(f"SELECT COUNT(*) FROM {quote_ident(name)}").fetchone()[0]
            if count > limits.max_rows_per_table:
                raise InvalidFileError(
                    f"Bảng '{name}' có {count:,} dòng, vượt giới hạn {limits.max_rows_per_table:,} dòng."
                )

        target = open_target(db_path, limits)
        try:
            target.execute("BEGIN")
            for name, create_sql in real_tables:
                target.execute(create_sql)
                columns = [
                    r[1] for r in source.execute(f"PRAGMA table_xinfo({quote_ident(name)})") if r[6] == 0
                ]  # r[6]=hidden; 0 = cột thường (bỏ cột sinh tự động)
                if not columns:
                    continue
                select = f"SELECT {', '.join(quote_ident(c) for c in columns)} FROM {quote_ident(name)}"
                insert = (
                    f"INSERT INTO {quote_ident(name)} ({', '.join(quote_ident(c) for c in columns)}) "
                    f"VALUES ({', '.join('?' * len(columns))})"
                )
                cursor = source.execute(select)
                while batch := cursor.fetchmany(_BATCH):
                    target.executemany(insert, batch)
            target.execute("COMMIT")
            seal_target(target)
            tables, relations = describe_db(target)
        except sqlite3.Error as exc:
            raise translate_sqlite_error(exc, "Import SQLite") from exc
    finally:
        if target is not None:
            target.close()
        source.close()
    return ImportResult(tables, warnings, relations)
