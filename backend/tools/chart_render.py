"""
backend/tools/chart_render.py
-----------------------------
Bộ vẽ XÁC ĐỊNH: ChartSpec -> PNG bằng matplotlib. Không `exec`, không code do LLM sinh;
chỉ các loại trong allowlist `CHART_TYPES` được vẽ.
"""
from __future__ import annotations

import io
from typing import Callable, Dict, Optional

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.ticker import FuncFormatter  # noqa: E402

from backend.tools.base import Tool  # noqa: E402
from backend.tools.chart_select import ChartSpec  # noqa: E402
from backend.tools.intent import CHART_TYPES  # noqa: E402
from backend.tools.viz_tools import _PLOT_LOCK, ChartRender  # noqa: E402

_COLOR = "#2f6fdb"
_PALETTE = ["#2f6fdb", "#e8743b", "#19a979", "#945ecf", "#ed4a7b", "#13a4b4", "#a6a6a6", "#e6b800"]


def _num(v: float, _pos=None) -> str:
    if abs(v) >= 1000 or float(v).is_integer():
        return f"{v:,.0f}"
    return f"{v:,.3g}"


def _fmt_axis(ax, which: str = "y") -> None:
    (ax.yaxis if which == "y" else ax.xaxis).set_major_formatter(FuncFormatter(_num))


def _bar(ax, p: dict) -> None:
    labels, values = p["labels"], p["values"]
    horizontal = len(labels) > 10 or max((len(str(l)) for l in labels), default=0) > 12
    if horizontal:
        ax.barh(range(len(labels)), values, color=_COLOR)
        ax.set_yticks(range(len(labels)))
        ax.set_yticklabels(labels)
        ax.invert_yaxis()
        ax.set_xlabel(p.get("ylabel", ""))
        ax.set_ylabel(p.get("xlabel", ""))
        _fmt_axis(ax, "x")
    else:
        ax.bar(range(len(labels)), values, color=_COLOR)
        ax.set_xticks(range(len(labels)))
        ax.set_xticklabels(labels, rotation=30 if len(labels) > 4 else 0, ha="right" if len(labels) > 4 else "center")
        ax.set_xlabel(p.get("xlabel", ""))
        ax.set_ylabel(p.get("ylabel", ""))
        _fmt_axis(ax, "y")


def _pie(ax, p: dict) -> None:
    ax.pie(p["values"], labels=p["labels"], autopct="%1.1f%%", startangle=90, colors=_PALETTE * 2,
           wedgeprops={"edgecolor": "white"}, textprops={"fontsize": 9})
    ax.axis("equal")


def _line(ax, p: dict) -> None:
    many = max(len(s["x"]) for s in p["series"]) <= 40
    for i, s in enumerate(p["series"]):
        ax.plot(s["x"], s["y"], marker="o" if many else None, markersize=4, label=s["name"], color=_PALETTE[i % len(_PALETTE)])
    if len(p["series"]) > 1:
        ax.legend(fontsize=8)
    ax.set_xlabel(p.get("xlabel", ""))
    ax.set_ylabel(p.get("ylabel", ""))
    _fmt_axis(ax, "y")
    if p.get("temporal"):
        ax.figure.autofmt_xdate()
    else:
        n = len(p["series"][0]["x"])
        if n > 12 and not isinstance(p["series"][0]["x"][0], (int, float)):
            ax.tick_params(axis="x", rotation=45)


def _scatter(ax, p: dict) -> None:
    if p.get("hue"):
        for i, g in enumerate(dict.fromkeys(p["hue"])):
            idx = [k for k, h in enumerate(p["hue"]) if h == g]
            ax.scatter([p["x"][k] for k in idx], [p["y"][k] for k in idx], s=18, alpha=0.7, label=g, color=_PALETTE[i % len(_PALETTE)])
        ax.legend(fontsize=8)
    else:
        ax.scatter(p["x"], p["y"], s=18, alpha=0.7, color=_COLOR)
    ax.set_xlabel(p.get("xlabel", ""))
    ax.set_ylabel(p.get("ylabel", ""))
    _fmt_axis(ax, "x")
    _fmt_axis(ax, "y")


def _hist(ax, p: dict) -> None:
    edges, counts = p["edges"], p["counts"]
    widths = [edges[i + 1] - edges[i] for i in range(len(counts))]
    ax.bar(edges[:-1], counts, width=widths, align="edge", color=_COLOR, edgecolor="white")
    ax.set_xlabel(p.get("xlabel", ""))
    ax.set_ylabel(p.get("ylabel", ""))
    _fmt_axis(ax, "x")
    _fmt_axis(ax, "y")


def _kde(ax, p: dict) -> None:
    for i, s in enumerate(p["series"]):
        c = _PALETTE[i % len(_PALETTE)]
        ax.plot(s["x"], s["y"], color=c, label=s["name"])
        ax.fill_between(s["x"], s["y"], alpha=0.2, color=c)
    if len(p["series"]) > 1:
        ax.legend(fontsize=8)
    ax.set_ylim(bottom=0)
    ax.set_xlabel(p.get("xlabel", ""))
    ax.set_ylabel(p.get("ylabel", ""))
    _fmt_axis(ax, "x")


