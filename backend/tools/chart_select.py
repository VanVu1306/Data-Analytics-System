"""
backend/tools/chart_select.py
-----------------------------
Chọn loại biểu đồ + dựng dữ liệu vẽ (ChartSpec) một cách XÁC ĐỊNH, không gọi LLM, không exec code.

- Đầu vào: DataFrame (kết quả truy vấn hoặc dữ liệu đã chuẩn bị từ EDA).
- Loại biểu đồ nằm trong allowlist `CHART_TYPES`; mỗi loại có một builder riêng.
- Chọn theo MỤC TIÊU (so sánh / xu hướng / phân phối / quan hệ / thành phần) + KIỂU DỮ LIỆU, không theo từ khóa.
- Loại do người dùng chỉ định luôn được ưu tiên: nếu dữ liệu không phù hợp thì trả lỗi rõ ràng kèm gợi ý
  (không âm thầm đổi sang loại khác); chỉ khi tự chọn mới tự quyết định.
- Mọi bước giới hạn/lấy mẫu/gộp nhóm đều ghi vào `ChartMeta` (rows_total, rows_used, method, warnings).
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from backend.tools.intent import CHART_TYPES


class ChartError(Exception):
    """Dữ liệu không phù hợp / không đủ để vẽ loại biểu đồ này. `str(e)` đã gồm gợi ý (nếu có)."""

    def __init__(self, message: str, suggestion: Optional[str] = None):
        self.reason = message
        self.suggestion = suggestion
        super().__init__(f"{message} {suggestion}".strip() if suggestion else message)


@dataclass(frozen=True)
class ChartLimits:
    max_categories: int = 30
    pie_max_slices: int = 8
    max_points: int = 2000
    hist_bins: int = 30

    @classmethod
    def from_settings(cls, s) -> "ChartLimits":
        return cls(
            max_categories=max(2, s.chart_max_categories),
            pie_max_slices=max(2, s.chart_pie_max_slices),
            max_points=max(10, s.chart_max_points),
            hist_bins=max(5, s.chart_hist_bins),
        )


@dataclass
class ChartMeta:
    source: str = "query_result"     # query_result | eda_payload | table_sample | sql_aggregate
    rows_total: Optional[int] = None  # số dòng gốc (kết quả truy vấn / bảng)
    rows_used: Optional[int] = None   # số dòng thực sự dùng để vẽ
    method: str = ""                  # cách chuẩn bị dữ liệu
    chart_type: str = ""              # loại được vẽ
    requested_type: Optional[str] = None
    reason: str = ""                  # vì sao chọn loại này
    goal: Optional[str] = None
    x: Optional[str] = None
    y: Optional[str] = None
    group: Optional[str] = None
    title: str = ""
    xlabel: str = ""
    ylabel: str = ""
    units: Dict[str, str] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)

    def warn(self, message: str) -> None:
        if message not in self.warnings:
            self.warnings.append(message)


@dataclass
class ChartSpec:
    chart_type: str
    payload: Dict[str, Any]
    meta: ChartMeta


# ── Phân loại cột ──────────────────────────────────────────────────────────
_ISO_DATE = re.compile(r"^\d{4}-\d{2}(-\d{2})?([ T]\d{2}:\d{2}(:\d{2}(\.\d+)?)?)?$")
_ORDINAL_NAME = re.compile(
    r"(^|[_\s])(year|month|quarter|week|day|hour|nam|thang|quy|tuan|ngay|gio)($|[_\s])", re.IGNORECASE
)
_UNIT_SUFFIX = {
    "usd": "USD", "vnd": "VND", "eur": "EUR", "pct": "%", "percent": "%", "percentage": "%",
    "kg": "kg", "km": "km", "ms": "ms", "sec": "s", "sqm": "m²",
}


def infer_unit(column: str) -> Optional[str]:
    """Đơn vị suy ra từ tên cột: `price (USD)`, `[kg]` hoặc hậu tố `_usd`, `_pct`. Không đoán nếu không rõ."""
    m = re.search(r"[\(\[]([^)\]]{1,12})[\)\]]\s*$", column)
    if m:
        return m.group(1).strip()
    tokens = re.split(r"[_\s]+", column.strip().lower())
    if len(tokens) > 1 and tokens[-1] in _UNIT_SUFFIX:
        return _UNIT_SUFFIX[tokens[-1]]
    return None


def column_label(column: str) -> str:
    base = re.sub(r"\s*[\(\[][^)\]]{1,12}[\)\]]\s*$", "", str(column))
    tokens = re.split(r"[_\s]+", base.strip())
    unit = infer_unit(str(column))
    if len(tokens) > 1 and tokens[-1].lower() in _UNIT_SUFFIX and not re.search(r"[\(\[]", str(column)):
        tokens = tokens[:-1]
    text = " ".join(t for t in tokens if t) or str(column)
    return f"{text} ({unit})" if unit else text


@dataclass
class Frame:
    df: pd.DataFrame
    kinds: Dict[str, str]          # numeric | temporal | categorical | other
    ordinal: set                   # cột số mang nghĩa thứ tự thời gian (year, month...)

    @property
    def numeric(self) -> List[str]:
        return [c for c, k in self.kinds.items() if k == "numeric"]

    @property
    def temporal(self) -> List[str]:
        return [c for c, k in self.kinds.items() if k == "temporal"]

    @property
    def categorical(self) -> List[str]:
        return [c for c, k in self.kinds.items() if k == "categorical"]

    @property
    def measures(self) -> List[str]:
        """Cột có thể làm giá trị số: số thật trước, rồi cột số dạng thứ tự (year...)."""
        return self.numeric + [c for c in self.temporal if c in self.ordinal]


def classify(df: pd.DataFrame) -> Frame:
    df = df.copy()
    kinds: Dict[str, str] = {}
    ordinal: set = set()
    for c in list(df.columns):
        s = df[c]
        if not s.notna().any():
            kinds[c] = "other"
        elif pd.api.types.is_bool_dtype(s):
            kinds[c] = "categorical"
        elif pd.api.types.is_datetime64_any_dtype(s):
            kinds[c] = "temporal"
        elif pd.api.types.is_numeric_dtype(s):
            vals = s.dropna()
            if _ORDINAL_NAME.search(str(c)) and bool((vals % 1 == 0).all()):
                kinds[c] = "temporal"
                ordinal.add(c)
            else:
                kinds[c] = "numeric"
        else:
            strs = s.dropna().astype(str)
            if bool(strs.map(lambda v: bool(_ISO_DATE.match(v.strip()))).all()):
                try:
                    df[c] = pd.to_datetime(s, errors="coerce")
                    kinds[c] = "temporal"
                    continue
                except (ValueError, TypeError):
                    pass
            num = pd.to_numeric(s, errors="coerce")
            if num.notna().sum() >= 0.95 * s.notna().sum():
                df[c] = num
                kinds[c] = "numeric"
            else:
                kinds[c] = "categorical"
    return Frame(df, kinds, ordinal)


# ── Mục tiêu <-> loại biểu đồ ─────────────────────────────────────────────
GOAL_TYPES = {
    "comparison": ("bar chart", "count plot"),
    "trend": ("line chart",),
    "distribution": ("histogram", "kde plot", "box plot", "violin plot", "count plot"),
    "relationship": ("scatter plot", "heatmap"),
    "composition": ("pie chart", "bar chart", "count plot"),
}
_GOAL_VI = {
    "comparison": "so sánh", "trend": "xu hướng", "distribution": "phân phối",
    "relationship": "quan hệ", "composition": "thành phần",
}
_GOAL_BEST = {
    "comparison": "bar chart", "trend": "line chart", "distribution": "histogram",
    "relationship": "scatter plot", "composition": "pie chart",
}


def _fmt_labels(s: pd.Series) -> List[str]:
    if pd.api.types.is_datetime64_any_dtype(s):
        vals = s.dropna()
        if len(vals) and bool((vals.dt.hour + vals.dt.minute + vals.dt.second == 0).all()):
            fmt = "%Y-%m" if bool((vals.dt.day == 1).all()) else "%Y-%m-%d"
        else:
            fmt = "%Y-%m-%d %H:%M"
        return [v.strftime(fmt) if pd.notna(v) else "" for v in s]
    if pd.api.types.is_float_dtype(s):
        return [("" if pd.isna(v) else (str(int(v)) if float(v).is_integer() else f"{v:g}")) for v in s]
    return ["" if pd.isna(v) else str(v) for v in s]


class _Builder:
    """Ngữ cảnh dựng một biểu đồ từ một DataFrame."""

    def __init__(self, df: pd.DataFrame, limits: ChartLimits, vi: bool, goal: Optional[str]):
        self.frame = classify(df)
        self.df = self.frame.df
        self.limits = limits
        self.vi = vi
        self.goal = goal
        self.meta = ChartMeta(goal=goal)
        self.meta.rows_total = len(df)
        self.meta.rows_used = len(df)

    def tr(self, vi: str, en: str) -> str:
        return vi if self.vi else en

    def set_cols(self, x: Optional[str] = None, y: Optional[str] = None, group: Optional[str] = None) -> None:
        self.meta.x, self.meta.y, self.meta.group = x, y, group
        for c in (x, y):
            if c and infer_unit(str(c)):
                self.meta.units[str(c)] = infer_unit(str(c))  # type: ignore[assignment]

    def top(self, labels: List[str], values: List[float], what: str, limit: int) -> Tuple[List[str], List[float]]:
        if len(labels) > limit:
            self.meta.warn(self.tr(
                f"Có {len(labels)} {what}; chỉ hiển thị {limit} nhóm đầu để biểu đồ dễ đọc.",
                f"{len(labels)} {what}; showing only the top {limit} for readability.",
            ))
            return labels[:limit], values[:limit]
        return labels, values

    def aggregate(self, x: str, y: str) -> pd.DataFrame:
        d = self.df[[x, y]].dropna(subset=[y])
        d = d[d[x].notna()]
        if d[x].duplicated().any():
            d = d.groupby(x, sort=False, as_index=False)[y].sum()
            self.meta.warn(self.tr(
                f"Cột '{x}' có giá trị lặp; đã cộng tổng '{y}' theo '{x}'.",
                f"Column '{x}' has repeated values; '{y}' was summed per '{x}'.",
            ))
            self.meta.method = self.tr("gộp tổng theo nhóm", "summed per group")
        return d

    def label_col(self, exclude: Optional[str] = None) -> Optional[str]:
        for c in self.frame.categorical + [t for t in self.frame.temporal if t not in self.frame.ordinal]:
            if c != exclude:
                return c
        return None


# ── Builder từng loại ──────────────────────────────────────────────────────
def _bar(b: _Builder) -> Dict[str, Any]:
    f = b.frame
    x = b.label_col()
    measures = f.measures
    if x is None and len(f.numeric) >= 2:
        x, measures = f.numeric[0], f.numeric[1:]
    elif x is None and len(f.temporal) and f.numeric:
        x = f.temporal[0]
    y = next((m for m in f.numeric if m != x), None) or next((m for m in measures if m != x), None)
    if x is None or y is None:
        raise ChartError(
            b.tr("Biểu đồ cột cần một cột nhóm/nhãn và một cột số.", "A bar chart needs a label column and a numeric column."),
            b.tr("Với một cột phân loại hãy dùng count plot; với một cột số hãy dùng histogram.",
                 "Use a count plot for one categorical column, or a histogram for one numeric column."),
        )
    if len([m for m in f.numeric if m != x]) > 1:
        b.meta.warn(b.tr(f"Có nhiều cột số; biểu đồ chỉ vẽ '{y}'.", f"Several numeric columns; only '{y}' is drawn."))
    d = b.aggregate(x, y)
    if d.empty:
        raise ChartError(b.tr(f"Cột '{y}' không có giá trị số hợp lệ.", f"Column '{y}' has no valid numeric values."))
    if f.kinds.get(x) == "temporal":
        d = d.sort_values(x)
    else:
        d = d.sort_values(y, ascending=False, kind="stable")
    labels, values = b.top(_fmt_labels(d[x]), [float(v) for v in d[y]], b.tr("nhóm", "groups"), b.limits.max_categories)
    b.meta.rows_used = len(d)
    b.set_cols(x, y)
    return {"labels": labels, "values": values, "xlabel": column_label(x), "ylabel": column_label(y)}


def _count(b: _Builder) -> Dict[str, Any]:
    f = b.frame
    x = b.label_col()
    if x is None:
        raise ChartError(
            b.tr("Count plot cần một cột phân loại để đếm tần suất.", "A count plot needs a categorical column to count."),
            b.tr("Hãy dùng histogram cho cột số.", "Use a histogram for a numeric column."),
        )
    s = b.df[x].dropna()
    pre = f.numeric[0] if f.numeric and bool(b.df[x].dropna().is_unique) else None
    if pre:  # kết quả đã đếm sẵn (mỗi nhóm một dòng + cột số): dùng luôn cột số làm tần suất
        d = b.df[[x, pre]].dropna().sort_values(pre, ascending=False, kind="stable")
        labels, values = _fmt_labels(d[x]), [float(v) for v in d[pre]]
        b.meta.method = b.tr("dùng cột số có sẵn làm tần suất", "used the existing numeric column as the frequency")
        ylabel = column_label(pre)
    else:
        vc = s.astype(str).value_counts()
        labels, values = list(vc.index), [float(v) for v in vc.values]
        b.meta.method = b.tr("đếm số dòng theo nhóm", "row counts per group")
        ylabel = b.tr("Số dòng", "Count")
        b.meta.rows_used = int(len(s))
    labels, values = b.top(labels, values, b.tr("nhóm", "groups"), b.limits.max_categories)
    b.set_cols(x, pre)
    return {"labels": labels, "values": values, "xlabel": column_label(x), "ylabel": ylabel}


def _pie(b: _Builder) -> Dict[str, Any]:
    f = b.frame
    x = b.label_col()
    y = f.numeric[0] if f.numeric else None
    if x is None or y is None:
        raise ChartError(
            b.tr("Biểu đồ tròn cần một cột nhóm và một cột số không âm.", "A pie chart needs a label column and a non-negative numeric column."),
            b.tr("Hãy thử count plot hoặc bar chart.", "Try a count plot or bar chart."),
        )
    d = b.aggregate(x, y)
    if (d[y] < 0).any():
        raise ChartError(
            b.tr(f"Cột '{y}' có giá trị âm nên không thể biểu diễn tỷ trọng bằng biểu đồ tròn.",
                 f"Column '{y}' has negative values, so shares cannot be shown as a pie chart."),
            b.tr("Hãy dùng bar chart.", "Use a bar chart."),
        )
    d = d[d[y] > 0].sort_values(y, ascending=False, kind="stable")
    if d.empty or float(d[y].sum()) <= 0:
        raise ChartError(b.tr(f"Tổng '{y}' bằng 0 nên không có tỷ trọng để vẽ.", f"The total of '{y}' is 0; there are no shares to draw."))
    labels, values = _fmt_labels(d[x]), [float(v) for v in d[y]]
    cap = b.limits.pie_max_slices
    if len(labels) > cap:
        other = sum(values[cap - 1:])
        b.meta.warn(b.tr(
            f"Có {len(labels)} nhóm; giữ {cap - 1} nhóm lớn nhất, phần còn lại gộp thành 'Khác'.",
            f"{len(labels)} groups; kept the {cap - 1} largest and merged the rest into 'Other'.",
        ))
        labels, values = labels[:cap - 1] + [b.tr("Khác", "Other")], values[:cap - 1] + [other]
    b.meta.rows_used = len(d)
    b.set_cols(x, y)
    return {"labels": labels, "values": values}


def _line(b: _Builder) -> Dict[str, Any]:
    f = b.frame
    x = f.temporal[0] if f.temporal else None
    if x is None and len(f.numeric) >= 2:
        x = f.numeric[0]
    if x is None:
        x = b.label_col()
        if x is not None and f.numeric:
            b.meta.warn(b.tr("Trục x không phải thời gian/số; các điểm được nối theo thứ tự dữ liệu.",
                             "The x axis is not time/numeric; points are joined in data order."))
    ys = [c for c in f.numeric if c != x]
    if x is None or not ys:
        raise ChartError(
            b.tr("Biểu đồ đường cần một cột trục x (thời gian/thứ tự) và ít nhất một cột số.",
                 "A line chart needs an x column (time/order) and at least one numeric column."),
            b.tr("Hãy thử bar chart hoặc histogram.", "Try a bar chart or a histogram."),
        )
    group = None
    cats = [c for c in f.categorical if c != x]
    if cats and len(ys) == 1:
        group = cats[0]
    d = b.df[[x] + ([group] if group else []) + ys].copy()
    d = d[d[x].notna()]
    series: List[Dict[str, Any]] = []
    if group:
        y = ys[0]
        totals = d.groupby(group)[y].sum().sort_values(ascending=False)
        keep = list(totals.index[:6])
        if len(totals) > 6:
            b.meta.warn(b.tr(f"Có {len(totals)} nhóm '{group}'; chỉ vẽ 6 nhóm lớn nhất.", f"{len(totals)} '{group}' groups; drawing the 6 largest."))
        for g in keep:
            sub = d[d[group] == g].groupby(x, as_index=False)[y].sum().sort_values(x)
            series.append({"name": str(g), "x": list(sub[x]), "y": [float(v) for v in sub[y]]})
        used = int(d[d[group].isin(keep)].shape[0])
    else:
        if len(ys) > 5:
            b.meta.warn(b.tr(f"Có {len(ys)} cột số; chỉ vẽ 5 cột đầu.", f"{len(ys)} numeric columns; drawing the first 5."))
            ys = ys[:5]
        if d[x].duplicated().any():
            d = d.groupby(x, as_index=False)[ys].sum()
            b.meta.warn(b.tr(f"Cột '{x}' có giá trị lặp; đã cộng tổng theo '{x}'.", f"Column '{x}' has repeated values; summed per '{x}'."))
        d = d.sort_values(x) if f.kinds.get(x) != "categorical" else d
        for y in ys:
            sub = d[[x, y]].dropna()
            series.append({"name": column_label(y), "x": list(sub[x]), "y": [float(v) for v in sub[y]]})
        used = len(d)
    series = [s for s in series if s["x"]]
    if not series or max(len(s["x"]) for s in series) < 2:
        raise ChartError(
            b.tr("Cần ít nhất 2 điểm dữ liệu để vẽ biểu đồ đường.", "At least 2 data points are needed for a line chart."),
            b.tr("Hãy dùng bar chart cho một giá trị duy nhất.", "Use a bar chart for a single value."),
        )
    for s in series:
        if len(s["x"]) > b.limits.max_points:
            k = math.ceil(len(s["x"]) / b.limits.max_points)
            s["x"], s["y"] = s["x"][::k], s["y"][::k]
            b.meta.warn(b.tr(f"Chuỗi '{s['name']}' quá dài; vẽ mỗi {k} điểm một lần.", f"Series '{s['name']}' is long; drawing every {k}th point."))
            b.meta.method = b.tr("lấy mẫu mỗi k điểm", "every-kth-point sampling")
    b.meta.rows_used = used
    b.set_cols(x, ys[0], group)
    temporal = f.kinds.get(x) == "temporal"
    return {"series": series, "xlabel": column_label(x), "ylabel": column_label(ys[0]) if len(ys) == 1 or group else b.tr("Giá trị", "Value"),
            "temporal": temporal and x not in f.ordinal}


def _scatter(b: _Builder) -> Dict[str, Any]:
    f = b.frame
    nums = f.numeric if len(f.numeric) >= 2 else f.measures
    if len(nums) < 2:
        raise ChartError(
            b.tr("Biểu đồ phân tán cần ít nhất 2 cột số.", "A scatter plot needs at least 2 numeric columns."),
            b.tr("Hãy dùng bar chart (nhóm + số) hoặc histogram (một cột số).", "Use a bar chart (category + number) or a histogram (one number)."),
        )
    x, y = nums[0], nums[1]
    hue = next((c for c in f.categorical if 2 <= b.df[c].nunique() <= 8), None)
    d = b.df[[x, y] + ([hue] if hue else [])].dropna(subset=[x, y])
    if len(d) < 2:
        raise ChartError(b.tr("Cần ít nhất 2 dòng có đủ giá trị ở cả hai cột.", "At least 2 rows with both values are needed."))
    if d[x].nunique() < 2 or d[y].nunique() < 2:
        b.meta.warn(b.tr("Một trục gần như không đổi nên khó thấy quan hệ.", "One axis is constant, so any relationship is hard to see."))
    total = len(d)
    if total > b.limits.max_points:
        d = d.sample(n=b.limits.max_points, random_state=0).sort_index()
        b.meta.warn(b.tr(f"Vẽ mẫu ngẫu nhiên {len(d):,}/{total:,} điểm để biểu đồ rõ ràng.", f"Drawing a random sample of {len(d):,}/{total:,} points."))
        b.meta.method = b.tr("mẫu ngẫu nhiên (seed cố định)", "random sample (fixed seed)")
    elif total < 5:
        b.meta.warn(b.tr(f"Chỉ có {total} điểm; quan hệ có thể không đáng tin.", f"Only {total} points; any relationship may be unreliable."))
    if len(f.numeric) > 2:
        b.meta.warn(b.tr(f"Có {len(f.numeric)} cột số; biểu đồ chỉ vẽ '{x}' và '{y}'.", f"{len(f.numeric)} numeric columns; plotting only '{x}' vs '{y}'."))
    b.meta.rows_used = len(d)
    b.set_cols(x, y, hue)
    return {
        "x": [float(v) for v in d[x]], "y": [float(v) for v in d[y]],
        "hue": [str(v) for v in d[hue]] if hue else None,
        "xlabel": column_label(x), "ylabel": column_label(y),
    }


def _values(b: _Builder) -> Tuple[str, np.ndarray]:
    f = b.frame
    cols = f.numeric or f.measures
    if not cols:
        raise ChartError(
            b.tr("Biểu đồ phân phối cần một cột số.", "A distribution chart needs a numeric column."),
            b.tr("Với cột phân loại hãy dùng count plot.", "For a categorical column use a count plot."),
        )
    c = cols[0]
    if len(cols) > 1:
        b.meta.warn(b.tr(f"Có {len(cols)} cột số; chỉ vẽ '{c}'.", f"{len(cols)} numeric columns; only '{c}' is drawn."))
    v = pd.to_numeric(b.df[c], errors="coerce").replace([np.inf, -np.inf], np.nan).dropna().to_numpy(dtype=float)
    return c, v


def hist_bins(n: int, limits: ChartLimits) -> int:
    return int(min(limits.hist_bins, max(5, math.isqrt(max(n, 1)))))


def histogram_payload(edges: List[float], counts: List[float], column: str) -> Dict[str, Any]:
    return {"edges": [float(e) for e in edges], "counts": [float(c) for c in counts],
            "xlabel": column_label(column), "ylabel": "count"}


def _hist(b: _Builder) -> Dict[str, Any]:
    c, v = _values(b)
    if len(v) < 2:
        raise ChartError(b.tr(f"Cột '{c}' có dưới 2 giá trị số nên không vẽ được phân phối.", f"Column '{c}' has fewer than 2 numeric values."))
    if float(v.max()) == float(v.min()):
        raise ChartError(b.tr(f"Mọi giá trị của '{c}' đều bằng nhau ({v[0]:g}); không có phân phối để vẽ.", f"All values of '{c}' are equal ({v[0]:g})."))
    if len(v) < 10:
        b.meta.warn(b.tr(f"Chỉ có {len(v)} giá trị; histogram có thể kém đại diện.", f"Only {len(v)} values; the histogram may be unrepresentative."))
    counts, edges = np.histogram(v, bins=hist_bins(len(v), b.limits))
    b.meta.rows_used = len(v)
    b.meta.method = b.tr(f"chia {len(counts)} khoảng đều", f"{len(counts)} equal-width bins")
    b.set_cols(c)
    p = histogram_payload(list(edges), list(counts), c)
    p["ylabel"] = b.tr("Số dòng", "Count")
    return p


def _kde_curve(v: np.ndarray, max_points: int) -> Tuple[np.ndarray, np.ndarray]:
    if len(v) > max_points:
        v = v[:: math.ceil(len(v) / max_points)]
    std = float(v.std(ddof=1))
    q1, q3 = np.quantile(v, [0.25, 0.75])
    spread = min(std, (q3 - q1) / 1.34) if q3 > q1 else std
    h = 0.9 * spread * len(v) ** (-0.2)
    if not math.isfinite(h) or h <= 0:
        raise ChartError("Không ước lượng được mật độ (dữ liệu gần như không đổi).")
    grid = np.linspace(float(v.min()) - 3 * h, float(v.max()) + 3 * h, 200)
    dens = np.zeros_like(grid)
    for start in range(0, len(v), 500):
        chunk = v[start:start + 500]
        dens += np.exp(-0.5 * ((grid[:, None] - chunk[None, :]) / h) ** 2).sum(axis=1)
    dens /= len(v) * h * math.sqrt(2 * math.pi)
    return grid, dens


def _grouped_values(b: _Builder, min_n: int) -> Tuple[str, Optional[str], List[Tuple[str, np.ndarray]]]:
    f = b.frame
    c, v = _values(b)
    group = next((g for g in f.categorical if 2 <= b.df[g].nunique() <= 60), None)
    out: List[Tuple[str, np.ndarray]] = []
    if group:
        d = b.df[[group, c]].copy()
        d[c] = pd.to_numeric(d[c], errors="coerce")
        d = d.dropna()
        order = d[group].astype(str).value_counts()
        cap = 12
        if len(order) > cap:
            b.meta.warn(b.tr(f"Có {len(order)} nhóm '{group}'; chỉ vẽ {cap} nhóm có nhiều dòng nhất.", f"{len(order)} '{group}' groups; drawing the {cap} largest."))
        for g in list(order.index[:cap]):
            vals = d.loc[d[group].astype(str) == g, c].to_numpy(dtype=float)
            if len(vals) >= min_n:
                out.append((g, vals))
        dropped = min(len(order), cap) - len(out)
        if dropped:
            b.meta.warn(b.tr(f"Bỏ {dropped} nhóm có dưới {min_n} giá trị.", f"Dropped {dropped} groups with fewer than {min_n} values."))
        b.meta.rows_used = int(sum(len(x) for _, x in out))
    elif len(v) >= min_n:
        out = [(column_label(c), v)]
        b.meta.rows_used = len(v)
    if not out:
        raise ChartError(
            b.tr(f"Không đủ dữ liệu: mỗi nhóm cần ít nhất {min_n} giá trị số.", f"Not enough data: each group needs at least {min_n} numeric values."),
            b.tr("Hãy thử bar chart hoặc để hệ thống tự chọn.", "Try a bar chart or let the system choose."),
        )
    b.set_cols(None, c, group if len(out) > 1 or group else None)
    return c, group, out


def _kde(b: _Builder) -> Dict[str, Any]:
    c, group, groups = _grouped_values(b, 5)
    series = []
    for name, v in groups[:6]:
        if float(v.max()) == float(v.min()):
            b.meta.warn(b.tr(f"Nhóm '{name}' có giá trị không đổi; bỏ qua.", f"Group '{name}' is constant; skipped."))
            continue
        grid, dens = _kde_curve(v, b.limits.max_points)
        series.append({"name": name, "x": [float(g) for g in grid], "y": [float(d) for d in dens]})
    if not series:
        raise ChartError(b.tr("Dữ liệu không đổi nên không ước lượng được mật độ.", "The data is constant; density cannot be estimated."))
    b.meta.method = b.tr("KDE Gaussian, băng thông Silverman", "Gaussian KDE, Silverman bandwidth")
    return {"series": series, "xlabel": column_label(c), "ylabel": b.tr("Mật độ", "Density")}


def _box_like(b: _Builder, min_n: int) -> Dict[str, Any]:
    c, group, groups = _grouped_values(b, min_n)
    if any(len(v) < 5 for _, v in groups):
        b.meta.warn(b.tr("Một số nhóm có dưới 5 giá trị; phân vị có thể không đáng tin.", "Some groups have fewer than 5 values; quartiles may be unreliable."))
    return {"groups": [{"label": n, "values": [float(x) for x in v]} for n, v in groups],
            "xlabel": column_label(group) if group else "", "ylabel": column_label(c)}


def _box(b: _Builder) -> Dict[str, Any]:
    return _box_like(b, 2)


def _violin(b: _Builder) -> Dict[str, Any]:
    p = _box_like(b, 5)
    return p


def matrix_payload(matrix: pd.DataFrame, *, diverging: bool, title_vals: str = "") -> Dict[str, Any]:
    return {
        "matrix": [[None if pd.isna(v) else float(v) for v in row] for row in matrix.to_numpy()],
        "xlabels": [str(c) for c in matrix.columns], "ylabels": [str(i) for i in matrix.index],
        "diverging": diverging, "cbar": title_vals,
    }


def _heatmap(b: _Builder) -> Dict[str, Any]:
    f = b.frame
    nums = f.numeric
    cats = f.categorical
    if len(cats) >= 2 and nums and len(nums) < 3:  # bảng chéo nhóm x nhóm -> giá trị
        r, c, v = cats[0], cats[1], nums[0]
        pivot = b.df.pivot_table(index=r, columns=c, values=v, aggfunc="sum")
        if pivot.shape[0] > 25 or pivot.shape[1] > 25:
            rows = pivot.abs().sum(axis=1).sort_values(ascending=False).index[:25]
            cols = pivot.abs().sum(axis=0).sort_values(ascending=False).index[:25]
            pivot = pivot.loc[rows, cols]
            b.meta.warn(b.tr("Bảng chéo quá lớn; chỉ vẽ tối đa 25×25 nhóm lớn nhất.", "Cross-tab too large; drawing the largest 25×25 groups."))
        if pivot.empty:
            raise ChartError(b.tr("Không có dữ liệu để dựng heatmap.", "No data to build a heatmap."))
        b.meta.method = b.tr("bảng chéo (tổng) theo hai cột nhóm", "cross-tab (sum) by two category columns")
        b.set_cols(r, v, c)
        p = matrix_payload(pivot, diverging=False, title_vals=column_label(v))
        p.update(xlabel=column_label(c), ylabel=column_label(r))
        return p
    if len(nums) < 2:
        raise ChartError(
            b.tr("Heatmap tương quan cần ít nhất 2 cột số (hoặc 2 cột nhóm + 1 cột số).", "A correlation heatmap needs at least 2 numeric columns (or 2 category columns + 1 number)."),
            b.tr("Hãy thử scatter plot hoặc bar chart.", "Try a scatter plot or bar chart."),
        )
    cap = 15
    if len(nums) > cap:
        b.meta.warn(b.tr(f"Có {len(nums)} cột số; chỉ tính {cap} cột đầu.", f"{len(nums)} numeric columns; using the first {cap}."))
        nums = nums[:cap]
    d = b.df[nums].apply(pd.to_numeric, errors="coerce")
    usable = [c for c in nums if d[c].notna().sum() >= 3 and d[c].nunique() > 1]
    for c in nums:
        if c not in usable:
            b.meta.warn(b.tr(f"Cột '{c}' bị loại: hằng số hoặc dưới 3 giá trị số.", f"Column '{c}' dropped: constant or fewer than 3 numeric values."))
    if len(usable) < 2:
        raise ChartError(b.tr("Không đủ cột số có biến thiên để tính tương quan.", "Not enough varying numeric columns for correlation."))
    corr = d[usable].corr(method="pearson", min_periods=3)
    b.meta.method = b.tr("tương quan Pearson theo cặp", "pairwise Pearson correlation")
    b.meta.rows_used = int(d[usable].dropna(how="all").shape[0])
    b.set_cols(None, None)
    p = matrix_payload(corr, diverging=True, title_vals="r")
    p.update(xlabel="", ylabel="")
    return p


_BUILDERS = {
    "bar chart": _bar, "line chart": _line, "pie chart": _pie, "scatter plot": _scatter,
    "histogram": _hist, "kde plot": _kde, "box plot": _box, "violin plot": _violin,
    "heatmap": _heatmap, "count plot": _count,
}
assert set(_BUILDERS) == set(CHART_TYPES)


# ── Tự chọn loại ───────────────────────────────────────────────────────────
def auto_select(b: _Builder) -> Tuple[str, str]:
    """(loại, lý do) theo mục tiêu + kiểu dữ liệu. Chỉ dùng khi người dùng không chỉ định loại."""
    f, goal = b.frame, b.goal
    nums, cats = f.numeric, f.categorical
    temporal = f.temporal
    labelish = b.label_col()
    n = len(b.df)
    if not nums and not [t for t in temporal if t in f.ordinal] and not cats and not temporal:
        raise ChartError(b.tr("Không có cột nào có dữ liệu để vẽ.", "No column has data to draw."))

    def why(vi: str, en: str) -> str:
        return b.tr(vi, en)

    if goal == "composition" and labelish and nums and n >= 2:
        if (b.df[nums[0]].dropna() >= 0).all() and 0 < b.df[labelish].nunique() <= b.limits.pie_max_slices:
            return "pie chart", why("mục tiêu thành phần, 1 cột nhóm + 1 cột số không âm, ít nhóm", "composition goal, one category + one non-negative number, few groups")
        return "bar chart", why("mục tiêu thành phần nhưng nhiều nhóm/có giá trị âm nên dùng cột", "composition goal but many groups/negatives, so bars")
    if temporal and (nums or len(temporal) > 1) and n >= 2 and goal in (None, "trend", "comparison"):
        if nums:
            return "line chart", why("trục x là thời gian/thứ tự nên chọn biểu đồ đường", "time/ordered x axis, so a line chart")
    if goal == "relationship":
        if len(nums) >= 3:
            return "heatmap", why("mục tiêu quan hệ giữa ≥3 cột số", "relationship goal across ≥3 numeric columns")
        if len(nums) >= 2:
            return "scatter plot", why("mục tiêu quan hệ giữa 2 cột số", "relationship goal between two numeric columns")
    if goal == "distribution":
        if nums and cats:
            return "box plot", why("mục tiêu phân phối của cột số theo nhóm", "distribution of a numeric column by group")
        if nums:
            return "histogram", why("mục tiêu phân phối của một cột số", "distribution of one numeric column")
        if cats:
            return "count plot", why("mục tiêu phân phối của cột phân loại", "frequency distribution of a categorical column")
    if goal == "trend" and nums and len(nums) >= 2:
        return "line chart", why("mục tiêu xu hướng, cột đầu làm trục x", "trend goal, first column used as x")
    if labelish and nums:
        return "bar chart", why("so sánh giá trị số giữa các nhóm", "compare a numeric value across groups")
    if len(nums) >= 3:
        return "heatmap", why("nhiều cột số, không có nhãn: xem tương quan", "many numeric columns, no labels: correlation view")
    if len(nums) == 2:
        return "scatter plot", why("hai cột số: xem quan hệ", "two numeric columns: relationship")
    if len(nums) == 1:
        return "histogram", why("một cột số: xem phân phối", "one numeric column: distribution")
    if cats or temporal:
        return "count plot", why("chỉ có cột phân loại: đếm tần suất", "only categorical columns: frequency count")
    if f.measures:
        return "histogram", why("chỉ có cột số dạng thứ tự: xem phân phối", "only ordinal-number columns: distribution")
    raise ChartError(b.tr("Dữ liệu không phù hợp để vẽ biểu đồ.", "The data is not suitable for any chart."))


# ── Giao diện chính ────────────────────────────────────────────────────────
def _title(chart_type: str, meta: ChartMeta, vi: bool) -> str:
    x, y = meta.x and column_label(meta.x), meta.y and column_label(meta.y)
    if chart_type in ("bar chart", "pie chart") and x and y:
        return f"{y} {'theo' if vi else 'by'} {x}" if chart_type == "bar chart" else f"{'Tỷ trọng' if vi else 'Share of'} {y} {'theo' if vi else 'by'} {x}"
    if chart_type == "line chart" and x and y:
        return f"{y} {'theo' if vi else 'over'} {x}"
    if chart_type == "scatter plot" and x and y:
        return f"{y} {'so với' if vi else 'vs'} {x}"
    if chart_type in ("histogram", "kde plot") and y:
        return f"{'Phân phối của' if vi else 'Distribution of'} {y}"
    if chart_type in ("histogram", "kde plot") and x:
        return f"{'Phân phối của' if vi else 'Distribution of'} {x}"
    if chart_type in ("box plot", "violin plot") and y:
        return f"{'Phân phối của' if vi else 'Distribution of'} {y}" + (f" {'theo' if vi else 'by'} {column_label(meta.group)}" if meta.group else "")
    if chart_type == "count plot" and x:
        return f"{'Tần suất theo' if vi else 'Frequency by'} {x}"
    if chart_type == "heatmap":
        return "Ma trận tương quan" if vi and meta.group is None else ("Correlation matrix" if meta.group is None else (f"{y} {'theo' if vi else 'by'} {x} × {column_label(meta.group)}" if x and y else "Heatmap"))
    return chart_type


def finalize(spec_type: str, payload: Dict[str, Any], meta: ChartMeta, vi: bool) -> ChartSpec:
    meta.chart_type = spec_type
    meta.title = _title(spec_type, meta, vi)
    meta.xlabel = str(payload.get("xlabel") or "")
    meta.ylabel = str(payload.get("ylabel") or "")
    payload["title"] = meta.title
    return ChartSpec(spec_type, payload, meta)


def build_from_frame(
    df: pd.DataFrame,
    chart_type: Optional[str],
    goal: Optional[str],
    limits: ChartLimits,
    *,
    vi: bool = True,
    source: str = "query_result",
) -> ChartSpec:
    """DataFrame -> ChartSpec. `chart_type=None`: tự chọn; ngược lại giữ đúng loại, lỗi nếu dữ liệu không hợp."""
    if df is None or df.empty or df.shape[1] == 0:
        raise ChartError("Không có dữ liệu để vẽ biểu đồ." if vi else "There is no data to draw.")
    if chart_type is not None and chart_type not in CHART_TYPES:
        raise ChartError(f"Loại biểu đồ '{chart_type}' không nằm trong danh sách hỗ trợ: {', '.join(CHART_TYPES)}.")
    b = _Builder(df, limits, vi, goal)
    b.meta.source = source
    b.meta.requested_type = chart_type
    if bool(df.attrs.get("truncated")):
        b.meta.warn(b.tr("Kết quả truy vấn đã bị cắt ở giới hạn hệ thống; biểu đồ chỉ phản ánh phần dữ liệu nhận được.",
                         "The query result was truncated by system limits; the chart reflects only the rows received."))
    if chart_type is None:
        chosen, reason = auto_select(b)
    else:
        chosen = chart_type
        reason = b.tr("theo yêu cầu của người dùng", "as requested")
        if goal and chosen not in GOAL_TYPES.get(goal, ()):
            best = _GOAL_BEST[goal]
            b.meta.warn(b.tr(
                f"Loại '{chosen}' ít phù hợp với mục tiêu {_GOAL_VI[goal]} trong câu hỏi (gợi ý: {best}); vẫn vẽ theo yêu cầu.",
                f"'{chosen}' is a weaker fit for the {goal} goal in the question (suggested: {best}); drawn as requested.",
            ))
    b.meta.reason = reason
    payload = _BUILDERS[chosen](b)
    if not b.meta.method:
        b.meta.method = b.tr("dùng trực tiếp kết quả truy vấn", "used the result rows directly") if source == "query_result" else ""
    return finalize(chosen, payload, b.meta, vi)
