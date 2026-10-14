"""
backend/tools/eda_tools.py
--------------------------
Các tool EDA (giai đoạn 4): xác định, có giới hạn, chỉ đọc. KHÔNG có đường nào để LLM sinh/chạy code phân tích.

  eda_table_overview       số dòng/cột, kiểu dữ liệu (khai báo / quan sát / lúc import), số giá trị phân biệt
  eda_data_quality         NULL, ô chuỗi rỗng, dòng trùng lặp
  eda_numeric_summary      count, mean, std, min, q1, median, q3, max cho cột số
  eda_categorical_summary  số nhóm + top giá trị phổ biến cho cột phân loại
  eda_correlation          ma trận tương quan Pearson/Spearman giữa các cột số

Nguyên tắc tính toán:
  * count/mean/min/max/std/COUNT DISTINCT/GROUP BY chạy bằng SQL (chính xác, trên toàn bảng);
  * quartile và tương quan cần pandas: dữ liệu đưa sang pandas bị giới hạn `eda_sample_rows`; vượt thì lấy mẫu
    có hệ thống (mỗi k dòng) và payload ghi rõ `sampled=True`, `exact=False`;
  * giới hạn số cột (`eda_max_columns`), số nhóm (`eda_top_k`), số cột tương quan, và ngân sách thời gian cho
    mỗi operation; phần bị cắt được ghi vào `warnings`;
  * mọi định danh được kiểm tra với schema thật rồi mới trích dẫn vào SQL; SQL đã chạy được trả về trong `queries`.
"""
from __future__ import annotations

import math
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from backend.config import Settings
from backend.database import DatasetStore, quote_ident
from backend.tools.base import Tool

OPERATIONS = (
    "table_overview",
    "data_quality",
    "numeric_summary",
    "categorical_summary",
    "correlation",
)
CORRELATION_METHODS = ("pearson", "spearman")

_NUM = "typeof({c}) IN ('integer','real')"
_CHUNK_COLUMNS = 25
_MAX_RECORDED_QUERIES = 30
_MAX_QUERY_CHARS = 1500
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?)?$")
_NOISY_NOTES = ("ô trống", "ISO 8601", "đúng/sai")


# ── Kết quả ────────────────────────────────────────────────────────────────
@dataclass
class EdaPayload:
    """Kết quả có cấu trúc của một operation EDA."""

    operation: str
    table: str
    columns: List[str] = field(default_factory=list)   # cột mục tiêu đã xác thực
    status: str = "ok"                                  # ok | not_applicable | error
    summary: Dict[str, Any] = field(default_factory=dict)
    data: Optional[pd.DataFrame] = None                 # bảng hiển thị
    warnings: List[str] = field(default_factory=list)
    definitions: List[str] = field(default_factory=list)  # cách tính (duplicate, quartile, NULL...)
    exact: bool = True                                  # False nếu có lấy mẫu / cắt cột / hết thời gian
    sampled: bool = False
    sample_info: Optional[Dict[str, Any]] = None
    truncated: bool = False
    queries: List[str] = field(default_factory=list)
    elapsed_ms: int = 0
    error: Optional[str] = None
    text: Optional[str] = None                          # câu trả lời text (điền bởi eda_answer)

    def warn(self, message: str, *, inexact: bool = False, truncated: bool = False) -> None:
        if message not in self.warnings:
            self.warnings.append(message)
        if inexact or truncated:
            self.exact = False
        if truncated:
            self.truncated = True


class EdaTargetError(Exception):
    """Bảng/cột/tham số không hợp lệ."""


class EdaQueryError(Exception):
    """Một câu SQL của EDA lỗi hoặc quá thời gian (kết quả có thể còn dùng được từng phần)."""


class EdaBudgetError(EdaQueryError):
    """Hết ngân sách thời gian của operation."""


def _r(x: Any, digits: int = 6) -> Optional[float]:
    if x is None:
        return None
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    return round(f, digits) if math.isfinite(f) else None


def _count(x: Any) -> int:
    return 0 if x is None or (isinstance(x, float) and math.isnan(x)) else int(x)


