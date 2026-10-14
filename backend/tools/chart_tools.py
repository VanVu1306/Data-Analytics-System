"""
backend/tools/chart_tools.py
----------------------------
Chuẩn bị dữ liệu + chọn biểu đồ từ KẾT QUẢ ĐÃ CÓ (bảng truy vấn hoặc payload EDA).

Nguyên tắc: không nạp cả bảng lớn chỉ để vẽ.
- Kết quả truy vấn: dùng trực tiếp (đã bị giới hạn dòng/byte khi thực thi).
- Payload EDA: dùng số liệu đã tính (ma trận tương quan, top giá trị, quartile...).
- Cần thêm dữ liệu (histogram/KDE/violin của cột số): gom nhóm bằng SQL (chính xác) hoặc lấy mẫu có giới hạn.
Mọi cách chuẩn bị đều ghi vào `ChartMeta` (nguồn, số dòng gốc/đã dùng, phương pháp, cảnh báo).
"""
from __future__ import annotations

import math
from typing import List, Optional

import pandas as pd

from backend.config import Settings
from backend.database import DatasetStore, quote_ident
from backend.tools.base import Tool
from backend.tools.chart_select import (
    GOAL_TYPES,
    ChartError,
    ChartLimits,
    ChartMeta,
    ChartSpec,
    build_from_frame,
    column_label,
    finalize,
    hist_bins,
    histogram_payload,
    infer_unit,
    matrix_payload,
)
from backend.tools.eda_tools import EdaPayload
from backend.tools.intent import ChartPlan

_NUM = "typeof({c}) IN ('integer','real')"

# loại biểu đồ -> các operation EDA có thể làm nguồn (theo thứ tự ưu tiên)
_EDA_SOURCES = {
    "heatmap": ("correlation",),
    "count plot": ("categorical_summary",),
    "pie chart": ("categorical_summary",),
    "bar chart": ("categorical_summary", "data_quality"),
    "box plot": ("numeric_summary",),
    "violin plot": ("numeric_summary",),
    "histogram": ("numeric_summary",),
    "kde plot": ("numeric_summary",),
}


