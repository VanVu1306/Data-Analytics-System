"""Giai đoạn 5: chọn biểu đồ theo mục tiêu + kiểu dữ liệu, bộ vẽ xác định, biểu đồ từ kết quả EDA."""
import base64
import dataclasses

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from backend.container import get_container
from backend.main import create_app
from backend.tools import (
    CHART_TYPES, ChartError, ChartLimits, build_from_frame, detect_chart_goal, resolve_chart_plan, render_png,
)
from backend.tools.chart_select import ChartMeta, ChartSpec, infer_unit

L = ChartLimits()
RNG = np.random.default_rng(7)
PNG = b"\x89PNG"


def spec_for(df, ctype=None, goal=None, limits=L, vi=True):
    spec = build_from_frame(df, ctype, goal, limits, vi=vi)
    assert render_png(spec).startswith(PNG)  # luôn vẽ được ra PNG hợp lệ
    return spec


@pytest.fixture
def frames():
    return {
        "cat_num": pd.DataFrame({"region": ["N", "S", "E", "W"], "revenue_usd": [50.0, 30.0, 90.0, 10.0]}),
        "time": pd.DataFrame({"month": pd.date_range("2024-01-01", periods=12, freq="MS"), "sales": RNG.random(12) * 100}),
        "nums": pd.DataFrame({"a": RNG.normal(size=80), "b": RNG.normal(size=80), "c": RNG.normal(size=80)}),
        "grouped": pd.DataFrame({"g": ["x", "y"] * 30, "v": RNG.normal(size=60)}),
        "only_cat": pd.DataFrame({"city": ["HN", "HCM", "HN", "DN", "HN", "HCM"]}),
    }


# ── Mỗi loại biểu đồ ───────────────────────────────────────────────────────
def test_every_chart_type_renders(frames):
    cases = {
        "bar chart": frames["cat_num"], "line chart": frames["time"], "pie chart": frames["cat_num"],
        "scatter plot": frames["nums"], "histogram": frames["nums"][["a"]], "kde plot": frames["nums"][["a"]],
        "box plot": frames["grouped"], "violin plot": frames["grouped"], "heatmap": frames["nums"],
        "count plot": frames["only_cat"],
    }
    assert set(cases) == set(CHART_TYPES)
    for ctype, df in cases.items():
        s = spec_for(df, ctype)
        assert s.chart_type == ctype and s.meta.title and s.meta.rows_total == len(df)


def test_explicit_type_is_never_silently_replaced(frames):
    s = spec_for(frames["cat_num"], "pie chart")
    assert s.chart_type == "pie chart" and s.meta.requested_type == "pie chart"
    with pytest.raises(ChartError) as e:
        build_from_frame(frames["cat_num"], "scatter plot", None, L)
    assert "2 cột số" in str(e.value) and e.value.suggestion  # lỗi + gợi ý, không đổi sang loại khác


# ── Chọn theo mục tiêu + kiểu dữ liệu ─────────────────────────────────────
@pytest.mark.parametrize("name,goal,expected", [
    ("cat_num", None, "bar chart"),
    ("cat_num", "comparison", "bar chart"),
    ("cat_num", "composition", "pie chart"),
    ("time", None, "line chart"),
    ("time", "trend", "line chart"),
    ("nums", "relationship", "heatmap"),
    ("nums", None, "heatmap"),
    ("grouped", "distribution", "box plot"),
    ("only_cat", None, "count plot"),
])
def test_auto_selection_by_goal_and_dtype(frames, name, goal, expected):
    s = spec_for(frames[name], None, goal)
    assert s.chart_type == expected and s.meta.requested_type is None and s.meta.reason


def test_auto_picks_scatter_histogram_and_year_line():
    two = pd.DataFrame({"x": RNG.random(30), "y": RNG.random(30)})
    assert spec_for(two).chart_type == "scatter plot"
    one = pd.DataFrame({"x": RNG.random(30)})
    assert spec_for(one).chart_type == "histogram"
    assert spec_for(one, None, "distribution").chart_type == "histogram"
    yearly = pd.DataFrame({"year": [2021, 2022, 2023], "total": [10, 12, 9]})
    assert spec_for(yearly).chart_type == "line chart"  # cột year (số) được hiểu là thời gian