class _Run:
    """Ngữ cảnh một lần chạy: repository chỉ-đọc, ghi lại SQL, ngân sách thời gian."""

    def __init__(self, repo, settings: Settings):
        self.repo = repo
        self.settings = settings
        self.queries: List[str] = []
        self.deadline = time.monotonic() + settings.eda_timeout_seconds
        self.schema = repo.get_table_columns()

    def sql(self, sql: str, max_rows: int = 100_000) -> pd.DataFrame:
        if time.monotonic() > self.deadline:
            raise EdaBudgetError("Hết ngân sách thời gian của operation EDA.")
        if len(self.queries) < _MAX_RECORDED_QUERIES:
            text = " ".join(sql.split())
            self.queries.append(text if len(text) <= _MAX_QUERY_CHARS else text[:_MAX_QUERY_CHARS] + " …")
        df, err = self.repo.fetch(sql, max_rows=max_rows)
        if err:
            raise EdaQueryError(err)
        return df

    def decls(self, table: str, columns: Optional[List[str]]) -> List[Dict[str, Any]]:
        if table not in self.schema:
            raise EdaTargetError(f"Bảng '{table}' không tồn tại trong dataset.")
        all_cols = self.schema[table]
        if not columns:
            return list(all_cols)
        by_name = {c["name"]: c for c in all_cols}
        missing = [c for c in columns if c not in by_name]
        if missing:
            raise EdaTargetError(f"Cột không tồn tại trong bảng '{table}': {', '.join(missing)}.")
        seen: List[str] = []
        for c in columns:
            if c not in seen:
                seen.append(c)
        return [by_name[c] for c in seen]


# ── Profile cột (một lần quét / nhóm cột) ──────────────────────────────────
def _profile(run: _Run, table: str, cols: List[str], payload: EdaPayload, *, distinct: bool) -> Dict[str, Dict[str, Any]]:
    """{cột: {non_null, nulls, distinct, numeric_n, text_n, blob_n, blank_n, kind}} + payload.summary['_n_rows']."""
    out: Dict[str, Dict[str, Any]] = {}
    n_rows: Optional[int] = None
    for start in range(0, len(cols), _CHUNK_COLUMNS):
        chunk = cols[start:start + _CHUNK_COLUMNS]
        parts = ["COUNT(*) AS n"]
        for j, c in enumerate(chunk):
            q = quote_ident(c)
            parts.append(f"COUNT({q}) AS nn{j}")
            if distinct:
                parts.append(f"COUNT(DISTINCT {q}) AS d{j}")
            parts.append(f"SUM({_NUM.format(c=q)}) AS num{j}")
            parts.append(f"SUM(typeof({q}) = 'text') AS txt{j}")
            parts.append(f"SUM(typeof({q}) = 'blob') AS blb{j}")
            parts.append(f"SUM(typeof({q}) = 'text' AND TRIM({q}) = '') AS bl{j}")
        try:
            row = run.sql(f"SELECT {', '.join(parts)} FROM {quote_ident(table)}").iloc[0]
        except EdaQueryError as e:
            payload.warn(f"Không thống kê được {len(chunk)} cột ({e}).", inexact=True, truncated=True)
            continue
        n_rows = _count(row["n"])
        for j, c in enumerate(chunk):
            nn, num, txt, blb = (_count(row[f"{k}{j}"]) for k in ("nn", "num", "txt", "blb"))
            if nn == 0:
                kind = "empty"
            elif num == nn:
                kind = "numeric"
            elif txt == nn:
                kind = "text"
            elif blb == nn:
                kind = "blob"
            else:
                kind = "mixed"
            out[c] = {
                "non_null": nn,
                "nulls": n_rows - nn,
                "distinct": _count(row[f"d{j}"]) if distinct else None,
                "numeric_n": num,
                "text_n": txt,
                "blob_n": blb,
                "blank_n": _count(row[f"bl{j}"]),
                "kind": kind,
            }
    if n_rows is None:
        try:
            n_rows = _count(run.sql(f"SELECT COUNT(*) AS n FROM {quote_ident(table)}").iloc[0, 0])
        except EdaQueryError as e:
            raise EdaQueryError(f"Không đếm được số dòng ({e}).")
    payload.summary["n_rows"] = n_rows
    return out


def _stride(n: int, limit: int) -> int:
    return max(1, math.ceil(n / limit)) if limit > 0 else 1


