"""
backend/tools/answer_evidence.py
--------------------------------
Bằng chứng có cấu trúc cho bước trả lời text (Giai đoạn 7) — hàm/dữ liệu thuần, không gọi LLM:

  * `QueryEvidence` / `AnswerEvidence` — hợp đồng đầu vào của Answer Agent: kết quả query và/hoặc các `EdaPayload`
    theo thứ tự kế hoạch. Nhánh nào không chạy thì để trống (None / danh sách rỗng), không tạo dữ liệu giả;
  * `build_evidence_context`          — dựng ngữ cảnh gửi LLM (đã giới hạn) và ĐỒNG THỜI tập số được phép xuất hiện
    trong câu trả lời, nên "LLM thấy gì" và "câu trả lời được dùng số nào" luôn khớp nhau;
  * `answer_is_grounded`              — mọi con số trong câu trả lời của LLM phải có trong ngữ cảnh đã gửi / câu hỏi;
  * `format_value`, `numbers_in`      — định dạng giá trị ô và đọc số (',' và '.' mơ hồ giữa các locale).
"""
from __future__ import annotations

import json
import math
import numbers
import re
from dataclasses import dataclass, field
from typing import Any, List, Optional, Set, Union

import numpy as np
import pandas as pd

from backend.tools.eda_tools import EdaPayload

QUERY_OK = "ok"
QUERY_EMPTY = "empty"                       # truy vấn chạy được nhưng 0 dòng
QUERY_ERROR = "error"                       # SQL không sinh được / chạy lỗi / bị từ chối
QUERY_CLARIFICATION = "needs_clarification"  # câu hỏi cần làm rõ trước khi truy vấn

_MAX_CELL_CHARS = 80
_MAX_TABLE_COLS = 12
_SUMMARY_CHARS = 1200       # số liệu tóm tắt tối đa gửi cho LLM cho mỗi operation EDA
_MAX_NOTES = 5              # cảnh báo / định nghĩa tối đa gửi cho mỗi operation
_MAX_NOTE_CHARS = 200


# ── Hợp đồng bằng chứng ────────────────────────────────────────────────────
@dataclass
class QueryEvidence:
    """Kết quả nhánh query (đã giới hạn bởi tầng thực thi)."""

    df: Optional[pd.DataFrame] = None
    truncated: bool = False
    status: str = QUERY_OK

    @property
    def has_data(self) -> bool:
        return self.status == QUERY_OK and self.df is not None and not self.df.empty


@dataclass
class AnswerEvidence:
    """Bằng chứng cho một câu trả lời: query-only, EDA-only hoặc query + EDA."""

    query: Optional[QueryEvidence] = None
    eda: List[EdaPayload] = field(default_factory=list)

    @classmethod
    def from_query(cls, df: Optional[pd.DataFrame], truncated: bool = False) -> "AnswerEvidence":
        status = QUERY_OK if df is not None and not df.empty else QUERY_EMPTY
        return cls(query=QueryEvidence(df=df if status == QUERY_OK else None, truncated=truncated, status=status))

    @property
    def is_empty(self) -> bool:
        """Không nhánh nào chạy: không có gì để trả lời."""
        return self.query is None and not self.eda

    @property
    def ok_eda(self) -> List[EdaPayload]:
        return [p for p in self.eda if p.status == "ok"]

    @property
    def interpretable(self) -> bool:
        """Có số liệu thật để diễn giải (query có dòng, hoặc ít nhất một operation EDA thành công)."""
        return (self.query is not None and self.query.has_data) or bool(self.ok_eda)

    @property
    def kind(self) -> str:
        parts = (["query"] if self.query is not None else []) + (["eda"] if self.eda else [])
        return "+".join(parts) or "none"


# ── Định dạng & đọc số ─────────────────────────────────────────────────────
def format_value(v: Any) -> str:
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


# ── Ngữ cảnh gửi LLM + tập số được phép ────────────────────────────────────
@dataclass
class EvidenceContext:
    text: str                       # phần "Evidence" của prompt (không gồm câu hỏi)
    allowed: Set[float]             # mọi số có trong `text`
    query_rows_shown: int = 0


class _Builder:
    def __init__(self) -> None:
        self.lines: List[str] = []
        self.allowed: Set[float] = set()

    def text(self, s: str) -> None:
        """Dòng văn bản (đầu mục, JSON số liệu...): mọi số trong dòng đều được phép."""
        self.lines.append(s)
        for cands in numbers_in(s.replace(",", " ")):  # ',' ở đây là dấu phân tách JSON/danh sách, không phải thập phân
            for v, _ in cands:
                self.allowed.add(v)
                self.allowed.add(abs(v))

    def table(self, df: pd.DataFrame, max_rows: int, max_cols: Optional[int] = None) -> int:
        """CSV của `max_rows` dòng đầu; số trong từng ô được phép (chuỗi/ngày: '2024-03' -> 2024, 3)."""
        sample = df.head(max_rows)
        if max_cols is not None:
            sample = sample.iloc[:, :max_cols]
        sample = sample.copy()
        for col in sample.columns:
            if sample[col].dtype == object or str(sample[col].dtype).startswith("str"):
                sample[col] = sample[col].map(
                    lambda v: v if not isinstance(v, str) or len(v) <= _MAX_CELL_CHARS else v[:_MAX_CELL_CHARS] + "…"
                )
            for v in sample[col].tolist():
                self._allow_cell(v)
        self.lines.append(sample.to_csv(index=False).rstrip("\n"))
        return len(sample)

    def _allow_cell(self, v: Any) -> None:
        if isinstance(v, bool) or v is None:
            return
        if isinstance(v, (int, float, np.integer, np.floating)):
            if not (isinstance(v, float) and math.isnan(v)):
                self.allowed.add(float(v))
        else:
            for cands in numbers_in(str(v)):
                self.allowed.update(val for val, _ in cands)
                self.allowed.add(abs(cands[0][0]))