def test_composition_with_many_groups_or_negatives_falls_back_to_bar():
    many = pd.DataFrame({"k": [f"g{i}" for i in range(15)], "v": range(1, 16)})
    assert spec_for(many, None, "composition").chart_type == "bar chart"
    neg = pd.DataFrame({"k": ["a", "b"], "v": [5, -2]})
    assert spec_for(neg, None, "composition").chart_type == "bar chart"


def test_goal_detection_and_intent_types():
    assert detect_chart_goal("so sánh doanh thu giữa các vùng") == "comparison"
    assert detect_chart_goal("xu hướng doanh thu theo tháng") == "trend"
    assert detect_chart_goal("phân phối của giá") == "distribution"
    assert detect_chart_goal("mối quan hệ giữa giá và số lượng") == "relationship"
    assert detect_chart_goal("tỷ lệ đơn hàng theo vùng") == "composition"
    assert detect_chart_goal("liệt kê khách hàng") is None
    for q, t in [("vẽ histogram của giá", "histogram"), ("vẽ biểu đồ mật độ KDE", "kde plot"),
                 ("vẽ boxplot", "box plot"), ("vẽ violin plot", "violin plot"),
                 ("vẽ heatmap tương quan", "heatmap"), ("vẽ count plot theo vùng", "count plot"),
                 ("vẽ biểu đồ tròn", "pie chart"), ("vẽ biểu đồ phân tán", "scatter plot")]:
        assert resolve_chart_plan(q, "detect").chart_type == t, q
    assert resolve_chart_plan("vẽ biểu đồ xu hướng doanh thu", "detect").goal == "trend"
    assert resolve_chart_plan("x", "box plot").chart_type == "box plot"  # tùy chọn bên cạnh


# ── Dữ liệu thiếu / quá ít / không phù hợp ─────────────────────────────────
def test_missing_and_empty_data():
    with pytest.raises(ChartError):
        build_from_frame(pd.DataFrame({"a": []}), None, None, L)
    with pytest.raises(ChartError):
        build_from_frame(pd.DataFrame({"a": [None, None], "b": [None, None]}), "histogram", None, L)
    holes = pd.DataFrame({"k": ["a", "b", "c"], "v": [1.0, None, 3.0]})
    s = spec_for(holes, "bar chart")
    assert s.payload["labels"] == ["c", "a"] and s.meta.rows_used == 2  # dòng thiếu giá trị bị bỏ, được ghi nhận


def test_too_few_points():
    one_row = pd.DataFrame({"month": pd.to_datetime(["2024-01-01"]), "v": [1.0]})
    with pytest.raises(ChartError) as e:
        build_from_frame(one_row, "line chart", None, L)
    assert "2 điểm" in str(e.value)
    assert spec_for(one_row, None).chart_type == "bar chart"  # tự chọn vẫn ra biểu đồ hợp lệ
    with pytest.raises(ChartError):
        build_from_frame(pd.DataFrame({"x": [1.0], "y": [2.0]}), "scatter plot", None, L)
    few = pd.DataFrame({"x": [1.0, 2.0, 3.0]})
    assert any("kém đại diện" in w for w in spec_for(few, "histogram").meta.warnings)
    with pytest.raises(ChartError):
        build_from_frame(pd.DataFrame({"x": [1.0, 2.0, 3.0]}), "kde plot", None, L)  # < 5 giá trị
    with pytest.raises(ChartError):
        build_from_frame(pd.DataFrame({"x": [4.0] * 20}), "histogram", None, L)  # hằng số


