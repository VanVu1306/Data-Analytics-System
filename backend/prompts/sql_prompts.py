"""
backend/prompts/sql_prompts.py
------------------------------
Prompt cho Stage 1 (DAIL-SQL): Code Representation + few-shot Q+SQL pairs.
Nội dung giữ nguyên như bản gốc modules/dail_sql.py.
"""

SQL_SYSTEM_PROMPT = (
    "You are an expert SQL assistant. "
    "Given a database schema and a question, write only the SQL query. "
    "Output ONLY the SQL query starting with SELECT, no explanation, no markdown."
)

# Few-shot examples cố định cho domain bán hàng (demo DB).
# Trong production thật nên dùng skeleton-similarity selection từ train set.
FEW_SHOT_EXAMPLES = """
/* Answer the following: How many customers are there? */
SELECT COUNT(*) FROM customers

/* Answer the following: What is the total revenue? */
SELECT SUM(revenue) FROM orders

/* Answer the following: List all product categories. */
SELECT DISTINCT category FROM products

/* Answer the following: What is the revenue by region? */
SELECT r.region_name, SUM(o.revenue) AS total_revenue
FROM orders o
JOIN customers c ON o.customer_id = c.customer_id
JOIN regions r ON c.region_id = r.region_id
GROUP BY r.region_name
ORDER BY total_revenue DESC

/* Answer the following: Show monthly revenue in 2024. */
SELECT strftime('%m', order_date) AS month, SUM(revenue) AS monthly_revenue
FROM orders
WHERE order_date LIKE '2024%'
GROUP BY month
ORDER BY month

/* Answer the following: Top 5 best-selling products. */
SELECT p.product_name, SUM(o.quantity) AS total_qty
FROM orders o
JOIN products p ON o.product_id = p.product_id
GROUP BY p.product_name
ORDER BY total_qty DESC
LIMIT 5
"""


def build_dail_prompt(question: str, schema: str) -> str:
    """
    Prompt theo chuẩn DAIL-SQL:
    1. Few-shot examples (DAIL Organization: Q + SQL pairs)
    2. Database schema (Code Representation: CREATE TABLE statements)
    3. Target question
    """
    return f"""/* Some example questions and corresponding SQL queries based on similar problems: */
{FEW_SHOT_EXAMPLES}

/* Given the following database schema: */
{schema}

/* Answer the following: {question} */
SELECT"""
