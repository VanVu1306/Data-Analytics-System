"""Registry dataset: mỗi upload = một thư mục riêng chứa `data.db` + `meta.json`.

Nguyên tắc (theo TODO.md):
  * đường dẫn do server tạo, client chỉ biết `dataset_id` (uuid4 hex, kiểm tra
    định dạng trước khi ghép đường dẫn);
  * tên tệp/MIME do client gửi không bao giờ dùng làm đường dẫn;
  * import thất bại -> xóa sạch thư mục dataset (kể cả tệp tạm);
  * tệp gốc người dùng gửi bị xóa sau khi import, chỉ giữ SQLite chuẩn hóa.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from backend.errors import (
    DatasetNotFoundError,
    FileTooLargeError,
    InvalidFileError,
    InvalidRelationError,
    RelationNotFoundError,
    UnsupportedFormatError,
)

from .importers import ImportLimits, ImportResult, import_csv, import_excel, import_sqlite
from .sqlite_repo import (
    DEFAULT_MAX_ROWS,
    DEFAULT_TIMEOUT_SECONDS,
    SQLiteRepository,
)

_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_DB_FILE, _META_FILE = "data.db", "meta.json"
_UPLOAD_NAME = {"csv": "upload.csv", "excel": "upload.xlsx", "sqlite": "upload.sqlite"}  # đuôi do server đặt

_FORMATS = {
    ".csv": "csv",
    ".xlsx": "excel",
    ".xlsm": "excel",
    ".db": "sqlite",
    ".sqlite": "sqlite",
    ".sqlite3": "sqlite",
}
_SUPPORTED_TEXT = "CSV (.csv), Excel (.xlsx) hoặc SQLite (.db, .sqlite)"
_REJECTED = {
    ".sql": "Hiện chưa hỗ trợ file script .sql vì không thực thi script tùy ý. "
    f"Hãy dùng {_SUPPORTED_TEXT}.",
    ".json": f"Định dạng JSON chưa được hỗ trợ. Hãy dùng {_SUPPORTED_TEXT}.",
    ".xls": "Định dạng .xls (Excel cũ) chưa được hỗ trợ. Hãy lưu lại thành .xlsx.",
}


@dataclass
class DatasetInfo:
    dataset_id: str
    filename: str
    format: str
    size_bytes: int
    created_at: str
    status: str = "ready"
    tables: list[dict] = field(default_factory=list)
    relations: list[dict] = field(default_factory=list)  # khóa ngoại khai báo sẵn trong SQLite
    confirmed_relations: list[dict] = field(default_factory=list)  # quan hệ người dùng xác nhận
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _safe_filename(filename: str) -> str:
    name = os.path.basename((filename or "").replace("\\", "/")).strip()
    return (name or "upload")[:255]


_REL_KEYS = ("from_table", "from_column", "to_table", "to_column")


class DatasetStore:
    """Registry dataset upload + điểm tra cứu repository theo `dataset_id`.

    `repository(None)` trả về database mặc định (demo, luồng cũ); `repository(id)` chỉ trả
    dataset đã upload, id sai/không tồn tại -> DatasetNotFoundError (không fallback sang demo).
    """

    def __init__(
        self,
        root: str | Path,
        limits: ImportLimits | None = None,
        *,
        default_db_path: str | Path | None = None,
        query_max_rows: int = DEFAULT_MAX_ROWS,
        query_timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.limits = limits or ImportLimits()
        self._default_db_path = default_db_path
        self._query_kwargs = dict(max_rows=query_max_rows, timeout_seconds=query_timeout_seconds)
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ #
    def create(self, filename: str, content: bytes) -> DatasetInfo:
        safe_name = _safe_filename(filename)
        fmt = self._detect_format(safe_name)
        if not content:
            raise InvalidFileError("Tệp rỗng.")
        if len(content) > self.limits.max_upload_bytes:
            limit_mb = self.limits.max_upload_bytes / (1024 * 1024)
            raise FileTooLargeError(f"Tệp vượt giới hạn {limit_mb:g} MB.")

        dataset_id = uuid.uuid4().hex
        directory = self.root / dataset_id
        directory.mkdir()
        try:
            upload = directory / _UPLOAD_NAME[fmt]
            upload.write_bytes(content)
            db_path = directory / _DB_FILE
            result = self._import(fmt, upload, db_path, safe_name)
            upload.unlink()
            info = DatasetInfo(
                dataset_id=dataset_id,
                filename=safe_name,
                format=fmt,
                size_bytes=len(content),
                created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                tables=[asdict(t) for t in result.tables],
                relations=[asdict(r) for r in result.relations],
                warnings=result.warnings,
            )
            self._write_meta(directory, info)
        except BaseException:
            shutil.rmtree(directory, ignore_errors=True)
            raise
        return info

    def get(self, dataset_id: str) -> DatasetInfo:
        meta_path = self._dir(dataset_id) / _META_FILE
        try:
            data = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise DatasetNotFoundError("Không tìm thấy dataset.") from exc
        return DatasetInfo(**data)

    def db_path(self, dataset_id: str) -> Path:
        """Đường dẫn SQLite của dataset (dùng cho repository ở giai đoạn 2)."""
        path = self._dir(dataset_id) / _DB_FILE
        if not path.is_file():
            raise DatasetNotFoundError("Không tìm thấy dataset.")
        return path

    def repository(self, dataset_id: str | None = None) -> SQLiteRepository:
        """Repository chỉ-đọc cho dataset (None = database mặc định)."""
        if dataset_id is None:
            if self._default_db_path is None:
                raise DatasetNotFoundError("Chưa cấu hình dataset mặc định.")
            return SQLiteRepository(self._default_db_path, **self._query_kwargs)
        info = self.get(dataset_id)
        relations = [tuple(r[k] for k in _REL_KEYS) for r in info.confirmed_relations]
        return SQLiteRepository(self.db_path(dataset_id), relations=relations, **self._query_kwargs)

    # ---------------------- quan hệ khai báo thủ công ------------------- #
    def add_relation(self, dataset_id: str, from_table: str, from_column: str, to_table: str, to_column: str) -> DatasetInfo:
        rel = dict(zip(_REL_KEYS, (from_table, from_column, to_table, to_column)))
        with self._lock:
            info = self.get(dataset_id)
            columns = {t["name"]: {c["name"] for c in t["columns"]} for t in info.tables}
            for table, column in ((from_table, from_column), (to_table, to_column)):
                if table not in columns:
                    raise InvalidRelationError(f"Bảng '{table}' không tồn tại trong dataset.")
                if column not in columns[table]:
                    raise InvalidRelationError(f"Cột '{column}' không tồn tại trong bảng '{table}'.")
            if rel not in info.confirmed_relations:
                info.confirmed_relations.append(rel)
                self._write_meta(self._dir(dataset_id), info)
        return info

    def remove_relation(self, dataset_id: str, from_table: str, from_column: str, to_table: str, to_column: str) -> DatasetInfo:
        rel = dict(zip(_REL_KEYS, (from_table, from_column, to_table, to_column)))
        with self._lock:
            info = self.get(dataset_id)
            if rel not in info.confirmed_relations:
                raise RelationNotFoundError("Quan hệ này chưa được khai báo.")
            info.confirmed_relations.remove(rel)
            self._write_meta(self._dir(dataset_id), info)
        return info

    # ------------------------------------------------------------------ #
    def _dir(self, dataset_id: str) -> Path:
        if not isinstance(dataset_id, str) or not _ID_RE.match(dataset_id):
            raise DatasetNotFoundError("Không tìm thấy dataset.")
        return self.root / dataset_id

    @staticmethod
    def _detect_format(filename: str) -> str:
        ext = os.path.splitext(filename)[1].lower()
        if ext in _FORMATS:
            return _FORMATS[ext]
        raise UnsupportedFormatError(
            _REJECTED.get(ext) or f"Định dạng '{ext or filename}' chưa được hỗ trợ. Hãy dùng {_SUPPORTED_TEXT}."
        )

    def _import(self, fmt: str, upload: Path, db_path: Path, filename: str) -> ImportResult:
        if fmt == "csv":
            return import_csv(upload, db_path, self.limits, filename)
        if fmt == "excel":
            return import_excel(upload, db_path, self.limits)
        return import_sqlite(upload, db_path, self.limits)

    @staticmethod
    def _write_meta(directory: Path, info: DatasetInfo) -> None:
        tmp = directory / (_META_FILE + ".tmp")
        tmp.write_text(json.dumps(info.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(directory / _META_FILE)
