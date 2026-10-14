"""
backend/prompts/planner_prompts.py
----------------------------------
Prompt cho planner (giai đoạn 4): LLM chỉ trả KẾ HOẠCH JSON; mọi bước được `validate_plan` kiểm tra với schema và
allowlist trước khi chạy. Chỉ gửi tên bảng/cột/kiểu, không gửi dữ liệu.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

PLANNER_SYSTEM_PROMPT = """You are a planner for a data-analysis assistant. Decide which operations answer the user's question.
Reply with ONE JSON object only, no prose, no markdown:
{"steps": [ {"op": "...", "table": "...", "columns": ["..."], "params": {}, "question": "..."} ], "clarification": null}

Allowed operations:
- "query": answer a specific question by SQL (e.g. revenue by month, top 5 products, filter rows). At most ONE query step. Put the data question (without the profiling part) in "question".
- "table_overview": row/column counts, data types, distinct counts of a table.
- "data_quality": missing values (NULL), blank strings and duplicate rows of a table.
- "numeric_summary": count, mean, std, min, quartiles, median, max of numeric columns.
- "categorical_summary": number of groups and top values of categorical columns. params: {"top_k": 1-50}.
- "correlation": correlation matrix of numeric columns. params: {"method": "pearson"|"spearman"}.

Rules:
- Use ONLY table and column names that appear in the schema, copied exactly. Omit "columns" to mean "all suitable columns".
- Do not run profiling steps for a simple data question; add an EDA step only when the user asks for it (overview, quality, statistics, distribution, correlation).
- If the question is ambiguous (which table? what to analyse?), return {"steps": [], "clarification": "<one short question in the user's language>"}.
- Never invent operations, code or SQL."""


def format_schema(schema: Dict[str, List[Dict[str, Any]]], max_tables: int = 30, max_columns: int = 60) -> str:
    lines = []
    for t, cols in list(schema.items())[:max_tables]:
        shown = ", ".join(f"{c['name']} {c['type']}".strip() for c in cols[:max_columns])
        more = f", … (+{len(cols) - max_columns} columns)" if len(cols) > max_columns else ""
        lines.append(f"- {t}({shown}{more})")
    return "\n".join(lines)


def build_planner_prompt(question: str, schema: Dict[str, List[Dict[str, Any]]], hint: Optional[List[str]] = None) -> str:
    hint_txt = ""
    if hint:
        hint_txt = "\nRule-based draft (may be wrong): " + json.dumps(hint, ensure_ascii=False) + "\n"
    return f"""Schema:
{format_schema(schema)}
{hint_txt}
Question: {question}

JSON plan:"""
