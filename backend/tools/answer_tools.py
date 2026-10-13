"""
backend/tools/answer_tools.py
-----------------------------
Tool/hàm thuần cho bước trả lời text (không gọi LLM):

  * `template_answer`     — câu trả lời xác định, chỉ dùng giá trị có trong kết quả (fallback khi không có LLM,
                            LLM lỗi, hoặc câu LLM không bám số liệu);
  * `answer_is_grounded`  — kiểm tra mọi con số trong câu trả lời của LLM đều có trong kết quả đã gửi / câu hỏi.
"""
from __future__ import annotations

import math
import numbers
import re
import unicodedata
from typing import List, Optional, Set

import numpy as np
import pandas as pd

from backend.tools.base import Tool

# Sau khi tách dấu (NFD): chữ đ, dấu thanh (huyền/sắc/ngã/hỏi/nặng), dấu mũ/trăng/móc — không có trong tiếng Anh.
_VI_MARKS = re.compile("[đĐ\u0300\u0301\u0303\u0309\u0323\u0302\u0306\u031b]")
_MAX_INLINE_COLS = 6


def is_vietnamese(text: str) -> bool:
    return bool(_VI_MARKS.search(unicodedata.normalize("NFD", text or "")))


def format_value(v) -> str:
    """Giá trị ô -> chuỗi hiển thị trong câu trả lời mẫu."""
    if v is None or v is pd.NaT:
        return "NULL"
    try:
        if pd.isna(v):
            return "NULL"
    except (TypeError, ValueError):
        pass
    if isinstance(v, (bool, np.bool_)):
        return str(bool(v))
    if isinstance(v, numbers.Integral):
        return f"{int(v):,}"
    if isinstance(v, numbers.Real):
        f = float(v)
        return f"{int(f):,}" if f.is_integer() else f"{f:,.2f}".rstrip("0").rstrip(".")
    return str(v)


def template_answer(question: str, df: Optional[pd.DataFrame], truncated: bool = False) -> str:
    vi = is_vietnamese(question)
    if df is None or df.empty:
        return (
            "Truy vấn chạy thành công nhưng không có dòng dữ liệu nào khớp với câu hỏi."
            if vi
            else "The query ran successfully but no rows matched your question."
        )

    n_rows, n_cols = df.shape
    cols = list(df.columns)
    first = df.iloc[0]

    def pairs(row, limit=_MAX_INLINE_COLS) -> str:
        shown = [f"{c} = {format_value(row[c])}" for c in cols[:limit]]
        return "; ".join(shown) + ("; ..." if len(cols) > limit else "")

    if n_rows == 1 and n_cols == 1:
        text = (
            f"Kết quả: {cols[0]} = {format_value(first[cols[0]])}."
            if vi
            else f"The result is {cols[0]} = {format_value(first[cols[0]])}."
        )
    elif n_rows == 1:
        text = f"Kết quả: {pairs(first)}." if vi else f"The result is: {pairs(first)}."
    else:
        text = (
            f"Truy vấn trả về {n_rows:,} dòng × {n_cols} cột (xem bảng bên dưới). Dòng đầu tiên: {pairs(first)}."
            if vi
            else f"The query returned {n_rows:,} rows × {n_cols} columns (see the table below). "
            f"First row: {pairs(first)}."
        )
    if truncated:
        text += (
            " Lưu ý: kết quả đã bị cắt bớt theo giới hạn của hệ thống nên chưa đầy đủ."
            if vi
            else " Note: the result was cut off by a system limit, so it is incomplete."
        )
    return text


# ── Kiểm tra bám số liệu ───────────────────────────────────────────────────
_NUMBER = re.compile(r"(?<![\w.,])[-+]?\d[\d.,]*\d|(?<![\w.,])[-+]?\d")
_LIST_MARKER = re.compile(r"(?m)^\s*\d+[.)]\s+")


def _candidates(token: str) -> List[tuple[float, int]]:
    """Token số -> các cách đọc có thể (giá trị, số chữ số thập phân), vì ',' và '.' mơ hồ giữa các locale."""
    t = token.lstrip("+")
    out: List[tuple[float, int]] = []
    has_dot, has_comma = "." in t, "," in t
    try:
        if has_dot and has_comma:
            dec = "." if t.rfind(".") > t.rfind(",") else ","
            thou = "," if dec == "." else "."
            whole, _, frac = t.replace(thou, "").partition(dec)
            out.append((float(f"{whole}.{frac}" if frac else whole), len(frac)))
        elif has_dot or has_comma:
            sep = "." if has_dot else ","
            parts = t.split(sep)
            if len(parts) > 2:  # nhiều dấu phân cách -> hàng nghìn
                out.append((float("".join(parts)), 0))
            else:
                head, frac = parts
                out.append((float(f"{head}.{frac}"), len(frac)))  # thập phân
                if len(frac) == 3 and head.lstrip("-").isdigit() and 1 <= len(head.lstrip("-")) <= 3:
                    out.append((float(head + frac), 0))  # hoặc hàng nghìn
        else:
            out.append((float(t), 0))
    except ValueError:
        pass
    return out


def numbers_in(text: str) -> List[List[tuple[float, int]]]:
    text = _LIST_MARKER.sub("", text)
    return [c for c in (_candidates(m.group(0)) for m in _NUMBER.finditer(text)) if c]


def _allowed_numbers(question: str, df: pd.DataFrame, max_rows: int) -> Set[float]:
    allowed: Set[float] = {float(len(df)), float(df.shape[1]), float(min(len(df), max_rows))}
    for cands in numbers_in(question):
        allowed.update(v for v, _ in cands)
    sample = df.head(max_rows)
    for col in sample.columns:
        for v in sample[col].tolist():
            if isinstance(v, bool) or v is None:
                continue
            if isinstance(v, (int, float)):
                if not (isinstance(v, float) and math.isnan(v)):
                    allowed.add(float(v))
            else:
                for cands in numbers_in(str(v)):  # chuỗi/ngày: '2024-03' -> 2024, 3
                    allowed.update(val for val, _ in cands)
                    allowed.add(abs(cands[0][0]))
    return allowed


def answer_is_grounded(answer: str, question: str, df: pd.DataFrame, max_rows: int) -> bool:
    """Mọi số trong `answer` phải có trong câu hỏi hoặc trong `max_rows` dòng đầu của kết quả (sai số làm tròn)."""
    allowed = _allowed_numbers(question, df, max_rows)
    for cands in numbers_in(answer):
        ok = False
        for value, decimals in cands:
            tol = 0.5 * 10 ** (-decimals) + 1e-9
            if any(abs(value - a) <= tol for a in allowed) or any(abs(abs(value) - a) <= tol for a in allowed):
                ok = True
                break
        if not ok:
            return False
    return True


class TemplateAnswerTool(Tool):
    name = "template_answer"
    description = "Sinh câu trả lời text xác định từ kết quả truy vấn (không gọi LLM); chỉ dùng giá trị có trong bảng."

    def run(self, question: str, df: Optional[pd.DataFrame], truncated: bool = False) -> str:
        return template_answer(question, df, truncated)


__all__ = [
    "TemplateAnswerTool",
    "template_answer",
    "answer_is_grounded",
    "is_vietnamese",
    "format_value",
    "numbers_in",
]
