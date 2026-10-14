"""Giai đoạn 4: EDA tools xác định (overview, quality, numeric, categorical, correlation)."""
import dataclasses
import hashlib
import io
import sqlite3

import pytest

from backend.tools.eda_tools import (
    CategoricalSummaryTool,
    CorrelationTool,
    DataQualityTool,
    NumericSummaryTool,
    TableOverviewTool,
)

CSV = (
    "id,price,qty,cat,note,when\n"
    + "\n".join(
        f"{i},{i * 1.5},{'' if i % 4 == 0 else i % 7},{'abc'[i % 3]},,2024-01-{i % 28 + 1:02d}" for i in range(1, 121)
    )
    + "\n5,7.5,5,c,,2024-01-06\n"  # trùng dòng id=5
).encode()


@pytest.fixture
def ds(make_container):
    c = make_container()
    info = c.datasets.create("sales.csv", CSV)
    return c, info.dataset_id


def _run(c, cls, ds_id, **kw):
    return cls(c.datasets, c.settings).run(table="sales", dataset_id=ds_id, **kw)


def test_overview_counts_types_and_import_metadata(ds):
    c, d = ds
    p = _run(c, TableOverviewTool, d)
    assert p.status == "ok" and p.exact
    assert p.summary["n_rows"] == 121 and p.summary["n_columns"] == 6
    rows = {r["column"]: r for r in p.data.to_dict("records")}
    assert rows["id"]["observed"] == "numeric" and rows["id"]["distinct"] == 120
    assert rows["when"]["observed"] == "date-like text" and rows["when"]["import_inferred_type"] == "date"
    assert rows["note"]["observed"] == "empty" and rows["qty"]["nulls"] == 30
    assert rows["cat"]["distinct"] == 3
    assert "ISO" not in " ".join(p.warnings) and len(p.warnings) <= 1  # không có cảnh báo riêng cho từng cột
    assert rows["code" if "code" in rows else "qty"]["import_notes"] is not None  # ghi chú import nằm ở cột import_notes
    assert p.queries and all(q.lstrip().upper().startswith("SELECT") for q in p.queries)


def test_data_quality_nulls_blanks_and_duplicates(ds):
    c, d = ds
    p = _run(c, DataQualityTool, d)
    assert p.summary["duplicate_rows"] == 1 and p.summary["distinct_rows"] == 120
    assert set(p.summary["columns_with_nulls"]) == {"qty", "note"}
    qty = p.data.set_index("column").loc["qty"]
    assert qty["null_count"] == 30 and qty["null_pct"] == pytest.approx(24.79, abs=0.01)
    # định nghĩa trùng lặp theo cột chỉ định: 'cat' chỉ có 3 giá trị
    p2 = _run(c, DataQualityTool, d, columns=["cat"])
    assert p2.summary["duplicate_rows"] == 118 and p2.summary["duplicate_basis"] == "cat"


def test_blank_strings_counted_separately(make_container):
    c = make_container()
    path = c.datasets.root / "src.db"
    conn = sqlite3.connect(path)
    conn.executescript("CREATE TABLE t (a TEXT); INSERT INTO t VALUES ('x'),(''),('  '),(NULL);")
    conn.commit(); conn.close()
    d = c.datasets.create("t.db", path.read_bytes()).dataset_id
    p = DataQualityTool(c.datasets, c.settings).run(table="t", dataset_id=d)
    row = p.data.iloc[0]
    assert row["null_count"] == 1 and row["blank_count"] == 2


def test_numeric_summary_exact(ds):
    c, d = ds
    p = _run(c, NumericSummaryTool, d, columns=["price"])
    r = p.data.iloc[0]
    values = [i * 1.5 for i in range(1, 121)] + [7.5]
    import statistics

    assert r["count"] == 121 and r["min"] == 1.5 and r["max"] == 180.0
    assert r["mean"] == pytest.approx(statistics.mean(values), abs=1e-5)
    assert r["std"] == pytest.approx(statistics.stdev(values), abs=1e-5)
    assert r["median"] == pytest.approx(statistics.median(values), abs=1e-5)
    assert p.exact and not p.sampled and r["quantile_basis"] == "exact"


def test_numeric_summary_all_numeric_columns_and_nulls(ds):
    c, d = ds
    p = _run(c, NumericSummaryTool, d)
    assert list(p.data["column"]) == ["id", "price", "qty"]  # cột chữ/ngày/rỗng không được chọn
    assert p.data.set_index("column").loc["qty", "missing"] == 30


def test_numeric_sampling_is_flagged(make_container):
    c = make_container(eda_sample_rows=50)
    d = c.datasets.create("sales.csv", CSV).dataset_id
    p = _run(c, NumericSummaryTool, d, columns=["price"])
    r = p.data.iloc[0]
    assert p.sampled and not p.exact and p.sample_info["columns"]["price"]["rows_total"] == 121
    assert r["quantile_basis"].startswith("mẫu") and r["count"] == 121 and r["max"] == 180.0  # phần SQL vẫn chính xác
    assert any("mẫu" in w for w in p.warnings)