def _cap(items: List[Any], limit: int, payload: EdaPayload, what: str) -> List[Any]:
    if len(items) > limit:
        payload.warn(f"Chỉ xử lý {limit}/{len(items)} {what} đầu tiên theo giới hạn hệ thống.", truncated=True)
        return items[:limit]
    return items


# ── Tool cơ sở ─────────────────────────────────────────────────────────────
class EdaTool(Tool):
    operation = ""

    def __init__(self, store: DatasetStore, settings: Settings):
        self._store = store
        self._settings = settings

    @property
    def name(self) -> str:  # type: ignore[override]
        return f"eda_{self.operation}"

    def run(  # type: ignore[override]
        self,
        *,
        table: str,
        columns: Optional[List[str]] = None,
        dataset_id: Optional[str] = None,
        **params: Any,
    ) -> EdaPayload:
        started = time.perf_counter()
        repo = self._store.repository(dataset_id)  # DatasetNotFoundError được để nổi lên (không fallback)
        payload = EdaPayload(operation=self.operation, table=table, columns=list(columns or []))
        run = _Run(repo, self._settings)
        try:
            params["_explicit_columns"] = bool(columns)
            decls = run.decls(table, columns)
            payload.columns = [d["name"] for d in decls]
            meta = self._store.column_meta(dataset_id, table)
            self._compute(run, payload, decls, meta, params)
        except EdaTargetError as e:
            payload.status, payload.error = "error", str(e)
        except EdaQueryError as e:
            payload.status, payload.error = "error", f"Không chạy được {self.operation}: {e}"
        payload.queries = run.queries
        payload.elapsed_ms = int((time.perf_counter() - started) * 1000)
        return payload

    def _compute(self, run: _Run, payload: EdaPayload, decls, meta, params) -> None:  # pragma: no cover
        raise NotImplementedError


# ── 1. Tổng quan bảng ──────────────────────────────────────────────────────
class TableOverviewTool(EdaTool):
    operation = "table_overview"
    description = "Tổng quan bảng: số dòng/cột, kiểu dữ liệu (khai báo, quan sát, lúc import), NULL và số giá trị phân biệt."

    def _compute(self, run, payload, decls, meta, params):
        names = [d["name"] for d in decls]
        shown = _cap(names, self._settings.eda_max_columns, payload, "cột (số giá trị phân biệt)")
        prof = _profile(run, payload.table, shown, payload, distinct=True)
        declared = {d["name"]: d["type"] for d in decls}

        rows, kinds, mixed = [], {}, []
        for c in names:
            p = prof.get(c)
            kind = p["kind"] if p else None
            if p and kind == "text" and p["non_null"]:
                try:
                    sample = run.sql(
                        f"SELECT DISTINCT {quote_ident(c)} AS v FROM {quote_ident(payload.table)} "
                        f"WHERE {quote_ident(c)} IS NOT NULL LIMIT 200"
                    )["v"].astype(str)
                    if len(sample) and sample.map(lambda v: bool(_ISO_DATE.match(v))).all():
                        kind = "date-like text"
                except EdaQueryError:
                    pass
            if kind:
                kinds[kind] = kinds.get(kind, 0) + 1
            m = meta.get(c, {})
            rows.append({
                "column": c,
                "declared_type": declared[c],
                "observed": kind,
                "import_source_type": m.get("source_type"),
                "import_inferred_type": m.get("inferred_type"),
                "import_notes": " ".join(n for n in m.get("notes", []) if not any(x in n for x in _NOISY_NOTES)) or None,
                "non_null": p["non_null"] if p else None,
                "nulls": p["nulls"] if p else None,
                "distinct": p["distinct"] if p else None,
            })
            if p and p["kind"] == "mixed":
                mixed.append(c)
        if mixed:
            payload.warn(f"{len(mixed)} cột có kiểu hỗn hợp số/chữ ({', '.join(mixed[:5])}{'…' if len(mixed) > 5 else ''}); "
                         "nên thận trọng khi tính toán.")
        n_rows = payload.summary.get("n_rows", 0)
        if n_rows == 0:
            payload.warn("Bảng không có dòng dữ liệu nào.")
        payload.summary.update(n_columns=len(names), kind_counts=kinds, profiled_columns=len(shown))
        payload.data = pd.DataFrame(rows)
        payload.definitions = [
            "declared_type: kiểu khai báo trong SQLite; observed: kiểu thực tế của giá trị (typeof) trong cột.",
            "import_*: kiểu ở tệp nguồn / kiểu suy luận / ghi chú chuẩn hóa lúc import; dữ liệu đã được chuẩn hóa về SQLite.",
            "nulls: số ô NULL; distinct: số giá trị khác nhau (không tính NULL).",
        ]


