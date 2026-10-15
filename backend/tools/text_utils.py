"""
backend/tools/text_utils.py
---------------------------
Hàm thuần (không side-effect) để làm sạch / kiểm tra output của LLM.
Logic giữ nguyên như bản gốc (dail_sql._extract_sql, text_to_python._clean_code/_is_valid_code).
"""
from __future__ import annotations

import ast
import re
import unicodedata

# Sau khi tách dấu (NFD): chữ đ, dấu thanh (huyền/sắc/ngã/hỏi/nặng), dấu mũ/trăng/móc — không có trong tiếng Anh.
_VI_MARKS = re.compile("[đĐ\u0300\u0301\u0303\u0309\u0323\u0302\u0306\u031b]")
# Một số từ khóa tiếng Việt không dấu thường gặp trong câu hỏi về dữ liệu.
_ASCII_VI = re.compile(
    r"\b(tong quan|gia tri|thieu|trung lap|tuong quan|thong ke|bao nhieu|cot|bang|du lieu|phan phoi|kiem tra|"
    r"chat luong|khuyet|cho toi|hay)\b"
)


def is_vietnamese(text: str) -> bool:
    """Câu có phải tiếng Việt không (có dấu, hoặc không dấu nhưng chứa từ khóa thường gặp).

    Đây là helper ngôn ngữ DUY NHẤT của hệ thống: Planner, câu trả lời mẫu (query/EDA) và Answer Agent đều dùng.
    """
    if _VI_MARKS.search(unicodedata.normalize("NFD", text or "")):
        return True
    ascii_text = unicodedata.normalize("NFD", text or "").encode("ascii", "ignore").decode().lower()
    return bool(_ASCII_VI.search(ascii_text))


def extract_sql(raw: str) -> str:
    """Lấy SQL sạch từ response LLM (xử lý nhiều format)."""
    raw = re.sub(r"```sql\s*", "", raw, flags=re.IGNORECASE)
    raw = re.sub(r"```", "", raw)
    raw = raw.strip()

    m = re.search(r"```sql\s*(.*?)\s*```", raw, re.IGNORECASE | re.DOTALL)
    if m:
        raw = m.group(1).strip()

    if not raw.upper().startswith(("SELECT", "WITH", "--")):
        raw = "SELECT " + raw

    raw = raw.rstrip(";").strip()
    return raw


def clean_code(raw: str) -> str:
    """Làm sạch code từ LLM: bỏ markdown fences, ANSI escape codes, ký tự control."""
    m = re.search(r"```(?:python|py)?\s*(.*?)\s*```", raw, re.IGNORECASE | re.DOTALL)
    if m:
        raw = m.group(1).strip()
    else:
        raw = re.sub(r"```python\s*", "", raw, flags=re.IGNORECASE)
        raw = re.sub(r"```", "", raw)

    raw = re.sub(r"\x1b\[[0-9;]*m", "", raw)
    raw = "".join(c for c in raw if ord(c) >= 32 or c in "\n\r\t")
    return raw.strip()


def is_valid_code(code: str) -> bool:
    """Quick check: code có syntax hợp lệ và có ít nhất 1 lời gọi plt/sns?"""
    if not code or len(code) < 20:
        return False
    if "plt." not in code and "sns." not in code:
        return False
    try:
        ast.parse(code)
        return True
    except SyntaxError:
        return False
