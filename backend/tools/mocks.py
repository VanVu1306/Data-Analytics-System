"""
backend/tools/mocks.py
----------------------
Mock SQL / mock viz code dùng khi không có API key, Ollama không chạy,
hoặc người dùng bật chế độ debug. Logic giữ nguyên như bản gốc.
"""
from __future__ import annotations

import pandas as pd

from backend.database import DatasetStore, quote_ident
from backend.tools.base import Tool


def mock_sql(question: str) -> str:
    """Mock SQL dựa trên keyword — chỉ dùng khi không có API key."""
    q = question.lower()

    if "month" in q or "tháng" in q:
        return """SELECT strftime('%m', order_date) AS month,
       SUM(revenue) AS monthly_revenue
FROM orders
WHERE order_date LIKE '2024%'
GROUP BY month
ORDER BY month"""

    elif "region" in q or "vùng" in q or "khu vực" in q:
        return """SELECT r.region_name, SUM(o.revenue) AS total_revenue
FROM orders o
JOIN customers c ON o.customer_id = c.customer_id
JOIN regions r ON c.region_id = r.region_id
GROUP BY r.region_name
ORDER BY total_revenue DESC"""

    elif "category" in q or "danh mục" in q or "product" in q.lower():
        return """SELECT p.category, SUM(o.revenue) AS total_revenue
FROM orders o
JOIN products p ON o.product_id = p.product_id
GROUP BY p.category
ORDER BY total_revenue DESC"""

    elif "customer" in q or "khách hàng" in q:
        return """SELECT c.customer_name, SUM(o.revenue) AS total_revenue
FROM orders o
JOIN customers c ON o.customer_id = c.customer_id
GROUP BY c.customer_name
ORDER BY total_revenue DESC"""

    elif "top" in q or "best" in q:
        return """SELECT p.product_name, SUM(o.quantity) AS total_qty
FROM orders o
JOIN products p ON o.product_id = p.product_id
GROUP BY p.product_name
ORDER BY total_qty DESC
LIMIT 5"""

    else:
        return "SELECT * FROM orders LIMIT 10"


def mock_viz_code(df: pd.DataFrame, question: str) -> str:
    """Sinh code matplotlib đơn giản dựa trên cấu trúc DataFrame."""
    num_cols = df.select_dtypes(include="number").columns.tolist()
    cat_cols = df.select_dtypes(exclude="number").columns.tolist()
    q = question.lower()

    if "pie" in q or "tỷ lệ" in q or "proportion" in q:
        chart_type = "pie"
    elif "line" in q or "trend" in q or "tháng" in q or "month" in q or "time" in q:
        chart_type = "line"
    elif "scatter" in q:
        chart_type = "scatter"
    else:
        chart_type = "bar"

    x_col = cat_cols[0] if cat_cols else (df.columns[0] if len(df.columns) > 0 else None)
    y_col = num_cols[0] if num_cols else (df.columns[1] if len(df.columns) > 1 else None)

    if x_col is None or y_col is None:
        return """import matplotlib.pyplot as plt
plt.style.use('ggplot')
fig, ax = plt.subplots(figsize=(8, 4))
ax.text(0.5, 0.5, 'Insufficient data', ha='center', va='center', transform=ax.transAxes)
ax.set_title('No suitable data')
plt.tight_layout()"""

    if chart_type == "bar":
        return f"""import matplotlib.pyplot as plt
plt.style.use('ggplot')
plt.figure(figsize=(10, 6))
ax = plt.gca()
bars = ax.bar(df['{x_col}'].astype(str), df['{y_col}'], color='steelblue', edgecolor='white')
ax.bar_label(bars, fmt='%.0f', padding=3, fontsize=9)
ax.set_xlabel('{x_col}', fontsize=11)
ax.set_ylabel('{y_col}', fontsize=11)
ax.set_title('{y_col} by {x_col}', fontsize=13, fontweight='bold')
plt.xticks(rotation=30, ha='right')
plt.tight_layout()"""

    elif chart_type == "line":
        return f"""import matplotlib.pyplot as plt
plt.style.use('seaborn-v0_8-whitegrid')
plt.figure(figsize=(10, 6))
ax = plt.gca()
ax.plot(df['{x_col}'].astype(str), df['{y_col}'], marker='o', linewidth=2,
        color='#2196F3', markersize=6, markerfacecolor='white', markeredgewidth=2)
ax.fill_between(range(len(df)), df['{y_col}'], alpha=0.1, color='#2196F3')
ax.set_xlabel('{x_col}', fontsize=11)
ax.set_ylabel('{y_col}', fontsize=11)
ax.set_title('{y_col} over {x_col}', fontsize=13, fontweight='bold')
ax.set_xticks(range(len(df)))
ax.set_xticklabels(df['{x_col}'].astype(str), rotation=30, ha='right')
plt.tight_layout()"""

    elif chart_type == "pie":
        return f"""import matplotlib.pyplot as plt
plt.style.use('ggplot')
plt.figure(figsize=(8, 8))
ax = plt.gca()
wedges, texts, autotexts = ax.pie(
    df['{y_col}'],
    labels=df['{x_col}'].astype(str),
    autopct='%1.1f%%',
    startangle=140,
    pctdistance=0.82,
)
for t in autotexts:
    t.set_fontsize(9)
ax.set_title('{y_col} by {x_col}', fontsize=13, fontweight='bold')
plt.tight_layout()"""

    elif chart_type == "scatter" and len(num_cols) >= 2:
        return f"""import matplotlib.pyplot as plt
plt.style.use('seaborn-v0_8-whitegrid')
plt.figure(figsize=(8, 6))
ax = plt.gca()
ax.scatter(df['{num_cols[0]}'], df['{num_cols[1]}'],
           alpha=0.7, s=60, color='coral', edgecolors='white')
ax.set_xlabel('{num_cols[0]}', fontsize=11)
ax.set_ylabel('{num_cols[1]}', fontsize=11)
ax.set_title('{num_cols[0]} vs {num_cols[1]}', fontsize=13, fontweight='bold')
plt.tight_layout()"""

    else:
        return f"""import matplotlib.pyplot as plt
plt.style.use('ggplot')
plt.figure(figsize=(10, 6))
ax = plt.gca()
ax.bar(df['{x_col}'].astype(str), df['{y_col}'], color='steelblue')
ax.set_title('Data Visualization', fontsize=13, fontweight='bold')
plt.tight_layout()"""


class MockSQLTool(Tool):
    name = "mock_sql"
    description = (
        "Sinh SQL mẫu (không gọi LLM): theo keyword cho demo DB; với dataset upload chỉ xem trước bảng đầu tiên."
    )

    def __init__(self, store: DatasetStore | None = None):
        self._store = store

    def run(self, question: str, dataset_id: str | None = None) -> str:
        if dataset_id is None or self._store is None:
            return mock_sql(question)
        tables = self._store.repository(dataset_id).get_schema_dict()
        if not tables:
            return "SELECT 1"
        return f"SELECT * FROM {quote_ident(next(iter(tables)))} LIMIT 10"


class MockVizCodeTool(Tool):
    name = "mock_viz_code"
    description = "Sinh code matplotlib mẫu theo cấu trúc DataFrame (không gọi LLM)."

    def run(self, df: pd.DataFrame, question: str) -> str:
        return mock_viz_code(df, question)