# ── 2. Chất lượng dữ liệu ──────────────────────────────────────────────────
class DataQualityTool(EdaTool):
    operation = "data_quality"
    description = "Chất lượng dữ liệu: NULL theo cột, ô chuỗi rỗng và dòng trùng lặp."

    def _compute(self, run, payload, decls, meta, params):
        names = [d["name"] for d in decls]
        explicit = bool(params.get("_explicit_columns"))
        shown = _cap(names, self._settings.eda_max_columns, payload, "cột")
        prof = _profile(run, payload.table, shown, payload, distinct=False)
        n_rows = payload.summary["n_rows"]

        rows = []
        for c in shown:
            p = prof.get(c)
            if not p:
                continue
            rows.append({
                "column": c,
                "non_null": p["non_null"],
                "null_count": p["nulls"],
                "null_pct": _r(100 * p["nulls"] / n_rows, 2) if n_rows else None,
                "blank_count": p["blank_n"],
                "observed": p["kind"],
            })
        table = quote_ident(payload.table)
        basis = "mọi cột" if not explicit else ", ".join(names)
        select = "*" if not explicit else ", ".join(quote_ident(c) for c in names)
        dup = distinct_rows = None
        if n_rows:
            try:
                distinct_rows = _count(run.sql(f"SELECT COUNT(*) AS n FROM (SELECT DISTINCT {select} FROM {table})").iloc[0, 0])
                dup = n_rows - distinct_rows
            except EdaQueryError as e:
                payload.warn(f"Không kiểm tra được dòng trùng lặp ({e}).", inexact=True)
        else:
            dup = distinct_rows = 0
        with_nulls = [r["column"] for r in rows if r["null_count"]]
        payload.summary.update(
            n_columns=len(names),
            duplicate_rows=dup,
            distinct_rows=distinct_rows,
            duplicate_basis=basis,
            columns_with_nulls=with_nulls,
            total_null_cells=sum(r["null_count"] for r in rows),
            columns_with_blanks=[r["column"] for r in rows if r["blank_count"]],
        )
        if n_rows == 0:
            payload.warn("Bảng không có dòng dữ liệu nào.")
        payload.data = pd.DataFrame(rows)
        payload.definitions = [
            "Giá trị thiếu = NULL. Ô chuỗi rỗng/chỉ có khoảng trắng được đếm riêng (blank_count), không tính vào NULL.",
            f"Dòng trùng lặp = số dòng thừa so với bản đầu tiên của dòng giống hệt nhau trên {basis} "
            "(NULL được coi là bằng nhau). Cột khóa duy nhất làm dòng không thể trùng; hãy chỉ định cột nếu cần.",
        ]


# ── 3. Cột số ──────────────────────────────────────────────────────────────
def _numeric_candidates(prof, names: List[str], explicit: bool, payload: EdaPayload) -> List[str]:
    out, skipped, mixed = [], [], []
    for c in names:
        p = prof.get(c)
        if not p:
            continue
        if p["numeric_n"] == 0:
            if explicit:
                skipped.append(c)
            continue
        if p["kind"] == "mixed":
            mixed.append(c)
        elif not explicit and p["kind"] != "numeric":
            continue
        out.append(c)
    if skipped:
        payload.warn(f"{len(skipped)} cột không có giá trị số nên bị bỏ qua: {', '.join(skipped[:5])}.")
    if mixed:
        payload.warn(f"{len(mixed)} cột có kiểu hỗn hợp ({', '.join(mixed[:5])}{'…' if len(mixed) > 5 else ''}): "
                     "chỉ các giá trị số được dùng, phần còn lại bị bỏ qua.")
    return out