def test_unsuitable_columns(frames):
    neg = pd.DataFrame({"k": ["a", "b"], "v": [5.0, -2.0]})
    with pytest.raises(ChartError) as e:
        build_from_frame(neg, "pie chart", None, L)
    assert "âm" in str(e.value)
    with pytest.raises(ChartError):
        build_from_frame(frames["only_cat"], "histogram", None, L)  # không có cột số
    with pytest.raises(ChartError):
        build_from_frame(frames["cat_num"], "heatmap", None, L)  # 1 cột số
    with pytest.raises(ChartError):
        build_from_frame(frames["nums"][["a"]], "bar chart", None, L)  # thiếu cột nhãn
    with pytest.raises(ChartError):
        build_from_frame(frames["nums"], "count plot", None, L)  # không có cột phân loại
    with pytest.raises(ChartError):
        build_from_frame(pd.DataFrame({"k": ["a"], "v": [1]}), "not a chart", None, L)  # ngoài allowlist
    with pytest.raises(ValueError):
        render_png(ChartSpec("not a chart", {}, ChartMeta()))


# ── Dữ liệu lớn: giới hạn + ghi nhận ───────────────────────────────────────
def test_large_data_is_bounded_and_reported():
    n = 10_000
    big = pd.DataFrame({"x": RNG.random(n), "y": RNG.random(n)})
    s = spec_for(big, "scatter plot")
    assert len(s.payload["x"]) == L.max_points and s.meta.rows_total == n and s.meta.rows_used == L.max_points
    assert "mẫu" in s.meta.method and s.meta.warnings
    cats = pd.DataFrame({"k": [f"c{i}" for i in range(100)], "v": range(100)})
    s = spec_for(cats, "bar chart")
    assert len(s.payload["labels"]) == L.max_categories and any("100" in w for w in s.meta.warnings)
    pie = pd.DataFrame({"k": [f"c{i}" for i in range(20)], "v": range(1, 21)})
    s = spec_for(pie, "pie chart")
    assert len(s.payload["labels"]) == L.pie_max_slices and s.payload["labels"][-1] == "Khác"
    assert pytest.approx(sum(s.payload["values"])) == sum(range(1, 21))  # gộp "Khác" không làm mất tổng
    line = pd.DataFrame({"t": range(6000), "v": RNG.random(6000), "id": range(6000)})
    line["t"] = pd.date_range("2000-01-01", periods=6000, freq="h")
    s = spec_for(line[["t", "v"]], "line chart")
    assert len(s.payload["series"][0]["x"]) <= L.max_points
    truncated = pd.DataFrame({"k": list("ab"), "v": [1, 2]})
    truncated.attrs["truncated"] = True
    assert any("cắt" in w for w in spec_for(truncated, "bar chart").meta.warnings)


# ── Yêu cầu mâu thuẫn ──────────────────────────────────────────────────────
def test_type_vs_goal_conflict_warns_but_follows_explicit_type(frames):
    s = spec_for(frames["cat_num"], "pie chart", "trend")
    assert s.chart_type == "pie chart" and any("ít phù hợp" in w for w in s.meta.warnings)
    s = spec_for(frames["cat_num"], "bar chart", "comparison")
    assert not any("ít phù hợp" in w for w in s.meta.warnings)


# ── Nhãn, tiêu đề, đơn vị ──────────────────────────────────────────────────
def test_titles_labels_and_units(frames):
    s = spec_for(frames["cat_num"], "bar chart")
    assert s.meta.title == "revenue (USD) theo region" and s.meta.ylabel == "revenue (USD)" and s.meta.xlabel == "region"
    assert s.meta.units == {"revenue_usd": "USD"}
    assert infer_unit("price (VND)") == "VND" and infer_unit("growth_pct") == "%" and infer_unit("price") is None
    en = spec_for(frames["cat_num"], "bar chart", vi=False)
    assert en.meta.title == "revenue (USD) by region"
    assert spec_for(frames["time"], None).meta.title == "sales theo month"


def test_heatmap_ignores_constant_columns_and_values_in_range(frames):
    df = frames["nums"].assign(const=1.0)
    s = spec_for(df, "heatmap")
    assert "const" not in s.payload["xlabels"] and any("const" in w for w in s.meta.warnings)
    flat = [v for row in s.payload["matrix"] for v in row if v is not None]
    assert all(-1.0001 <= v <= 1.0001 for v in flat)
    cross = pd.DataFrame({"r": list("aabb"), "c": list("xyxy"), "v": [1, 2, 3, 4]})
    assert spec_for(cross, "heatmap").payload["xlabels"] == ["x", "y"]