def test_numeric_not_applicable_and_mixed_types(make_container):
    c = make_container()
    path = c.datasets.root / "m.db"
    conn = sqlite3.connect(path)
    conn.executescript("CREATE TABLE t (a, b TEXT); INSERT INTO t VALUES (1,'x'),(2,'y'),('z','w'),(NULL,'v');")
    conn.commit(); conn.close()
    d = c.datasets.create("m.db", path.read_bytes()).dataset_id
    p = NumericSummaryTool(c.datasets, c.settings).run(table="t", dataset_id=d)
    r = p.data.iloc[0]
    assert r["count"] == 2 and r["non_numeric"] == 1 and r["mean"] == 1.5  # chỉ tính giá trị số
    assert any("hỗn hợp" in w for w in p.warnings)
    p2 = NumericSummaryTool(c.datasets, c.settings).run(table="t", columns=["b"], dataset_id=d)
    assert p2.status == "not_applicable"


def test_categorical_top_values_and_limits(make_container):
    c = make_container(eda_top_k=2)
    d = c.datasets.create("sales.csv", CSV).dataset_id
    p = _run(c, CategoricalSummaryTool, d, columns=["cat"])
    assert p.summary["columns"]["cat"]["distinct"] == 3
    top = p.data
    assert len(top) == 2 and top.iloc[0]["count"] >= top.iloc[1]["count"]
    assert top["count"].sum() <= 121 and p.truncated and p.exact  # cắt top-k không làm số liệu kém chính xác
    assert any("top 2" in w for w in p.warnings)


def test_correlation_pairwise_and_methods(make_container):
    c = make_container()
    csv = "a,b,c,d\n" + "\n".join(f"{i},{2 * i},{100 - i},{'' if i % 5 == 0 else i % 3}" for i in range(1, 51))
    d = c.datasets.create("x.csv", csv.encode()).dataset_id
    run = lambda **kw: CorrelationTool(c.datasets, c.settings).run(table="x", dataset_id=d, **kw)
    p = run()
    m = p.data.set_index("column")
    assert m.loc["a", "b"] == pytest.approx(1.0) and m.loc["a", "c"] == pytest.approx(-1.0)
    pair = {(x["a"], x["b"]): x for x in p.summary["top_pairs"] + [{"a": "a", "b": "d", **{}}]}
    assert p.summary["method"] == "pearson" and "pairwise" in p.definitions[0]
    s = run(method="spearman")
    assert s.summary["method"] == "spearman"
    assert run(method="kendall").status == "error"  # ngoài allowlist
    one = run(columns=["a"])
    assert one.status == "not_applicable"


def test_correlation_column_cap(make_container):
    c = make_container(eda_corr_max_columns=3)
    csv = "a,b,c,d,e\n" + "\n".join(f"{i},{i * i},{i % 4},{i % 7},{i % 5}" for i in range(1, 30))
    d = c.datasets.create("x.csv", csv.encode()).dataset_id
    t = CorrelationTool(c.datasets, c.settings)
    auto = t.run(table="x", dataset_id=d)
    assert len(auto.columns) == 3 and auto.truncated
    explicit = t.run(table="x", columns=list("abcde"), dataset_id=d)
    assert explicit.status == "not_applicable" and "tối đa 3" in explicit.error


def test_invalid_targets_and_dataset(ds):
    c, d = ds
    assert TableOverviewTool(c.datasets, c.settings).run(table="nope", dataset_id=d).status == "error"
    assert _run(c, NumericSummaryTool, d, columns=["zzz"]).status == "error"
    from backend.errors import DatasetNotFoundError

    with pytest.raises(DatasetNotFoundError):
        TableOverviewTool(c.datasets, c.settings).run(table="sales", dataset_id="0" * 32)


def test_empty_table_and_identifier_quoting(make_container):
    c = make_container()
    path = c.datasets.root / "e.db"
    conn = sqlite3.connect(path)
    conn.executescript('CREATE TABLE "we ird" ("a b" INTEGER, "c""d" TEXT);')
    conn.commit(); conn.close()
    d = c.datasets.create("e.db", path.read_bytes()).dataset_id
    for cls in (TableOverviewTool, DataQualityTool):
        p = cls(c.datasets, c.settings).run(table="we ird", dataset_id=d)
        assert p.status == "ok" and p.summary["n_rows"] == 0 and any("không có dòng" in w for w in p.warnings)
    assert NumericSummaryTool(c.datasets, c.settings).run(table="we ird", dataset_id=d).status == "not_applicable"


def test_tools_do_not_modify_database(ds):
    c, d = ds
    db = c.datasets.db_path(d)
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    for cls in (TableOverviewTool, DataQualityTool, NumericSummaryTool, CategoricalSummaryTool, CorrelationTool):
        _run(c, cls, d)
    assert hashlib.sha256(db.read_bytes()).hexdigest() == before


def test_time_budget_returns_partial_result(make_container):
    c = make_container(eda_timeout_seconds=0.0)
    d = c.datasets.create("sales.csv", CSV).dataset_id
    p = _run(c, TableOverviewTool, d)
    assert p.status == "error" and "thời gian" in p.error