class NumericSummaryTool(EdaTool):
    operation = "numeric_summary"
    description = "Thống kê mô tả cột số: count, mean, std, min, q1, median, q3, max."

    def _compute(self, run, payload, decls, meta, params):
        explicit = bool(params.get("_explicit_columns"))
        names = _cap([d["name"] for d in decls], self._settings.eda_max_columns, payload, "cột")
        prof = _profile(run, payload.table, names, payload, distinct=False)
        n_rows = payload.summary["n_rows"]
        cols = _numeric_candidates(prof, names, explicit, payload)
        if not cols:
            payload.status = "not_applicable"
            payload.error = "Không có cột số nào để thống kê."
            return
        table = quote_ident(payload.table)
        stats: Dict[str, Dict[str, Any]] = {}
        for start in range(0, len(cols), _CHUNK_COLUMNS):
            chunk = cols[start:start + _CHUNK_COLUMNS]
            parts = []
            for j, c in enumerate(chunk):
                q = quote_ident(c)
                v = f"CASE WHEN {_NUM.format(c=q)} THEN {q} END"
                parts += [f"COUNT({v}) AS c{j}", f"AVG({v}) AS a{j}", f"MIN({v}) AS lo{j}", f"MAX({v}) AS hi{j}"]
            try:
                row = run.sql(f"SELECT {', '.join(parts)} FROM {table}").iloc[0]
            except EdaQueryError as e:
                payload.warn(f"Không thống kê được {len(chunk)} cột ({e}).", inexact=True, truncated=True)
                continue
            std_parts, with_std = [], []
            for j, c in enumerate(chunk):
                n, mean = _count(row[f"c{j}"]), row[f"a{j}"]
                stats[c] = {"count": n, "mean": _r(mean), "min": _r(row[f"lo{j}"]), "max": _r(row[f"hi{j}"]), "std": None}
                if n >= 2 and mean is not None and math.isfinite(float(mean)):
                    q = quote_ident(c)
                    v = f"CASE WHEN {_NUM.format(c=q)} THEN {q} END"
                    std_parts.append(f"SUM(({v} - {float(mean)!r}) * ({v} - {float(mean)!r})) AS s{j}")
                    with_std.append((j, c, n))
            if std_parts:
                try:
                    srow = run.sql(f"SELECT {', '.join(std_parts)} FROM {table}").iloc[0]
                    for j, c, n in with_std:
                        ss = srow[f"s{j}"]
                        stats[c]["std"] = _r(math.sqrt(float(ss) / (n - 1))) if ss is not None else None
                except EdaQueryError as e:
                    payload.warn(f"Không tính được độ lệch chuẩn ({e}).", inexact=True)

        limit = self._settings.eda_sample_rows
        sampled_cols: Dict[str, Dict[str, int]] = {}
        for c in cols:
            st = stats.get(c)
            if st is None:
                continue
            st.update(q1=None, median=None, q3=None, quantile_basis="exact")
            n = st["count"]
            if n == 0:
                continue
            q = quote_ident(c)
            k = _stride(n, limit)
            try:
                if k == 1:
                    sql = f"SELECT {q} AS v FROM {table} WHERE {_NUM.format(c=q)}"
                else:
                    sql = (
                        f"SELECT v FROM (SELECT {q} AS v, ROW_NUMBER() OVER () AS rn FROM {table} "
                        f"WHERE {_NUM.format(c=q)}) WHERE (rn - 1) % {k} = 0"
                    )
                values = run.sql(sql, max_rows=limit + 1)["v"].to_numpy(dtype=float)
            except EdaQueryError as e:
                payload.warn(f"Không tính được quartile của '{c}' ({e}).", inexact=True)
                st["quantile_basis"] = "không tính được"
                continue
            values = values[np.isfinite(values)]
            if len(values):
                st["q1"], st["median"], st["q3"] = (_r(x) for x in np.quantile(values, [0.25, 0.5, 0.75]))
            if k > 1:
                st["quantile_basis"] = f"mẫu {len(values):,}/{n:,}"
                sampled_cols[c] = {"rows_total": n, "rows_used": int(len(values))}
        if sampled_cols:
            payload.sampled = True
            payload.exact = False
            payload.sample_info = {"method": "lấy mẫu có hệ thống (mỗi k dòng)", "columns": sampled_cols}
            payload.warn("Quartile/median của một số cột được ước tính trên mẫu; count/mean/std/min/max vẫn chính xác.")

        records = []
        for c in cols:
            if c in stats:
                records.append({
                    "column": c,
                    "missing": prof[c]["nulls"],
                    "non_numeric": prof[c]["non_null"] - prof[c]["numeric_n"],
                    **stats[c],
                })
        order = ["column", "count", "missing", "non_numeric", "mean", "std", "min", "q1", "median", "q3", "max", "quantile_basis"]
        payload.columns = [r["column"] for r in records]
        payload.summary.update(stats=[{k: r.get(k) for k in order} for r in records])
        payload.data = pd.DataFrame(records)[order]
        payload.definitions = [
            "count = số giá trị số (không tính NULL và giá trị không phải số); missing = số NULL.",
            "std là độ lệch chuẩn mẫu (chia n-1), tính chính xác bằng SQL; quartile dùng nội suy tuyến tính.",
        ]