# ── API: biểu đồ từ kết quả truy vấn / EDA ─────────────────────────────────
@pytest.fixture
def env(make_container):
    def _make(**kw):
        c = make_container(**kw)
        app = create_app()
        app.dependency_overrides[get_container] = lambda: c
        return TestClient(app), c
    return _make


def _csv(n=300):
    rng = np.random.default_rng(3)
    lines = ["grp,price_usd,qty,score"]
    for i in range(n):
        lines.append(f"{'ABC'[i % 3]},{round(float(rng.normal(50, 10)), 2)},{int(rng.integers(1, 20))},{round(float(rng.normal(0, 1)), 3)}")
    return "\n".join(lines).encode()


def _upload(client):
    r = client.post("/api/datasets", files={"file": ("data.csv", _csv())})
    assert r.status_code == 201, r.text
    return r.json()["dataset_id"]


def _q(client, question, ds, **kw):
    r = client.post("/api/query", json={"question": question, "dataset_id": ds, "use_mock_sql": True, **kw})
    assert r.status_code == 200, r.text
    return r.json()


def _png(d):
    return base64.b64decode(d["chart"]["png_base64"])


def test_query_result_chart_in_spec_mode_has_meta_and_no_code(env):
    client, _ = env()
    ds = _upload(client)
    d = _q(client, "Cho tôi 20 dòng đầu của bảng data", ds, chart_hint="scatter plot")
    ch = d["chart"]
    assert d["status"] == "ok" and d["table"] and ch["status"] == "ok" and _png(d).startswith(PNG)
    assert ch["source"] == "spec" and ch["code"] is None and ch["chart_type"] == "scatter plot"
    m = ch["meta"]
    assert m["source"] == "query_result" and m["rows_used"] <= m["rows_total"] and m["title"] and m["xlabel"] and m["ylabel"]
    assert (client.get("/api/health").json())["viz_mode"] == "spec"


def test_option_priority_none_overrides_prompt_and_auto_overrides_none(env):
    client, _ = env()
    ds = _upload(client)
    d = _q(client, "Vẽ biểu đồ 20 dòng đầu của bảng data", ds, chart_hint="none")
    assert d["chart"]["status"] == "not_requested" and d["table"] and d["answer"]
    d = _q(client, "Cho tôi 20 dòng đầu của bảng data", ds, chart_hint="auto")
    assert d["chart"]["status"] == "ok" and d["chart"]["requested_by"] == "option" and d["chart"]["meta"]["requested_type"] is None


def test_chart_failure_keeps_answer_and_table(env, monkeypatch):
    client, c = env()
    ds = _upload(client)
    d = _q(client, "Cho tôi 20 dòng đầu của bảng data", ds, chart_hint="count plot")
    assert d["chart"]["status"] == "ok"  # grp là cột phân loại
    tool = c.tools.get("build_chart_spec")
    # (1) dữ liệu không phù hợp -> ChartError; (2) lỗi bất ngờ -> Exception: cả hai chỉ làm hỏng biểu đồ
    for exc in (ChartError("Heatmap không phù hợp.", "Hãy thử bar chart."), RuntimeError("boom")):
        def fail(**kw):
            raise exc
        monkeypatch.setattr(tool, "run", fail)
        d = _q(client, "Cho tôi 20 dòng đầu của bảng data", ds, chart_hint="heatmap")
        assert d["status"] == "ok" and d["table"] and d["answer"]["text"] and d["evidence"]["sql"]
        assert d["chart"]["status"] == "error" and d["chart"]["png_base64"] is None
        assert str(exc) in d["chart"]["error"] or type(exc).__name__ in d["chart"]["error"]


def test_eda_correlation_to_heatmap(env):
    client, _ = env()
    ds = _upload(client)
    d = _q(client, "Ma trận tương quan giữa các cột số của bảng data", ds, chart_hint="heatmap")
    assert d["eda"][0]["operation"] == "correlation" and d["eda"][0]["status"] == "ok"
    ch = d["chart"]
    assert ch["status"] == "ok" and ch["chart_type"] == "heatmap" and _png(d).startswith(PNG)
    assert ch["meta"]["source"] == "eda_payload" and ch["meta"]["rows_total"] == 300
    assert d["answer"]["text"] and d["eda"][0]["table_data"]  # EDA vẫn nguyên vẹn


