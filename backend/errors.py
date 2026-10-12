"""Lỗi dùng chung cho chức năng upload dataset.

Module lá (không import gì của backend) để cả `database`, `tools`, `pipeline`
và `api` cùng dùng được mà không vi phạm quy tắc phụ thuộc giữa các lớp.
Mỗi lỗi có `code` ổn định để frontend/API ánh xạ; `str(exc)` là thông điệp
tiếng Việt hiển thị được cho người dùng.
"""

from __future__ import annotations


class DatasetError(Exception):
    code = "dataset_error"


class UnsupportedFormatError(DatasetError):
    """Phần mở rộng/định dạng không được hỗ trợ (kể cả .json, .sql ở giai đoạn MVP)."""

    code = "unsupported_format"


class FileTooLargeError(DatasetError):
    """Tệp upload hoặc dữ liệu sau import vượt giới hạn."""

    code = "file_too_large"


class InvalidFileError(DatasetError):
    """Tệp rỗng, hỏng, sai nội dung so với phần mở rộng, hoặc không có dữ liệu."""

    code = "invalid_file"


class UnsafeContentError(DatasetError):
    """Nội dung có thao tác không được phép (ví dụ DDL lạ trong SQLite upload)."""

    code = "unsafe_content"


class ImportFailedError(DatasetError):
    """Import thất bại vì lý do khác (quá thời gian, lỗi cú pháp...)."""

    code = "import_failed"


class DatasetNotFoundError(DatasetError):
    code = "dataset_not_found"


class InvalidRelationError(DatasetError):
    """Quan hệ giữa bảng khai báo thủ công không hợp lệ (bảng/cột không tồn tại...)."""

    code = "invalid_relation"


class RelationNotFoundError(DatasetError):
    code = "relation_not_found"