# ── 4. Cột phân loại ───────────────────────────────────────────────────────
class CategoricalSummaryTool(EdaTool):
    operation = "categorical_summary"
    description = "Cột phân loại: số nhóm khác nhau và top giá trị phổ biến (có giới hạn)."

    def _compute(self, run, payload, decls, meta, params):
        explicit = bool(params.get("_explicit_columns"))
        top_k = max(1, min(int(params.get("top_k") or self._settings.eda_top_k), 50))
        names = [d["name"] for d in decls]
        if not explicit:
            names = _cap(names, self._settings.eda_max_columns, payload, "cột")
        prof = _profile(run, payload.table, names, payload, distinct=True)
        cols = [c for c in names if prof.get(c) and prof[c]["non_null"] and (explicit or prof[c]["kind"] in ("text", "mixed", "blob"))]
        if not explicit:
            cols = _cap(cols, self._settings.eda_max_columns, payload, "cột phân loại")
        if not cols:
            payload.status = "not_applicable"
            payload.error = "Không có cột phân loại (văn bản) nào có dữ liệu."
            return
        table = quote_ident(payload.table)
        rows, per_col = [], {}
        for c in cols:
            p, q = prof[c], quote_ident(c)
            try:
                top = run.sql(
                    f"SELECT {q} AS value, COUNT(*) AS n FROM {table} WHERE {q} IS NOT NULL "
                    f"GROUP BY {q} ORDER BY n DESC, value ASC LIMIT {top_k}"
                )
            except EdaQueryError as e:
                payload.warn(f"Không lấy được top giá trị của '{c}' ({e}).", inexact=True)
                continue
            nn = p["non_null"]
            shown = 0
            for rank, (v, n) in enumerate(zip(top["value"], top["n"]), 1):
                rows.append({"column": c, "rank": rank, "value": str(v), "count": int(n), "pct": _r(100 * int(n) / nn, 2)})
                shown += int(n)
            per_col[c] = {"non_null": nn, "distinct": p["distinct"], "nulls": p["nulls"],
                          "shown_values": len(top), "shown_pct": _r(100 * shown / nn, 2)}
            if p["distinct"] > top_k:
                was_exact = payload.exact  # cắt top-k không làm các con số kém chính xác
                payload.warn(f"Cột '{c}' có {p['distinct']:,} giá trị khác nhau; chỉ hiển thị top {top_k}.", truncated=True)
                payload.exact = was_exact
            if p["distinct"] == nn and nn > top_k:
                payload.warn(f"Cột '{c}': hầu như mọi giá trị đều khác nhau (có thể là mã định danh/văn bản tự do).")
        payload.columns = list(per_col)
        payload.summary.update(columns=per_col, top_k=top_k)
        payload.data = pd.DataFrame(rows, columns=["column", "rank", "value", "count", "pct"])
        payload.definitions = [
            f"Top {top_k} giá trị theo số lần xuất hiện (đồng hạng xếp theo giá trị tăng dần); pct = % trên số ô không NULL.",
        ]


