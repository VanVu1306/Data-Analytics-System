import pandas as pd

from backend.database import SQLiteRepository
from backend.tools import RenderChartTool, mock_sql, mock_viz_code


def test_schema_dict_and_ddl(demo_db):
    repo = SQLiteRepository(demo_db)
    schema = repo.get_schema_dict()
    assert set(schema) == {"customers", "orders", "products", "regions"}
    assert "order_date" in schema["orders"]

    ddl = repo.get_schema()
    assert "CREATE TABLE orders" in ddl
    assert "FOREIGN KEY (customer_id) REFERENCES customers(customer_id)" in ddl


def test_execute_ok_and_error(demo_db):
    repo = SQLiteRepository(demo_db)
    df, err = repo.execute("SELECT COUNT(*) AS n FROM orders")
    assert err is None and int(df["n"][0]) == 200

    df, err = repo.execute("SELECT * FROM table_that_does_not_exist")
    assert df is None and "no such table" in err


def test_exists_does_not_create_file(tmp_path):
    repo = SQLiteRepository(str(tmp_path / "missing.db"))
    assert not repo.exists()
    assert not (tmp_path / "missing.db").exists()


def test_mock_sql_keywords():
    assert "strftime" in mock_sql("Show monthly revenue in 2024")
    assert "regions" in mock_sql("Revenue by region")
    assert "customer_name" in mock_sql("Revenue by customer")
    assert mock_sql("hello") == "SELECT * FROM orders LIMIT 10"


def test_mock_viz_code_detects_chart_type():
    df = pd.DataFrame({"region_name": ["A", "B"], "total": [1.0, 2.0]})
    assert "ax.pie" in mock_viz_code(df, "share as pie chart")
    assert "ax.plot" in mock_viz_code(df, "trend over time")
    assert "ax.bar" in mock_viz_code(df, "revenue by region")


def test_render_chart_tool_returns_png():
    df = pd.DataFrame({"x": ["a", "b"], "y": [1, 2]})
    out = RenderChartTool(dpi=80).run(code=mock_viz_code(df, "bar"), df=df)
    assert out.error is None
    assert out.png.startswith(b"\x89PNG")


def test_render_chart_tool_reports_errors_and_empty_figure():
    df = pd.DataFrame({"x": [1], "y": [2]})
    tool = RenderChartTool()

    out = tool.run(code="raise ValueError('boom')", df=df)
    assert out.png is None and "boom" in out.error

    out = tool.run(code="x = 1", df=df)
    assert out.png is None and "no axes" in out.error

    out = tool.run(code="plt.plot([1, 2", df=df)
    assert out.png is None and "SyntaxError" in out.error
