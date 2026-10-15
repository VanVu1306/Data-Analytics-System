"""
backend/prompts/planner_prompts.py
----------------------------------
Prompt cho Planner Agent (giai đoạn 6): LLM lập MỘT kế hoạch JSON thống nhất cho câu hỏi gồm
`steps` (query tối đa 1 + các operation EDA), `chart` và `clarification`.

LLM chỉ quyết định "cần làm gì". Nó không viết SQL, không viết code, không tính số liệu:
SQL do SQL Agent sinh, EDA do tool xác định tính, biểu đồ do Viz Agent / chart tools dựng.
Mọi kế hoạch được `validate_llm_plan` kiểm tra với schema và allowlist trước khi Orchestrator chạy.
Chỉ gửi tên bảng/cột/kiểu (schema), không gửi dữ liệu.
"""
from __future__ import annotations

from typing import Any, Dict, List, Sequence

_TEMPLATE = """You are the planner of a data-analysis assistant. Read the user's question and the dataset schema, then decide what must be done to answer it.
Reply with ONE JSON object only (no prose, no markdown) in exactly this shape:
{{"steps": [{{"op": "...", "question": "...", "table": "...", "columns": ["..."], "params": {{}}}}],
 "chart": {{"requested": false, "type": null, "goal": null}},
 "clarification": null}}

"steps" - ordered list of operations. Allowed "op" values:
- "query": answer a specific data question with SQL (e.g. revenue by month, top 5 products, filter rows, a single number). AT MOST ONE query step. Put the data question in "question": copy the user's wording and language, keep every filter/condition, drop only the profiling and chart parts.
- "table_overview": row/column counts, data types, distinct counts of a table.
- "data_quality": missing values (NULL), blank strings and duplicate rows of a table.
- "numeric_summary": count, mean, std, min, quartiles, median, max of numeric columns.
- "categorical_summary": number of groups and top values of categorical columns. params: {{"top_k": integer 1-50}}.
- "correlation": correlation matrix of numeric columns. params: {{"method": "pearson" | "spearman"}}.
"table" and "columns" are required for EDA operations when more than one table could apply; copy names EXACTLY from the schema. Omit "columns" to mean "all suitable columns". "query" needs no table or columns.

"chart" - does the user ask for a chart/plot/graph/visualisation?
- "requested": true only if the user explicitly asks to draw/plot/visualise something; false otherwise (and false when the user says not to draw). Never request a chart on your own.
- "type": one of {chart_types} when the user names a type, otherwise null (the system picks one from the data).
- "goal": one of {chart_goals} when the purpose is clear (comparison between groups, trend over time, distribution of values, relationship between two numbers, composition/share of a whole), otherwise null.

Rules:
- Use ONLY table and column names from the schema. Never invent operations, SQL, code or chart types.
- A simple data question is exactly one "query" step. Add EDA steps only when the user asks for an overview, quality check, statistics, distribution, frequencies or correlation. Do not add profiling to a simple question.
- A combined question (a data question AND profiling) -> one "query" step plus the EDA steps.
- A request to draw a chart of a result still needs the step that produces the data (usually "query", or the EDA operation whose result is plotted, e.g. "correlation" for a heatmap).
- If the question is ambiguous or lacks what you need (which table? which columns? what to analyse?), return {{"steps": [], "chart": {{"requested": false, "type": null, "goal": null}}, "clarification": "<one short question in the user's language>"}}.
- Do not ask for clarification when the question can be answered from the schema."""


def build_planner_system_prompt(chart_types: Sequence[str], chart_goals: Sequence[str]) -> str:
    """Prompt hệ thống của planner; danh sách loại/mục tiêu biểu đồ do agent truyền vào (nguồn sự thật nằm ở tools/intent)."""
    return _TEMPLATE.format(chart_types=", ".join(chart_types), chart_goals=", ".join(chart_goals))


def format_schema(schema: Dict[str, List[Dict[str, Any]]], max_tables: int = 30, max_columns: int = 60) -> str:
    lines = []
    for t, cols in list(schema.items())[:max_tables]:
        shown = ", ".join(f"{c['name']} {c['type']}".strip() for c in cols[:max_columns])
        more = f", … (+{len(cols) - max_columns} columns)" if len(cols) > max_columns else ""
        lines.append(f"- {t}({shown}{more})")
    if len(schema) > max_tables:
        lines.append(f"- … (+{len(schema) - max_tables} more tables)")
    return "\n".join(lines)


def build_planner_prompt(question: str, schema: Dict[str, List[Dict[str, Any]]]) -> str:
    return f"""Schema:
{format_schema(schema)}

Question: {question}

JSON plan:"""
