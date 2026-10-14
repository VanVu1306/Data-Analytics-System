"""
backend/prompts/sql_prompts.py
------------------------------
Prompt cho Stage 1 (DAIL-SQL): Code Representation + few-shot Q+SQL pairs.
Nội dung giữ nguyên như bản gốc modules/dail_sql.py.
"""

SQL_SYSTEM_PROMPT = (
    "You are an expert SQL assistant. "
    "Given a database schema and a question, write only the SQL query. "
    "Output ONLY the SQL query starting with SELECT, no explanation, no markdown. "
    "If the question also asks for a chart, ignore that part and write the query for the data needed. "
    "Likewise ignore parts that ask for data profiling (missing values, duplicates, descriptive statistics, "
    "correlation); they are handled elsewhere."
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


CLARIFY_PREFIX = "-- CLARIFY:"

# Dataset do người dùng upload: schema bất kỳ -> không dùng few-shot bán hàng cố định.
DATASET_SQL_SYSTEM_PROMPT = (
    "You are an expert SQLite assistant. Given a database schema and a question, write exactly one read-only "
    "SQLite SELECT query (a WITH ... SELECT is also fine). Output ONLY the SQL, no explanation, no markdown.\n"
    "Rules:\n"
    "- Use ONLY tables and columns that appear in the schema; quote names that contain spaces or special characters.\n"
    "- JOIN tables ONLY when they are connected by a FOREIGN KEY listed in the schema. Never join tables just "
    "because column names look similar. Tables without a relation must be queried separately.\n"
    f"- If the question cannot be answered with the schema, or needs a join between tables that have no FOREIGN KEY "
    f"relation, output a single line `{CLARIFY_PREFIX} <one short question asking the user to clarify or to confirm "
    "how the tables are related>` written in the same language as the question, and no SQL.\n"
    "- If the question also asks for a chart or to show the SQL, ignore that part and write only the data query. "
    "Also ignore parts that ask for data profiling (missing values, duplicates, descriptive statistics, "
    "correlation); they are handled elsewhere.\n"
    "- Never write INSERT/UPDATE/DELETE/DDL; only SELECT."
)


def build_dataset_prompt(question: str, schema: str) -> str:
    """Prompt cho dataset upload: chỉ schema (kèm khóa ngoại/quan hệ đã xác nhận) + câu hỏi, không few-shot."""
    return f"""/* Given the following database schema: */
{schema}

/* Answer the following: {question} */
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
