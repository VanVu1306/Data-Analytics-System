"""
backend/tools/answer_templates.py
---------------------------------
Câu trả lời mẫu xác định (không gọi LLM) cho MỌI loại bằng chứng của Answer Agent (Giai đoạn 7):
query-only, EDA-only và query + EDA, tiếng Việt/tiếng Anh theo ngôn ngữ câu hỏi. Mọi con số lấy trực tiếp từ
bằng chứng nên bám đúng kết quả. Đây là phương án dự phòng khi LLM không dùng được / trả lời rỗng / không bám số liệu.

  * `template_answer`     — hợp nhất các phần theo thứ tự: query rồi từng operation EDA theo kế hoạch;
  * `describe_payload`    — mô tả một operation EDA (cũng là `eda[].text` trong API);
  * `missing_caveats`     — các lưu ý (cắt bớt, mẫu, operation lỗi...) mà câu của LLM bỏ sót -> được nối thêm;
  * `AnswerTemplateTool`  — tool duy nhất đăng ký trong registry cho các việc trên (thay `template_answer` + `eda_answer`).
"""
from __future__ import annotations

from typing import List, Optional

import pandas as pd

from backend.tools.answer_evidence import (
    QUERY_CLARIFICATION,
    QUERY_ERROR,
    AnswerEvidence,
    QueryEvidence,
    format_value,
)
from backend.tools.base import Tool
from backend.tools.eda_tools import EdaPayload
from backend.tools.text_utils import is_vietnamese

_MAX_INLINE_COLS = 6


# ── Phần query ─────────────────────────────────────────────────────────────
def _truncation_note(vi: bool) -> str:
    return (
        "Lưu ý: kết quả đã bị cắt bớt theo giới hạn của hệ thống nên chưa đầy đủ."
        if vi
        else "Note: the result was cut off by a system limit, so it is incomplete."
    )


def _query_text(q: QueryEvidence, vi: bool) -> str:
    if q.status == QUERY_ERROR:
        return ("Phần truy vấn không thực hiện được (xem chi tiết ở phần Bằng chứng)." if vi
                else "The query part could not be completed (see the Evidence section for details).")
    if q.status == QUERY_CLARIFICATION:
        return ("Phần truy vấn cần làm rõ thêm câu hỏi nên chưa có kết quả." if vi
                else "The query part needs clarification, so there is no result yet.")
    df: Optional[pd.DataFrame] = q.df
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
    if q.truncated:
        text += " " + _truncation_note(vi)
    return text


# ── Phần EDA ───────────────────────────────────────────────────────────────
def _n(v) -> str:
    return format_value(v)


def _f(v) -> str:
    return "—" if v is None else format_value(v)


def describe_payload(p: EdaPayload, vi: bool) -> str:
    """Mô tả xác định một operation EDA; operation lỗi / không áp dụng được cũng được nêu rõ."""
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


# ── Hợp nhất ───────────────────────────────────────────────────────────────
def template_answer(question: str, evidence: AnswerEvidence) -> str:
    """Câu trả lời mẫu cho mọi tổ hợp bằng chứng; mỗi phần cách nhau một dòng trống (query trước, EDA theo kế hoạch)."""
    vi = is_vietnamese(question)
    parts: List[str] = []
    if evidence.query is not None:
        parts.append(_query_text(evidence.query, vi))
    parts.extend(t for t in (describe_payload(p, vi) for p in evidence.eda) if t)
    return "\n\n".join(parts)


# ── Lưu ý bị bỏ sót trong câu trả lời của LLM ──────────────────────────────
_TRUNC_WORDS = ("cắt", "truncat", "cut off", "incomplete", "chưa đầy đủ", "mẫu", "sample")
_PARTIAL_WORDS = _TRUNC_WORDS + ("ước tính", "estimat", "approx", "giới hạn", "limit", "partial", "một phần")
_FAIL_WORDS = (
    "không thực hiện", "không áp dụng", "không thể", "không tính", "không chạy", "lỗi",
    "could not", "couldn't", "cannot", "can't", "unable", "failed", "not applicable", "error", "not possible",
)


def _mentions(text: str, words: tuple) -> bool:
    t = text.lower()
    return any(w in t for w in words)


def missing_caveats(text: str, evidence: AnswerEvidence, vi: bool) -> List[str]:
    """Các câu lưu ý mà `text` (câu của LLM) chưa nhắc tới: kết quả bị cắt, số liệu ước tính trên mẫu,
    operation EDA lỗi/không áp dụng được, phần query không thực hiện được. Câu lưu ý là văn bản xác định."""
    notes: List[str] = []
    q = evidence.query
    if q is not None and q.has_data and q.truncated and not _mentions(text, _TRUNC_WORDS):
        notes.append(_truncation_note(vi))
    partial = [p for p in evidence.ok_eda if p.sampled or p.truncated or not p.exact]
    if partial and not _mentions(text, _PARTIAL_WORDS):
        notes.append(
            "Lưu ý: một số số liệu phân tích là ước tính trên mẫu hoặc bị giới hạn theo cấu hình nên chưa đầy đủ (xem cảnh báo)."
            if vi else
            "Note: some analysis figures are estimated from a sample or limited by configured caps, so they are incomplete (see warnings)."
        )
    failed = [p for p in evidence.eda if p.status != "ok"]
    if failed and not _mentions(text, _FAIL_WORDS):
        notes.extend(describe_payload(p, vi) for p in failed)
    if q is not None and q.status in (QUERY_ERROR, QUERY_CLARIFICATION) and not _mentions(text, _FAIL_WORDS + ("truy vấn", "query")):
        notes.append(_query_text(q, vi))
    return notes


class AnswerTemplateTool(Tool):
    name = "answer_template"
    description = (
        "Sinh câu trả lời text xác định (không gọi LLM) từ bằng chứng query và/hoặc EDA; "
        "chỉ dùng giá trị có trong bằng chứng. Dùng làm fallback của Answer Agent và cho `eda[].text`."
    )

    def run(self, question: str, evidence: AnswerEvidence) -> str:
        return template_answer(question, evidence)