def _box_axes(ax, p: dict) -> list:
    labels = [g["label"] for g in p["groups"]] if "groups" in p else [s["label"] for s in p["stats"]]
    ax.set_xticks(range(1, len(labels) + 1))
    ax.set_xticklabels(labels, rotation=30 if len(labels) > 4 else 0, ha="right" if len(labels) > 4 else "center")
    ax.set_xlabel(p.get("xlabel", ""))
    ax.set_ylabel(p.get("ylabel", ""))
    _fmt_axis(ax, "y")
    return labels


def _box(ax, p: dict) -> None:
    if "stats" in p:  # từ thống kê EDA (không cần dữ liệu thô)
        stats = [
            {"label": s["label"], "q1": s["q1"], "med": s["med"], "q3": s["q3"], "whislo": s["whislo"],
             "whishi": s["whishi"], "fliers": s.get("fliers", [])}
            for s in p["stats"]
        ]
        bp = ax.bxp(stats, patch_artist=True, showfliers=True)
    else:
        bp = ax.boxplot([g["values"] for g in p["groups"]], patch_artist=True, showfliers=True)
    for patch in bp["boxes"]:
        patch.set_facecolor("#bcd3f7")
        patch.set_edgecolor(_COLOR)
    for med in bp["medians"]:
        med.set_color("#c0392b")
    _box_axes(ax, p)


def _violin(ax, p: dict) -> None:
    parts = ax.violinplot([g["values"] for g in p["groups"]], showmedians=True, showextrema=True)
    for body in parts["bodies"]:
        body.set_facecolor("#bcd3f7")
        body.set_edgecolor(_COLOR)
        body.set_alpha(0.9)
    _box_axes(ax, p)


def _heatmap(ax, p: dict) -> None:
    import numpy as np

    m = np.array([[np.nan if v is None else v for v in row] for row in p["matrix"]], dtype=float)
    if p.get("diverging"):
        im = ax.imshow(m, cmap="RdBu_r", vmin=-1, vmax=1, aspect="auto")
    else:
        im = ax.imshow(m, cmap="YlGnBu", aspect="auto")
    ax.set_xticks(range(len(p["xlabels"])))
    ax.set_xticklabels(p["xlabels"], rotation=45, ha="right")
    ax.set_yticks(range(len(p["ylabels"])))
    ax.set_yticklabels(p["ylabels"])
    cb = ax.figure.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
    if p.get("cbar"):
        cb.set_label(p["cbar"])
    if m.size <= 144:
        lo, hi = (-1, 1) if p.get("diverging") else (np.nanmin(m), np.nanmax(m))
        for i in range(m.shape[0]):
            for j in range(m.shape[1]):
                if not np.isnan(m[i, j]):
                    dark = hi != lo and abs((m[i, j] - (lo + hi) / 2) / (hi - lo)) > 0.3
                    ax.text(j, i, _num(m[i, j]) if not p.get("diverging") else f"{m[i, j]:.2f}",
                            ha="center", va="center", fontsize=8, color="white" if dark else "black")
    if p.get("xlabel"):
        ax.set_xlabel(p["xlabel"])
    if p.get("ylabel"):
        ax.set_ylabel(p["ylabel"])


_DRAW: Dict[str, Callable] = {
    "bar chart": _bar, "count plot": _bar, "pie chart": _pie, "line chart": _line, "scatter plot": _scatter,
    "histogram": _hist, "kde plot": _kde, "box plot": _box, "violin plot": _violin, "heatmap": _heatmap,
}
assert set(_DRAW) == set(CHART_TYPES)


def render_png(spec: ChartSpec, dpi: int = 100) -> bytes:
    draw = _DRAW.get(spec.chart_type)
    if draw is None:  # allowlist: không bao giờ vẽ loại lạ
        raise ValueError(f"Loại biểu đồ không được hỗ trợ: {spec.chart_type}")
    with _PLOT_LOCK:
        plt.close("all")
        fig, ax = plt.subplots(figsize=(8, 5) if spec.chart_type in ("heatmap", "pie chart") else (8, 4.8))
        try:
            draw(ax, spec.payload)
            ax.set_title(spec.meta.title, fontsize=12)
            m = spec.meta
            if m.rows_total and m.rows_used is not None and m.rows_used < m.rows_total and m.source != "sql_aggregate":
                fig.text(0.01, -0.01, f"n = {m.rows_used:,}/{m.rows_total:,}", fontsize=7, color="#777", ha="left", va="top")
            buf = io.BytesIO()
            fig.savefig(buf, format="png", dpi=dpi, bbox_inches="tight")
            return buf.getvalue()
        finally:
            plt.close("all")


class RenderChartSpecTool(Tool):
    name = "render_chart_spec"
    description = "Vẽ ChartSpec (allowlist loại biểu đồ) thành PNG bằng matplotlib; không chạy code do LLM sinh."

    def __init__(self, dpi: int = 100):
        self._dpi = dpi

    def run(self, spec: ChartSpec) -> ChartRender:  # type: ignore[override]
        try:
            return ChartRender(png=render_png(spec, self._dpi), error=None)
        except Exception as e:
            return ChartRender(png=None, error=f"{type(e).__name__}: {e}")