def _cut(s: str, limit: int) -> str:
    """Cắt ở ranh giới phần tử (dấu ',') để không cụt giữa một số."""
    if len(s) <= limit:
        return s
    head = s[:limit]
    return head[: head.rfind(",")] + ",…(cut)" if "," in head else "…(cut)"


def _summary_text(summary: dict) -> str:
    pieces: List[str] = []
    used = 0
    for k, v in summary.items():
        piece = f'"{k}":' + _cut(json.dumps(v, default=str, ensure_ascii=False, separators=(",", ":")), _SUMMARY_CHARS)
        if pieces and used + len(piece) > _SUMMARY_CHARS:
            pieces.append('"…":"more key figures omitted"')
            break
        pieces.append(piece)
        used += len(piece)
    return "{" + ",".join(pieces) + "}"


def _short(s: str) -> str:
    s = " ".join(str(s).split())
    return s if len(s) <= _MAX_NOTE_CHARS else s[:_MAX_NOTE_CHARS] + "…"


def _query_block(b: _Builder, q: QueryEvidence, max_rows: int) -> int:
    if q.status == QUERY_ERROR:
        b.text("[QUERY RESULT] Not available: the query step could not be completed, so there is no query data.")
        return 0
    if q.status == QUERY_CLARIFICATION:
        b.text("[QUERY RESULT] Not available: the question needs clarification before it can be queried, "
               "so there is no query data.")
        return 0
    if not q.has_data:
        b.text("[QUERY RESULT] The query ran successfully and returned 0 rows.")
        return 0
    df = q.df
    shown = min(len(df), max_rows)
    note = f"Rows in result: {len(df)}; columns: {df.shape[1]}; rows shown below: {shown}."
    if q.truncated:
        note += " The result was cut off by a system row limit, so it is NOT the complete answer set."
    b.text(f"[QUERY RESULT] {note}")
    return b.table(df, max_rows)


def _eda_block(b: _Builder, label: str, p: EdaPayload, max_rows: int) -> None:
    head = f"[EDA RESULT {label}] operation={p.operation}; table={p.table}"
    if p.columns:
        head += f"; columns={','.join(p.columns[:_MAX_TABLE_COLS])}" + ("…" if len(p.columns) > _MAX_TABLE_COLS else "")
    b.text(head)
    if p.status != "ok":
        kind = "not applicable" if p.status == "not_applicable" else "FAILED"
        b.text(f"status={p.status} ({kind}); reason: {_short(p.error or 'unknown')}")
        return
    flags = [f"status=ok", f"exact={str(p.exact).lower()}"]
    if p.sampled:
        flags.append("sampled=true (figures are ESTIMATES from a sample)")
    if p.truncated:
        flags.append("truncated=true (limited by configured caps)")
    b.text("; ".join(flags))
    if p.sample_info:
        b.text("sample_info=" + _cut(json.dumps(p.sample_info, default=str, ensure_ascii=False, separators=(",", ":")), 400))
    if p.summary:
        b.text("key_figures=" + _summary_text(p.summary))
    if p.data is not None and not p.data.empty:
        shown = min(len(p.data), max_rows)
        b.text(f"result_table (rows shown below: {shown} of {len(p.data)}):")
        b.table(p.data, max_rows, max_cols=_MAX_TABLE_COLS)
    for w in p.warnings[:_MAX_NOTES]:
        b.text("warning: " + _short(w))
    for d in p.definitions[:_MAX_NOTES]:
        b.text("definition: " + _short(d))


def build_evidence_context(evidence: AnswerEvidence, max_rows: int) -> EvidenceContext:
    """Ngữ cảnh gửi LLM từ bằng chứng (query và/hoặc EDA) + tập số được phép."""
    b = _Builder()
    shown = 0
    if evidence.query is not None:
        shown = _query_block(b, evidence.query, max_rows)
    for i, p in enumerate(evidence.eda):
        _eda_block(b, "E" + chr(ord("A") + i), p, max_rows)  # nhãn chữ (EA, EB...) để không thêm số vào tập "được phép"
    return EvidenceContext(text="\n".join(b.lines), allowed=b.allowed, query_rows_shown=shown)


def answer_is_grounded(
    answer: str, question: str, evidence: Union[AnswerEvidence, EvidenceContext], max_rows: int = 20
) -> bool:
    """Mọi số trong `answer` phải có trong câu hỏi hoặc trong ngữ cảnh bằng chứng đã gửi (sai số làm tròn)."""
    ctx = evidence if isinstance(evidence, EvidenceContext) else build_evidence_context(evidence, max_rows)
    allowed = set(ctx.allowed)
    for cands in numbers_in(question):
        allowed.update(v for v, _ in cands)
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