def test_eda_frequency_to_count_plot_and_auto(env):
    client, _ = env()
    ds = _upload(client)
    d = _q(client, "Tần suất giá trị của cột grp trong bảng data", ds, chart_hint="count plot")
    assert d["eda"][0]["operation"] == "categorical_summary" and d["chart"]["status"] == "ok"
    assert d["chart"]["meta"]["source"] == "eda_payload" and d["chart"]["chart_type"] == "count plot"
    d = _q(client, "Tần suất giá trị của cột grp trong bảng data", ds, chart_hint="auto")
    assert d["chart"]["status"] == "ok" and d["chart"]["chart_type"] == "count plot"


def test_eda_numeric_summary_to_box_histogram_kde_violin(env):
    client, _ = env()
    ds = _upload(client)
    q = "Thống kê mô tả cột price_usd của bảng data"
    d = _q(client, q, ds, chart_hint="box plot")
    assert d["eda"][0]["operation"] == "numeric_summary" and d["chart"]["status"] == "ok"
    assert d["chart"]["meta"]["source"] == "eda_payload" and _png(d).startswith(PNG)

    d = _q(client, q, ds, chart_hint="histogram")
    m = d["chart"]["meta"]
    assert d["chart"]["status"] == "ok" and m["source"] == "sql_aggregate"
    assert m["rows_total"] == 300 and m["rows_used"] == 300  # gom bằng SQL: chính xác, mọi dòng đều được đếm

    for t in ("kde plot", "violin plot"):
        d = _q(client, q, ds, chart_hint=t)
        assert d["chart"]["status"] == "ok" and d["chart"]["meta"]["source"] == "table_sample", t
        assert d["chart"]["meta"]["rows_used"] == 300


def test_large_table_chart_uses_bounded_sample_and_reports_it(env):
    client, _ = env(chart_sample_rows=50)
    ds = _upload(client)
    d = _q(client, "Thống kê mô tả cột price_usd của bảng data", ds, chart_hint="kde plot")
    m = d["chart"]["meta"]
    assert d["chart"]["status"] == "ok" and m["source"] == "table_sample"
    assert m["rows_total"] == 300 and 0 < m["rows_used"] <= 51 and "mẫu" in m["method"]
    assert any("mẫu" in w for w in m["warnings"])


def test_eda_chart_errors_keep_eda_results(env):
    client, _ = env()
    ds = _upload(client)
    d = _q(client, "Tổng quan bảng data", ds, chart_hint="auto")
    assert d["status"] == "ok" and d["eda"] and d["answer"]["text"]
    assert d["chart"]["status"] == "error" and d["chart"]["png_base64"] is None
    d = _q(client, "Ma trận tương quan giữa các cột số của bảng data", ds, chart_hint="pie chart")
    assert d["eda"][0]["status"] == "ok" and d["chart"]["status"] == "error" and d["answer"]["text"]


def test_combined_query_and_eda_prefers_query_result(env):
    client, _ = env()
    ds = _upload(client)
    d = _q(client, "Tổng qty theo grp và thống kê mô tả cột price_usd", ds, chart_hint="auto")
    assert d["table"] and d["eda"]
    assert d["chart"]["status"] == "ok" and d["chart"]["meta"]["source"] == "query_result"


def test_code_mode_is_still_available_and_spec_mode_never_execs(env, monkeypatch):
    import backend.tools.viz_tools as vt

    def boom(*a, **k):
        raise AssertionError("exec/LLM code path must not run in spec mode")

    monkeypatch.setattr(vt, "execute_viz_code", boom)
    client, c = env()
    assert c.settings.viz_mode == "spec"
    ds = _upload(client)
    d = _q(client, "Cho tôi 20 dòng đầu của bảng data", ds, chart_hint="bar chart", use_mock_viz=False)
    assert d["chart"]["status"] in ("ok", "error") and d["chart"]["code"] is None
    client2, c2 = env(viz_mode="code")
    assert client2.get("/api/health").json()["viz_mode"] == "code"