class BuildChartSpecTool(Tool):
    name = "build_chart_spec"
    description = "Chọn loại biểu đồ phù hợp (mục tiêu + kiểu dữ liệu) và chuẩn bị dữ liệu vẽ từ kết quả truy vấn/EDA."

    def __init__(self, store: DatasetStore, settings: Settings):
        self._store = store
        self._settings = settings
        self._limits = ChartLimits.from_settings(settings)

    def run(  # type: ignore[override]
        self,
        *,
        plan: ChartPlan,
        df: Optional[pd.DataFrame] = None,
        eda: Optional[List[EdaPayload]] = None,
        dataset_id: Optional[str] = None,
        vi: bool = True,
    ) -> ChartSpec:
        """Trả về ChartSpec hoặc raise ChartError (lý do + gợi ý). Lỗi này không ảnh hưởng câu trả lời/bảng."""
        ok = [p for p in (eda or []) if p.status == "ok"]
        corr = next((p for p in ok if p.operation == "correlation"), None)
        want_corr = plan.chart_type == "heatmap" and corr is not None
        if df is not None and not df.empty and not want_corr:
            return build_from_frame(df, plan.chart_type, plan.goal, self._limits, vi=vi, source="query_result")
        if ok:
            return self._from_eda(plan, ok, dataset_id, vi)
        raise ChartError("Không có dữ liệu để vẽ biểu đồ." if vi else "There is no data to draw.")

    # ── Từ payload EDA ─────────────────────────────────────────────────────
    def _from_eda(self, plan: ChartPlan, payloads: List[EdaPayload], dataset_id: Optional[str], vi: bool) -> ChartSpec:
        ctype = plan.chart_type
        by_op = {p.operation: p for p in payloads}
        if ctype is None:
            order = {
                "distribution": ("numeric_summary", "categorical_summary", "correlation", "data_quality"),
                "relationship": ("correlation", "numeric_summary", "categorical_summary", "data_quality"),
                "comparison": ("data_quality", "categorical_summary", "numeric_summary", "correlation"),
                "composition": ("categorical_summary", "data_quality", "numeric_summary", "correlation"),
            }.get(plan.goal or "", ("correlation", "numeric_summary", "categorical_summary", "data_quality"))
            op = next((o for o in order if o in by_op), None)
            if op is None:
                raise ChartError(
                    "Kết quả này (tổng quan bảng) không có dạng số liệu phù hợp để vẽ biểu đồ." if vi
                    else "This result (table overview) has no data shape suitable for a chart.",
                    "Hãy hỏi thống kê mô tả, tương quan, chất lượng dữ liệu hoặc tần suất cột để vẽ." if vi
                    else "Ask for numeric stats, correlation, data quality or column frequencies to chart.",
                )
            ctype = {"correlation": "heatmap", "categorical_summary": "count plot", "data_quality": "bar chart",
                     "numeric_summary": "histogram" if plan.goal == "distribution" else "box plot"}[op]
            reason = (f"tự chọn theo kết quả EDA '{op}'" if vi else f"auto-selected from the '{op}' EDA result")
        else:
            op = next((o for o in _EDA_SOURCES.get(ctype, ()) if o in by_op), None)
            reason = "theo yêu cầu của người dùng" if vi else "as requested"
            if op is None:
                have = ", ".join(sorted(by_op)) or "—"
                raise ChartError(
                    f"Loại '{ctype}' không phù hợp để vẽ từ kết quả EDA hiện có ({have})." if vi
                    else f"'{ctype}' does not fit the available EDA results ({have}).",
                    "Hãy bỏ chỉ định loại để hệ thống tự chọn, hoặc hỏi dữ liệu phù hợp hơn." if vi
                    else "Drop the chart type to let the system choose, or ask for data that fits.",
                )
        payload = by_op[op]
        build = {
            "correlation": self._corr, "categorical_summary": self._categorical,
            "data_quality": self._quality, "numeric_summary": self._numeric,
        }[op]
        spec = build(payload, ctype, plan, dataset_id, vi)
        spec.meta.requested_type = plan.chart_type
        spec.meta.reason = reason
        spec.meta.goal = plan.goal
        if plan.chart_type and plan.goal and plan.chart_type not in GOAL_TYPES.get(plan.goal, ()):
            spec.meta.warn(
                f"Loại '{plan.chart_type}' ít phù hợp với mục tiêu trong câu hỏi; vẫn vẽ theo yêu cầu." if vi
                else f"'{plan.chart_type}' is a weaker fit for the goal in the question; drawn as requested."
            )
        for w in payload.warnings:
            spec.meta.warn(w)
        spec.meta.title = spec.meta.title or ""
        return spec

    @staticmethod
    def _meta(payload: EdaPayload, method: str) -> ChartMeta:
        meta = ChartMeta(source="eda_payload", method=method)
        info = payload.sample_info or {}
        n = payload.summary.get("n_rows")
        meta.rows_total = info.get("rows_total") or n
        meta.rows_used = info.get("rows_used") or n
        return meta

    def _corr(self, p: EdaPayload, ctype: str, plan: ChartPlan, dataset_id, vi: bool) -> ChartSpec:
        if ctype != "heatmap":
            raise ChartError(f"Ma trận tương quan chỉ vẽ được bằng heatmap, không phải '{ctype}'.")
        m = p.data.set_index("column")
        m = m[[c for c in m.index if c in m.columns]]
        method = p.summary.get("method", "pearson")
        meta = self._meta(p, f"ma trận tương quan {method} từ kết quả EDA" if vi else f"{method} correlation matrix from the EDA result")
        meta.x = meta.y = None
        payload = matrix_payload(m, diverging=True, title_vals="r")
        payload.update(xlabel="", ylabel="")
        return finalize("heatmap", payload, meta, vi)

    def _categorical(self, p: EdaPayload, ctype: str, plan: ChartPlan, dataset_id, vi: bool) -> ChartSpec:
        if ctype not in ("count plot", "bar chart", "pie chart"):
            raise ChartError(f"Tần suất nhóm chỉ vẽ được bằng count plot/bar/pie, không phải '{ctype}'.")
        d = p.data
        col = p.columns[0] if p.columns else None
        if d is None or d.empty or col is None:
            raise ChartError("Không có giá trị nhóm nào để vẽ.")
        sub = d[d["column"] == col]
        meta = self._meta(p, f"top {p.summary.get('top_k')} giá trị phổ biến từ EDA" if vi else f"top {p.summary.get('top_k')} frequent values from EDA")
        info = p.summary.get("columns", {}).get(col, {})
        meta.rows_total = meta.rows_used = info.get("non_null") or meta.rows_total
        if info.get("shown_values") is not None and info.get("distinct", 0) > info["shown_values"]:
            meta.rows_used = int(sub["count"].sum())
            meta.warn(f"Chỉ vẽ top {info['shown_values']}/{info['distinct']:,} giá trị khác nhau." if vi
                      else f"Only the top {info['shown_values']} of {info['distinct']:,} distinct values are drawn.")
        if len(p.columns) > 1:
            meta.warn(f"Có {len(p.columns)} cột phân loại; chỉ vẽ '{col}'." if vi else f"{len(p.columns)} categorical columns; only '{col}' is drawn.")
        labels, values = list(sub["value"]), [float(v) for v in sub["count"]]
        meta.x = col
        meta.y = None
        if ctype == "pie chart":
            if info.get("shown_pct", 100) < 99.9:
                meta.warn("Biểu đồ tròn chỉ gồm top giá trị nên tổng không phải 100%." if vi else "The pie covers only the top values, so it does not sum to 100%.")
            payload = {"labels": labels, "values": values}
        else:
            payload = {"labels": labels, "values": values, "xlabel": column_label(col), "ylabel": "Số dòng" if vi else "Count"}
        return finalize(ctype, payload, meta, vi)

    def _quality(self, p: EdaPayload, ctype: str, plan: ChartPlan, dataset_id, vi: bool) -> ChartSpec:
        if ctype != "bar chart":
            raise ChartError(f"Chất lượng dữ liệu (tỷ lệ NULL) chỉ vẽ được bằng bar chart, không phải '{ctype}'.")
        d = p.data
        if d is None or d.empty:
            raise ChartError("Không có thống kê chất lượng dữ liệu để vẽ.")
        d = d.sort_values("null_pct", ascending=False, kind="stable")
        meta = self._meta(p, "tỷ lệ NULL theo cột từ EDA" if vi else "NULL percentage per column from EDA")
        cap = self._limits.max_categories
        if len(d) > cap:
            meta.warn(f"Có {len(d)} cột; chỉ vẽ {cap} cột có tỷ lệ NULL cao nhất." if vi else f"{len(d)} columns; drawing the {cap} with the most NULLs.")
            d = d.head(cap)
        if not float(d["null_pct"].fillna(0).max()):
            meta.warn("Không cột nào có giá trị NULL." if vi else "No column has NULL values.")
        meta.x, meta.y = "column", "null_pct"
        meta.units["null_pct"] = "%"
        payload = {"labels": list(d["column"]), "values": [float(v or 0) for v in d["null_pct"]],
                   "xlabel": "Cột" if vi else "Column", "ylabel": "% NULL"}
        meta.title = ""
        spec = finalize("bar chart", payload, meta, vi)
        spec.meta.title = spec.payload["title"] = "Tỷ lệ NULL theo cột" if vi else "NULL share by column"
        return spec

    def _numeric(self, p: EdaPayload, ctype: str, plan: ChartPlan, dataset_id, vi: bool) -> ChartSpec:
        if ctype not in ("box plot", "histogram", "kde plot", "violin plot"):
            raise ChartError(f"Thống kê số chỉ vẽ được box/histogram/KDE/violin, không phải '{ctype}'.")
        stats = [s for s in (p.summary.get("stats") or []) if s.get("count")]
        if not stats:
            raise ChartError("Không có cột số nào có dữ liệu để vẽ.")
        if ctype == "box plot":
            return self._box_from_stats(p, stats, vi)
        col_stats = stats[0]
        col = col_stats["column"]
        warn_multi = (f"Có {len(stats)} cột số; chỉ vẽ '{col}'." if vi else f"{len(stats)} numeric columns; only '{col}' is drawn.") if len(stats) > 1 else None
        spec = self._hist_sql(p, col_stats, dataset_id, vi) if ctype == "histogram" else self._sample_spec(p, col_stats, ctype, plan, dataset_id, vi)
        if warn_multi:
            spec.meta.warn(warn_multi)
        return spec

    def _box_from_stats(self, p: EdaPayload, stats: List[dict], vi: bool) -> ChartSpec:
        meta = self._meta(p, "quartile từ kết quả EDA (không cần dữ liệu thô)" if vi else "quartiles from the EDA result (no raw data needed)")
        usable = [s for s in stats if None not in (s.get("q1"), s.get("median"), s.get("q3"), s.get("min"), s.get("max"))]
        if not usable:
            raise ChartError("Chưa tính được quartile cho cột số nào để vẽ box plot.")
        if len(usable) > 6:
            meta.warn(f"Có {len(usable)} cột số; chỉ vẽ 6 cột đầu." if vi else f"{len(usable)} numeric columns; drawing the first 6.")
            usable = usable[:6]
        if len(usable) > 1:
            meta.warn("Các cột có thể khác thang đo; hãy xem từng cột riêng nếu cần." if vi else "Columns may have different scales; chart them one by one if needed.")
        out = []
        for s in usable:
            iqr = s["q3"] - s["q1"]
            lo, hi = max(s["min"], s["q1"] - 1.5 * iqr), min(s["max"], s["q3"] + 1.5 * iqr)
            fliers = [v for v in (s["min"], s["max"]) if v < lo or v > hi]
            out.append({"label": s["column"], "q1": s["q1"], "med": s["median"], "q3": s["q3"],
                        "whislo": lo, "whishi": hi, "fliers": fliers})
        if any(s["fliers"] for s in out):
            meta.warn("Chỉ đánh dấu giá trị min/max nằm ngoài whisker (1.5×IQR), không vẽ từng điểm ngoại lai." if vi
                      else "Only min/max beyond the whiskers (1.5×IQR) are marked; individual outliers are not drawn.")
        cols = [s["column"] for s in usable]
        meta.y, meta.x = cols[0], None
        for c in cols:
            if infer_unit(c):
                meta.units[c] = infer_unit(c)  # type: ignore[assignment]
        if any(s.get("quantile_basis") not in (None, "exact") for s in usable):
            meta.warn("Quartile của một số cột được ước tính trên mẫu." if vi else "Quartiles of some columns are estimated from a sample.")
        payload = {"stats": out, "xlabel": "", "ylabel": column_label(cols[0]) if len(cols) == 1 else ("Giá trị" if vi else "Value")}
        return finalize("box plot", payload, meta, vi)

    def _hist_sql(self, p: EdaPayload, st: dict, dataset_id, vi: bool) -> ChartSpec:
        col, lo, hi, n = st["column"], st.get("min"), st.get("max"), int(st["count"])
        if lo is None or hi is None or lo == hi:
            raise ChartError(f"Mọi giá trị của '{col}' bằng nhau; không có phân phối để vẽ." if vi else f"All values of '{col}' are equal; no distribution to draw.")
        bins = hist_bins(n, self._limits)
        w = (hi - lo) / bins
        q, t = quote_ident(col), quote_ident(p.table)
        sql = (
            f"SELECT CASE WHEN v >= {float(hi)!r} THEN {bins - 1} ELSE CAST((v - {float(lo)!r}) / {float(w)!r} AS INTEGER) END AS b, "
            f"COUNT(*) AS n FROM (SELECT {q} AS v FROM {t} WHERE {_NUM.format(c=q)}) GROUP BY b"
        )
        repo = self._store.repository(dataset_id)
        df, err = repo.fetch(sql, max_rows=bins + 5)
        if err:
            raise ChartError(f"Không gom nhóm được '{col}' bằng SQL: {err}")
        counts = [0.0] * bins
        for b, c in zip(df["b"], df["n"]):
            if b is not None and 0 <= int(b) < bins:
                counts[int(b)] += float(c)
        edges = [lo + i * w for i in range(bins + 1)]
        meta = ChartMeta(source="sql_aggregate", rows_total=n, rows_used=int(sum(counts)),
                         method=(f"gom {bins} khoảng đều bằng SQL (chính xác, không lấy mẫu)" if vi else f"{bins} equal-width bins aggregated in SQL (exact, no sampling)"))
        meta.y = col
        if infer_unit(col):
            meta.units[col] = infer_unit(col)  # type: ignore[assignment]
        payload = histogram_payload(edges, counts, col)
        payload["ylabel"] = "Số dòng" if vi else "Count"
        return finalize("histogram", payload, meta, vi)

    def _sample_spec(self, p: EdaPayload, st: dict, ctype: str, plan: ChartPlan, dataset_id, vi: bool) -> ChartSpec:
        col, n = st["column"], int(st["count"])
        limit = self._settings.chart_sample_rows
        k = max(1, math.ceil(n / limit))
        q, t = quote_ident(col), quote_ident(p.table)
        if k == 1:
            sql = f"SELECT {q} AS v FROM {t} WHERE {_NUM.format(c=q)}"
        else:
            sql = (f"SELECT v FROM (SELECT {q} AS v, ROW_NUMBER() OVER () AS rn FROM {t} "
                   f"WHERE {_NUM.format(c=q)}) WHERE (rn - 1) % {k} = 0")
        repo = self._store.repository(dataset_id)
        df, err = repo.fetch(sql, max_rows=limit + 1)
        if err:
            raise ChartError(f"Không lấy được dữ liệu của '{col}' để vẽ: {err}")
        df = df.rename(columns={"v": col})
        spec = build_from_frame(df, ctype, plan.goal, self._limits, vi=vi, source="table_sample" if k > 1 else "query_result")
        spec.meta.source = "table_sample"
        spec.meta.rows_total = n
        spec.meta.rows_used = len(df)
        if k > 1:
            spec.meta.method = (f"lấy mẫu có hệ thống mỗi {k} dòng ({len(df):,}/{n:,})" if vi else f"systematic sample of every {k}th row ({len(df):,}/{n:,})")
            spec.meta.warn(f"Biểu đồ được vẽ trên mẫu {len(df):,}/{n:,} dòng." if vi else f"The chart is drawn from a sample of {len(df):,}/{n:,} rows.")
        else:
            spec.meta.method = "đọc toàn bộ giá trị số của cột (trong giới hạn)" if vi else "read all numeric values of the column (within limits)"
        return spec


__all__ = ["BuildChartSpecTool"]
