"""
backend/tools/text_utils.py
---------------------------
Hàm thuần (không side-effect) để làm sạch / kiểm tra output của LLM.
Logic giữ nguyên như bản gốc (dail_sql._extract_sql, text_to_python._clean_code/_is_valid_code).
"""
from __future__ import annotations

import ast
import re


def extract_sql(raw: str) -> str:
    """Lấy SQL sạch từ response LLM (xử lý nhiều format)."""
    raw = re.sub(r"```sql\s*", "", raw, flags=re.IGNORECASE)
    raw = re.sub(r"```", "", raw)
    raw = raw.strip()

    m = re.search(r"```sql\s*(.*?)\s*```", raw, re.IGNORECASE | re.DOTALL)
    if m:
        raw = m.group(1).strip()

    if not raw.upper().startswith("SELECT"):
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
