"""
backend/tools/planner.py
------------------------
Kế hoạch xử lý có cấu trúc (giai đoạn 4): hàm thuần, không gọi LLM.

  * `draft_plan`      định tuyến bằng quy tắc cho ý định rõ ràng: query / EDA / kết hợp (query + EDA);
  * `validate_plan`   kiểm tra mọi kế hoạch (từ quy tắc HOẶC từ LLM) với schema thật + allowlist operation/tham số;
                      kế hoạch sai/mơ hồ -> `PlanError` mang câu hỏi làm rõ cho người dùng;
  * `ProcessingPlan`  danh sách bước đã xác thực + yêu cầu biểu đồ + yêu cầu hiển thị SQL.

Giai đoạn 6: LLM (PlannerAgent) là cơ chế quyết định chính; các quy tắc dưới đây chỉ còn là phương án dự phòng khi
LLM không dùng được (mock, PLANNER_MODE=rules, lỗi/hết hạn mức). `validate_llm_plan` kiểm tra TOÀN BỘ hợp đồng JSON của
LLM {steps, chart, clarification} — sai ở đâu là hỏi lại người dùng, không chạy một phần kế hoạch.

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
from backend.tools.intent import CHART_GOALS, CHART_TYPES, ChartPlan, canonical_chart_type, resolve_chart_plan

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
    question: Optional[str] = None            # câu hỏi con của bước (với `query`: câu hỏi gửi cho SQL Agent; mặc định câu gốc)

    def to_dict(self) -> Dict[str, Any]:
        return {"operation": self.op, "table": self.table, "columns": list(self.columns), "params": dict(self.params),
                "question": self.question}


@dataclass
class ProcessingPlan:
    steps: List[PlanStep]
    chart: ChartPlan
    show_sql: bool
    source: str = "rules"                      # rules | llm | rules_fallback
    fallback_reason: Optional[str] = None      # vì sao không dùng LLM planner (source != "llm")

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

        question = raw.get("question")
        if question is not None and (not isinstance(question, str) or len(question) > 1000):
            raise err("Câu hỏi con của bước không hợp lệ.", "The step's sub-question is invalid.")
        question = (question or "").strip() or None

        if op == OP_QUERY:
            out.append(PlanStep(OP_QUERY, question=question))
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
            out.append(PlanStep(op, t, tuple(canon_cols), dict(params), question))

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


# ── Hợp đồng kế hoạch của LLM (giai đoạn 6) ────────────────────────────────
def _parse_chart(raw_chart: Any, vi: bool) -> Optional[Tuple[bool, Optional[str], Optional[str]]]:
    """Kiểm tra khối `chart` của LLM -> (requested, type, goal). Thiếu khối -> None. Sai -> PlanError.

    type/goal có thể null (để hệ thống tự chọn khi đã có dữ liệu); type được đưa về tên chuẩn trong allowlist.
    """
    if raw_chart is None:
        return None
    if not isinstance(raw_chart, dict) or not isinstance(raw_chart.get("requested"), bool):
        raise PlanError("Phần biểu đồ của kế hoạch không hợp lệ." if vi else "The chart part of the plan is invalid.")
    raw_type = raw_chart.get("type", raw_chart.get("chart_type"))
    ctype = None
    if raw_type not in (None, ""):
        ctype = canonical_chart_type(raw_type)
        if ctype is None:
            raise PlanError(
                f"Loại biểu đồ không được hỗ trợ: {raw_type!r}. Hỗ trợ: {', '.join(CHART_TYPES)}." if vi
                else f"Unsupported chart type: {raw_type!r}. Supported: {', '.join(CHART_TYPES)}."
            )
    goal = raw_chart.get("goal")
    if goal in ("", None):
        goal = None
    elif not isinstance(goal, str) or goal.strip().lower() not in CHART_GOALS:
        raise PlanError(
            f"Mục tiêu trực quan không hợp lệ: {goal!r}. Hỗ trợ: {', '.join(CHART_GOALS)}." if vi
            else f"Invalid chart goal: {goal!r}. Supported: {', '.join(CHART_GOALS)}."
        )
    else:
        goal = goal.strip().lower()
    if not raw_chart["requested"]:
        return False, None, None  # không vẽ: bỏ qua type/goal thừa
    return True, ctype, goal


def resolve_planned_chart(raw_chart: Any, question: str, chart_hint: str = "detect", *, vi: bool = True) -> ChartPlan:
    """Kết hợp khối `chart` của LLM với tùy chọn phụ `chart_hint` (tùy chọn phụ luôn được ưu tiên):

      none     -> không vẽ, kể cả khi LLM cho là có yêu cầu
      <loại>   -> vẽ đúng loại đó (mục tiêu lấy từ LLM)
      auto     -> vẽ; loại/mục tiêu theo LLM, null = hệ thống tự chọn theo dữ liệu
      detect   -> vẽ khi và chỉ khi LLM xác định người dùng có yêu cầu
    Thiếu hẳn khối `chart` (LLM bỏ sót) -> dùng bộ nhận biết bằng quy tắc như trước. Khối sai -> PlanError.
    """
    parsed = _parse_chart(raw_chart, vi)  # kiểm tra cả khi chart_hint=none: kế hoạch sai thì hỏi lại
    if parsed is None:
        return resolve_chart_plan(question, chart_hint)
    requested, ctype, goal = parsed
    if chart_hint == "none":
        return ChartPlan(False, None, "option")
    if chart_hint in CHART_TYPES:
        return ChartPlan(True, chart_hint, "option", goal)
    if chart_hint == "auto":
        return ChartPlan(True, ctype, "option", goal)
    return ChartPlan(True, ctype, "prompt", goal) if requested else ChartPlan(False)


@dataclass
class LLMPlan:
    steps: List[PlanStep]
    chart: ChartPlan


def validate_llm_plan(
    obj: Any, question: str, schema: _SCHEMA, *, chart_hint: str = "detect", vi: bool = True, max_tables: int = 10,
    max_steps: int = MAX_STEPS,
) -> LLMPlan:
    """Kiểm tra object JSON do LLM trả: steps (op, bảng, cột, tham số, tối đa 1 query), chart (loại, mục tiêu).

    `clarification` do caller xử lý trước. Bất kỳ phần nào sai -> PlanError (câu hỏi làm rõ), không trả kế hoạch một phần.
    """
    if not isinstance(obj, dict):
        raise PlanError("Kế hoạch xử lý không hợp lệ." if vi else "The processing plan is invalid.")
    steps = validate_plan(obj.get("steps"), schema, vi=vi, max_tables=max_tables, max_steps=max_steps)
    chart = resolve_planned_chart(obj.get("chart"), question, chart_hint, vi=vi)
    return LLMPlan(steps, chart)
