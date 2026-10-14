"""
backend/tools/eda_text.py
-------------------------
Câu trả lời text ngắn cho kết quả EDA (giai đoạn 4). Xác định, không gọi LLM: mọi con số đều lấy trực tiếp từ
payload nên bám đúng kết quả. Hỗ trợ tiếng Việt/tiếng Anh theo ngôn ngữ câu hỏi.
"""
from __future__ import annotations

import re
import unicodedata
from typing import List

from backend.tools.answer_tools import format_value, is_vietnamese
from backend.tools.base import Tool
from backend.tools.eda_tools import EdaPayload

_ASCII_VI = re.compile(
    r"\b(tong quan|gia tri|thieu|trung lap|tuong quan|thong ke|bao nhieu|cot|bang|du lieu|phan phoi|kiem tra|"
    r"chat luong|khuyet|cho toi|hay)\b"
)


def is_vi_question(question: str) -> bool:
    """Tiếng Việt có dấu, hoặc tiếng Việt không dấu (nhận biết qua vài từ khóa)."""
    if is_vietnamese(question):
        return True
    ascii_text = unicodedata.normalize("NFD", question or "").encode("ascii", "ignore").decode().lower()
    return bool(_ASCII_VI.search(ascii_text))


def _n(v) -> str:
    return format_value(v)


def _f(v) -> str:
    return "—" if v is None else format_value(v)


def summarize_payload(p: EdaPayload, vi: bool) -> str:
    if p.status == "error":
        return (f"Không thực hiện được phân tích bảng {p.table}: {p.error}" if vi
                else f"Could not analyse table {p.table}: {p.error}")
    if p.status == "not_applicable":
        return (f"Bảng {p.table}: {p.error}" if vi else f"Table {p.table}: {p.error}")
    s, t = p.summary, p.table
    parts: List[str] = []
    if p.operation == "table_overview":
        kinds = ", ".join(f"{k}: {v}" for k, v in s.get("kind_counts", {}).items())
        parts.append(
            f"Bảng {t} có {_n(s['n_rows'])} dòng × {_n(s['n_columns'])} cột" + (f" (kiểu quan sát — {kinds})." if kinds else ".")
            if vi else
            f"Table {t} has {_n(s['n_rows'])} rows × {_n(s['n_columns'])} columns" + (f" (observed types — {kinds})." if kinds else ".")
        )
    elif p.operation == "data_quality":
        nulls = s.get("columns_with_nulls", [])
        total = s.get("total_null_cells", 0)
        dup = s.get("duplicate_rows")
        if vi:
            parts.append(f"Bảng {t} ({_n(s['n_rows'])} dòng): "
                         + (f"{len(nulls)} cột có giá trị thiếu (tổng {_n(total)} ô NULL: {', '.join(nulls[:5])}{'…' if len(nulls) > 5 else ''})"
                            if nulls else "không có giá trị thiếu (NULL)") + ".")
            if dup is None:
                parts.append("Chưa kiểm tra được dòng trùng lặp.")
            else:
                parts.append(f"{_n(dup)} dòng trùng lặp (theo {s['duplicate_basis']}).")
            if s.get("columns_with_blanks"):
                parts.append(f"Có ô chuỗi rỗng ở: {', '.join(s['columns_with_blanks'][:5])}.")
        else:
            parts.append(f"Table {t} ({_n(s['n_rows'])} rows): "
                         + (f"{len(nulls)} columns have missing values ({_n(total)} NULL cells: {', '.join(nulls[:5])}{'…' if len(nulls) > 5 else ''})"
                            if nulls else "no missing (NULL) values") + ".")
            parts.append("Duplicate rows could not be checked." if dup is None else f"{_n(dup)} duplicate rows (by {s['duplicate_basis']}).")
            if s.get("columns_with_blanks"):
                parts.append(f"Blank strings found in: {', '.join(s['columns_with_blanks'][:5])}.")
    elif p.operation == "numeric_summary":
        stats = s.get("stats", [])
        if len(stats) <= 3:
            for r in stats:
                parts.append(
                    (f"Cột {r['column']}: count {_n(r['count'])}, mean {_f(r['mean'])}, std {_f(r['std'])}, min {_f(r['min'])}, "
                     f"median {_f(r['median'])}, max {_f(r['max'])}.") if vi else
                    (f"Column {r['column']}: count {_n(r['count'])}, mean {_f(r['mean'])}, std {_f(r['std'])}, min {_f(r['min'])}, "
                     f"median {_f(r['median'])}, max {_f(r['max'])}.")
                )
        else:
            parts.append(f"Đã thống kê mô tả {len(stats)} cột số của bảng {t} (xem bảng bên dưới)." if vi
                         else f"Descriptive statistics for {len(stats)} numeric columns of {t} (see table below).")
    elif p.operation == "categorical_summary":
        for c, info in list(s.get("columns", {}).items())[:3]:
            rows = p.data[p.data["column"] == c] if p.data is not None else None
            top = rows.iloc[0] if rows is not None and len(rows) else None
            if top is None:
                continue
            parts.append(
                f"Cột {c} có {_n(info['distinct'])} giá trị khác nhau; phổ biến nhất: {top['value']} ({_n(top['count'])} lần, {_f(top['pct'])}%)."
                if vi else
                f"Column {c} has {_n(info['distinct'])} distinct values; most common: {top['value']} ({_n(top['count'])} times, {_f(top['pct'])}%)."
            )
        extra = len(s.get("columns", {})) - 3
        if extra > 0:
            parts.append(f"(Còn {extra} cột khác trong bảng bên dưới.)" if vi else f"({extra} more columns in the table below.)")
    elif p.operation == "correlation":
        method = "Pearson" if s.get("method") == "pearson" else "Spearman"
        pairs = s.get("top_pairs", [])
        if pairs:
            a = pairs[0]
            parts.append(
                f"Tương quan {method} giữa {s['n_columns']} cột số của bảng {t}; cặp mạnh nhất: {a['a']} – {a['b']} (r = {_f(a['r'])}, {_n(a['n'])} cặp giá trị)."
                if vi else
                f"{method} correlation across {s['n_columns']} numeric columns of {t}; strongest pair: {a['a']} – {a['b']} (r = {_f(a['r'])}, {_n(a['n'])} value pairs)."
            )
        else:
            parts.append("Không có cặp cột nào đủ dữ liệu để tính tương quan." if vi else "No column pair has enough data for a correlation.")
    if p.sampled:
        parts.append("(Một phần số liệu được ước tính trên mẫu — xem ghi chú bên dưới bảng.)" if vi
                     else "(Some figures are estimated from a sample — see the notes below the table.)")
    if p.truncated and not p.sampled:
        parts.append("(Một phần kết quả bị giới hạn theo cấu hình — xem cảnh báo.)" if vi else "(Part of the result is limited by configured caps — see warnings.)")
    return " ".join(parts)


class EdaAnswerTool(Tool):
    name = "eda_answer"
    description = "Sinh câu trả lời text xác định từ payload EDA (không gọi LLM)."

    def run(self, payload: EdaPayload, question: str) -> str:
        return summarize_payload(payload, is_vi_question(question))
