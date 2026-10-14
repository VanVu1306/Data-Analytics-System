"""
backend/tools/planner.py
------------------------
Kế hoạch xử lý có cấu trúc (giai đoạn 4): hàm thuần, không gọi LLM.

  * `draft_plan`      định tuyến bằng quy tắc cho ý định rõ ràng: query / EDA / kết hợp (query + EDA);
  * `validate_plan`   kiểm tra mọi kế hoạch (từ quy tắc HOẶC từ LLM) với schema thật + allowlist operation/tham số;
                      kế hoạch sai/mơ hồ -> `PlanError` mang câu hỏi làm rõ cho người dùng;
  * `ProcessingPlan`  danh sách bước đã xác thực + yêu cầu biểu đồ + yêu cầu hiển thị SQL.

Quy tắc chỉ nhận một tập từ khóa EDA tường minh (tổng quan, giá trị thiếu, trùng lặp, thống kê mô tả,
phân phối, tương quan...). Câu hỏi không chứa từ khóa EDA luôn là `query` (không gọi LLM planner);
câu hỏi vừa có từ khóa EDA vừa có phần truy vấn (theo/by, tổng, top...) là "kết hợp" -> `needs_llm`.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from backend.tools.eda_tools import CORRELATION_METHODS, OPERATIONS as EDA_OPERATIONS
from backend.tools.intent import ChartPlan

OP_QUERY = "query"
ALL_OPS = (OP_QUERY,) + EDA_OPERATIONS
MAX_STEPS = 6
MAX_COLUMNS_PER_STEP = 100
_ALL_TABLES_OPS = ("table_overview", "data_quality")  # không nêu bảng -> áp dụng cho các bảng của dataset
_SCHEMA = Dict[str, List[Dict[str, Any]]]             # {bảng: [{name, type, pk}]}


# ── Kiểu dữ liệu ───────────────────────────────────────────────────────────
@dataclass(frozen=True)
class PlanStep:
    op: str
    table: Optional[str] = None
    columns: Tuple[str, ...] = ()
    params: Dict[str, Any] = field(default_factory=dict)
    question: Optional[str] = None            # chỉ cho `query`: câu hỏi con (mặc định: câu hỏi gốc)

    def to_dict(self) -> Dict[str, Any]:
        return {"operation": self.op, "table": self.table, "columns": list(self.columns), "params": dict(self.params)}


@dataclass
class ProcessingPlan:
    steps: List[PlanStep]
    chart: ChartPlan
    show_sql: bool
    source: str = "rules"                      # rules | llm | rules_fallback

    @property
    def has_query(self) -> bool:
        return any(s.op == OP_QUERY for s in self.steps)

    @property
    def eda_steps(self) -> List[PlanStep]:
        return [s for s in self.steps if s.op != OP_QUERY]


@dataclass
class PlanOutcome:
    plan: Optional[ProcessingPlan] = None
    clarification: Optional[str] = None


@dataclass
class PlanDraft:
    """Bản nháp từ quy tắc (chưa xác thực)."""

    steps: List[Dict[str, Any]]
    needs_llm: bool = False
    clarification: Optional[str] = None
    vi: bool = True


class PlanError(Exception):
    """Kế hoạch không hợp lệ hoặc mơ hồ; `str(exc)` là câu hỏi làm rõ gửi cho người dùng."""


# ── Chuẩn hóa văn bản ──────────────────────────────────────────────────────
def norm(text: str) -> str:
    """Không dấu, chữ thường, '_' -> khoảng trắng (khớp tên bảng/cột và từ khóa tiếng Việt không dấu)."""
    t = unicodedata.normalize("NFD", text or "")
    t = "".join(ch for ch in t if unicodedata.category(ch) != "Mn").replace("đ", "d").replace("Đ", "d")
    return re.sub(r"[_\s]+", " ", t.lower()).strip()


_OVERVIEW = re.compile(
    r"\b(eda|tong quan|overview|profil\w*|kham pha|explor\w+|cau truc (?:du lieu|bang)|data structure|schema|"
    r"bao nhieu cot|how many columns|kich thuoc (?:du lieu|bang)|shape|kieu du lieu|data types?|column types?)\b"
)
_QUALITY = re.compile(
    r"\b(chat luong du lieu|data quality|missing (?:values?|data)|gia tri (?:thieu|khuyet|null|rong|trong)|"
    r"du lieu (?:thieu|khuyet)|thieu du lieu|ty le thieu|bao nhieu (?:gia tri )?(?:thieu|null|khuyet)|trung lap|"
    r"trung nhau|duplicat\w*|dong trung|kiem tra du lieu|data check|null values?|cot nao (?:co )?(?:thieu|null))\b"
)
_NUMERIC = re.compile(
    r"\b(thong ke mo ta|descriptive stat\w*|summary stat\w*|describe|do lech chuan|standard deviation|std|"
    r"tu phan vi|quartiles?|phan vi|percentiles?|median|trung vi)\b"
)
_DISTRIBUTION = re.compile(r"\b(phan phoi|distribution)\b")
_CATEGORICAL = re.compile(
    r"\b(top gia tri|gia tri (?:pho bien|xuat hien nhieu|duy nhat|khac nhau)|tan suat|value counts?|frequenc\w+|"
    r"bao nhieu nhom|so nhom|cac nhom|distinct values?|unique values?|most (?:common|frequent)|phan bo (?:cac )?nhom)\b"
)
_CORRELATION = re.compile(r"\b(tuong quan|correlat\w*|pearson|spearman)\b")
_QUERYISH = re.compile(
    r"\b(theo|by|per|moi|each|tung|group|top|nhieu nhat|it nhat|lon nhat|nho nhat|tong|sum|total|trung binh|"
    r"average|avg|dem|count|bao nhieu|how many|liet ke|list|loc|filter|where|so sanh|compare|doanh thu|revenue|"
    r"tang truong|growth|thang|month|nam|year|quy|quarter)\b"
)
_TRIGGERS = (_OVERVIEW, _QUALITY, _NUMERIC, _DISTRIBUTION, _CATEGORICAL, _CORRELATION)
_NUMERIC_TYPE_HINTS = ("int", "real", "floa", "doub", "num", "dec")


def _is_numeric_decl(declared: str) -> bool:
    d = (declared or "").lower()
    return any(k in d for k in _NUMERIC_TYPE_HINTS) and "char" not in d


# ── Nhận biết bảng/cột được nhắc tới ───────────────────────────────────────
def _mentions(question_norm: str, schema: _SCHEMA) -> Tuple[List[str], Dict[str, List[str]]]:
    def found(name: str) -> bool:
        n = norm(name)
        return len(n) >= 2 and re.search(rf"(?<![a-z0-9]){re.escape(n)}(?![a-z0-9])", question_norm) is not None

    tables = [t for t in schema if found(t)]
    cols: Dict[str, List[str]] = {}
    for t, decls in schema.items():
        hit = [d["name"] for d in decls if found(d["name"])]
        if hit:
            cols[t] = hit
    return tables, cols


def _table_list(schema: _SCHEMA) -> str:
    return ", ".join(list(schema)[:10]) + ("…" if len(schema) > 10 else "")


def _ask_table(schema: _SCHEMA, vi: bool) -> str:
    return (f"Bạn muốn phân tích bảng nào? Dataset có các bảng: {_table_list(schema)}." if vi
            else f"Which table should be analysed? The dataset has: {_table_list(schema)}.")


# ── Định tuyến bằng quy tắc ────────────────────────────────────────────────
def draft_plan(question: str, schema: _SCHEMA, vi: bool = True) -> PlanDraft:
    q = norm(question)
    if not schema:
        return PlanDraft([{"op": OP_QUERY}], vi=vi)

    spans: List[Tuple[int, int]] = []
    hits: Dict[str, bool] = {}
    for key, pat in (("overview", _OVERVIEW), ("quality", _QUALITY), ("numeric", _NUMERIC),
                     ("distribution", _DISTRIBUTION), ("categorical", _CATEGORICAL), ("correlation", _CORRELATION)):
        found = False
        for m in pat.finditer(q):
            spans.append(m.span())
            found = True
        hits[key] = found
    if not any(hits.values()):
        return PlanDraft([{"op": OP_QUERY}], vi=vi)  # không có từ khóa EDA -> query, không cần LLM planner

    mt, mc = _mentions(q, schema)
    residual = q
    # bỏ từ khóa EDA và tên bảng/cột được nhắc (ví dụ cột "revenue") trước khi tìm phần truy vấn
    names = {norm(n) for n in list(mt) + [c for cols in mc.values() for c in cols]}
    for n in names:
        for m in re.finditer(rf"(?<![a-z0-9]){re.escape(n)}(?![a-z0-9])", q):
            spans.append(m.span())
    covered = [False] * len(q)
    for a, b in spans:
        for i in range(a, b):
            covered[i] = True
    residual = "".join(" " if covered[i] else ch for i, ch in enumerate(q))
    has_query = bool(_QUERYISH.search(residual))
    steps: List[Dict[str, Any]] = [{"op": OP_QUERY}] if has_query else []

    def numeric_cols(t: str) -> List[str]:
        by = {d["name"]: d["type"] for d in schema[t]}
        return [c for c in mc.get(t, []) if _is_numeric_decl(by[c])]

    def other_cols(t: str) -> List[str]:
        by = {d["name"]: d["type"] for d in schema[t]}
        return [c for c in mc.get(t, []) if not _is_numeric_decl(by[c])]

    def targets(all_tables_ok: bool) -> List[str]:
        if mt:
            return mt
        if mc:
            return list(mc)
        if len(schema) == 1 or all_tables_ok:
            return list(schema)
        raise PlanError(_ask_table(schema, vi))

    try:
        if hits["overview"]:
            for t in targets(True):
                steps.append({"op": "table_overview", "table": t, "columns": mc.get(t, [])})
        if hits["quality"]:
            for t in targets(True):
                steps.append({"op": "data_quality", "table": t, "columns": mc.get(t, [])})
        if hits["numeric"]:
            for t in targets(False):
                steps.append({"op": "numeric_summary", "table": t, "columns": mc.get(t, [])})
        if hits["distribution"]:
            for t in targets(False):
                if not mc.get(t):  # không nêu cột: cả cột số lẫn cột phân loại
                    steps += [{"op": "numeric_summary", "table": t}, {"op": "categorical_summary", "table": t}]
                else:
                    if numeric_cols(t):
                        steps.append({"op": "numeric_summary", "table": t, "columns": numeric_cols(t)})
                    if other_cols(t):
                        steps.append({"op": "categorical_summary", "table": t, "columns": other_cols(t)})
        if hits["categorical"]:
            for t in targets(False):
                steps.append({"op": "categorical_summary", "table": t, "columns": other_cols(t) or mc.get(t, [])})
        if hits["correlation"]:
            for t in targets(False):
                cols = numeric_cols(t)
                steps.append({"op": "correlation", "table": t, "columns": cols,
                              "params": {"method": "spearman"} if "spearman" in q else {}})
    except PlanError as e:
        return PlanDraft([], clarification=str(e), vi=vi)
    return PlanDraft(steps, needs_llm=has_query, vi=vi)


# ── Xác thực ───────────────────────────────────────────────────────────────
def _canon(name: Any, options: Sequence[str]) -> Optional[str]:
    if not isinstance(name, str):
        return None
    if name in options:
        return name
    ci = [o for o in options if o.casefold() == name.strip().casefold()]
    return ci[0] if len(ci) == 1 else None


def validate_plan(
    raw_steps: Any, schema: _SCHEMA, *, vi: bool = True, max_tables: int = 10, max_steps: int = MAX_STEPS
) -> List[PlanStep]:
    """Kiểm tra danh sách bước (dict) với allowlist operation, schema và tham số. Sai/mơ hồ -> PlanError."""
    def err(vi_msg: str, en_msg: str) -> PlanError:
        return PlanError(vi_msg if vi else en_msg)

    if not isinstance(raw_steps, list) or not raw_steps:
        raise err("Mình chưa xác định được cần làm gì với câu hỏi này. Bạn có thể nói rõ hơn không?",
                  "I could not work out what to do with this question. Could you be more specific?")
    out: List[PlanStep] = []
    for raw in raw_steps:
        if not isinstance(raw, dict):
            raise err("Kế hoạch xử lý không hợp lệ.", "The processing plan is invalid.")
        op = raw.get("op", raw.get("operation"))
        if op not in ALL_OPS:
            raise err(f"Thao tác không được hỗ trợ: {op!r}.", f"Unsupported operation: {op!r}.")

        if op == OP_QUERY:
            question = raw.get("question")
            if question is not None and (not isinstance(question, str) or len(question) > 1000):
                raise err("Câu hỏi con của bước truy vấn không hợp lệ.", "The query step's sub-question is invalid.")
            out.append(PlanStep(OP_QUERY, question=(question or "").strip() or None))
            continue

        columns = raw.get("columns") or []
        if not isinstance(columns, list) or len(columns) > MAX_COLUMNS_PER_STEP or not all(isinstance(c, str) for c in columns):
            raise err("Danh sách cột không hợp lệ.", "The column list is invalid.")
        table = raw.get("table")
        if table in (None, ""):
            if len(schema) == 1:
                tables = list(schema)
            elif op in _ALL_TABLES_OPS and not columns:
                tables = list(schema)[:max_tables]
            else:
                owners = [t for t, decls in schema.items() if columns and all(c in {d["name"] for d in decls} for c in columns)]
                if len(owners) != 1:
                    raise PlanError(_ask_table(schema, vi))
                tables = owners
        else:
            canon = _canon(table, list(schema))
            if canon is None:
                raise err(f"Không có bảng '{table}'. Dataset có: {_table_list(schema)}.",
                          f"There is no table '{table}'. The dataset has: {_table_list(schema)}.")
            tables = [canon]

        params_in = raw.get("params") or {}
        if not isinstance(params_in, dict):
            raise err("Tham số không hợp lệ.", "Invalid parameters.")
        params: Dict[str, Any] = {}
        if op == "categorical_summary" and "top_k" in params_in:
            k = params_in["top_k"]
            if isinstance(k, bool) or not isinstance(k, int) or not 1 <= k <= 50:
                raise err("top_k phải là số nguyên từ 1 đến 50.", "top_k must be an integer between 1 and 50.")
            params["top_k"] = k
        if op == "correlation" and "method" in params_in:
            m = str(params_in["method"]).lower()
            if m not in CORRELATION_METHODS:
                raise err(f"Phương pháp tương quan chưa hỗ trợ: {m}.", f"Unsupported correlation method: {m}.")
            params["method"] = m

        for t in tables:
            names = [d["name"] for d in schema[t]]
            canon_cols: List[str] = []
            for c in columns:
                cc = _canon(c, names)
                if cc is None:
                    raise err(f"Bảng '{t}' không có cột '{c}'.", f"Table '{t}' has no column '{c}'.")
                if cc not in canon_cols:
                    canon_cols.append(cc)
            out.append(PlanStep(op, t, tuple(canon_cols), dict(params)))

    queries = [s for s in out if s.op == OP_QUERY]
    if len(queries) > 1:
        raise err("Chỉ hỗ trợ một truy vấn mỗi lượt; hãy tách câu hỏi.", "Only one query per turn is supported; please split the question.")
    # bỏ bước trùng lặp, giữ thứ tự
    unique: List[PlanStep] = []
    for s in out:
        if s not in unique:
            unique.append(s)
    if len(unique) > max_steps:
        raise err(f"Yêu cầu có quá nhiều bước (tối đa {max_steps}). Hãy thu hẹp câu hỏi.",
                  f"Too many steps (max {max_steps}). Please narrow the question.")
    return unique