# ── 5. Tương quan ──────────────────────────────────────────────────────────
class CorrelationTool(EdaTool):
    operation = "correlation"
    description = "Ma trận tương quan Pearson/Spearman giữa các cột số (pairwise, bỏ NULL)."

    def _compute(self, run, payload, decls, meta, params):
        explicit = bool(params.get("_explicit_columns"))
        method = str(params.get("method") or "pearson").lower()
        if method not in CORRELATION_METHODS:
            raise EdaTargetError(f"Phương pháp tương quan không hợp lệ: {method}. Hỗ trợ: {', '.join(CORRELATION_METHODS)}.")
        cap = self._settings.eda_corr_max_columns
        names = _cap([d["name"] for d in decls], self._settings.eda_max_columns, payload, "cột")
        prof = _profile(run, payload.table, names, payload, distinct=False)
        cols = _numeric_candidates(prof, names, explicit, payload)
        if len(cols) < 2:
            payload.status = "not_applicable"
            payload.error = "Cần ít nhất 2 cột số để tính tương quan."
            return
        if len(cols) > cap:
            if explicit:
                payload.status = "not_applicable"
                payload.error = f"Chọn {len(cols)} cột; tối đa {cap} cột cho ma trận tương quan. Hãy chỉ định ít cột hơn."
                return
            payload.warn(f"Có {len(cols)} cột số; chỉ tính {cap} cột đầu tiên.", truncated=True)
            cols = cols[:cap]

        n_rows = payload.summary["n_rows"]
        limit = self._settings.eda_sample_rows
        k = _stride(n_rows, limit)
        table = quote_ident(payload.table)
        select = ", ".join(
            f"CASE WHEN {_NUM.format(c=quote_ident(c))} THEN {quote_ident(c)} END AS c{j}" for j, c in enumerate(cols)
        )
        if k == 1:
            sql = f"SELECT {select} FROM {table}"
        else:
            sql = (
                f"SELECT {', '.join(f'c{j}' for j in range(len(cols)))} FROM "
                f"(SELECT {select}, ROW_NUMBER() OVER () AS rn FROM {table}) WHERE (rn - 1) % {k} = 0"
            )
        df = run.sql(sql, max_rows=limit + 1)
        df.columns = cols
        df = df.apply(pd.to_numeric, errors="coerce").replace([np.inf, -np.inf], np.nan)
        if k > 1:
            payload.sampled = payload.exact = False
            payload.sample_info = {"method": "lấy mẫu có hệ thống (mỗi k dòng)", "rows_total": n_rows, "rows_used": int(len(df))}
            payload.warn(f"Tương quan được ước tính trên mẫu {len(df):,}/{n_rows:,} dòng.")
            payload.sampled = True

        usable = [c for c in cols if df[c].notna().sum() >= 3 and df[c].nunique(dropna=True) > 1]
        for c in cols:
            if c not in usable:
                payload.warn(f"Cột '{c}' bị loại: hằng số hoặc có dưới 3 giá trị số.")
        if len(usable) < 2:
            payload.status = "not_applicable"
            payload.error = "Không đủ cột số có biến thiên để tính tương quan."
            return
        df = df[usable]
        corr = df.corr(method=method, min_periods=3)
        mask = df.notna().astype(int)
        pairs_n = mask.T.dot(mask)
        pairs = []
        for i, a in enumerate(usable):
            for b in usable[i + 1:]:
                r = corr.loc[a, b]
                if pd.notna(r):
                    pairs.append({"a": a, "b": b, "r": _r(r, 4), "n": int(pairs_n.loc[a, b])})
        pairs.sort(key=lambda x: -abs(x["r"]))
        matrix = corr.round(4).reset_index().rename(columns={"index": "column"})
        payload.columns = usable
        payload.summary.update(method=method, n_columns=len(usable), top_pairs=pairs[:5], n_pairs=len(pairs))
        payload.data = matrix
        label = "Pearson (tuyến tính)" if method == "pearson" else "Spearman (hạng)"
        payload.definitions = [
            f"Hệ số {label}, tính theo từng cặp cột trên các dòng có đủ giá trị số ở cả hai cột (pairwise deletion); "
            "NULL và giá trị không phải số bị bỏ qua, cần ≥ 3 cặp.",
            "Tương quan không hàm ý quan hệ nhân quả.",
        ]


EDA_TOOL_CLASSES = (
    TableOverviewTool,
    DataQualityTool,
    NumericSummaryTool,
    CategoricalSummaryTool,
    CorrelationTool,
)

__all__ = [
    "OPERATIONS",
    "CORRELATION_METHODS",
    "EdaPayload",
    "EdaTool",
    "EDA_TOOL_CLASSES",
    "TableOverviewTool",
    "DataQualityTool",
    "NumericSummaryTool",
    "CategoricalSummaryTool",
    "CorrelationTool",
]
