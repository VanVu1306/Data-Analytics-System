"""
backend/prompts/answer_prompts.py
---------------------------------
Prompt cho bước trả lời text (Giai đoạn 3): câu hỏi + kết quả truy vấn đã giới hạn -> câu trả lời ngắn có căn cứ.
Chỉ gửi tên cột và tối đa N dòng đầu của kết quả, không gửi dữ liệu nào khác.
"""
import pandas as pd

ANSWER_SYSTEM_PROMPT = (
    "You are a careful data analyst. Answer the user's question in 1-3 short sentences using ONLY the query "
    "result provided.\n"
    "Rules:\n"
    "- Use only values that appear in the result, copied exactly. Do NOT round, sum, average, convert units, "
    "abbreviate numbers (no 'k', 'million') or compute any new number.\n"
    "- If the result is only a sample or was truncated, say that instead of claiming totals.\n"
    "- Do not mention SQL, code, or the database. Do not explain causes or guess beyond the data.\n"
    "- Reply in the same language as the question. Plain text, no markdown table."
)

_MAX_CELL_CHARS = 80


def format_result_sample(df: pd.DataFrame, max_rows: int) -> str:
    """CSV của `max_rows` dòng đầu, cắt ô quá dài (giảm lượng dữ liệu gửi lên LLM)."""
    sample = df.head(max_rows).copy()
    for col in sample.columns:
        if sample[col].dtype == object or str(sample[col].dtype).startswith("str"):
            sample[col] = sample[col].map(
                lambda v: v if not isinstance(v, str) or len(v) <= _MAX_CELL_CHARS else v[:_MAX_CELL_CHARS] + "…"
            )
    return sample.to_csv(index=False)


def build_answer_prompt(question: str, df: pd.DataFrame, *, max_rows: int, truncated: bool = False) -> str:
    shown = min(len(df), max_rows)
    note = f"Rows in result: {len(df)}; rows shown below: {shown}."
    if truncated:
        note += " The result was cut off by a system row limit, so it is NOT the complete answer set."
    return f"""Question: {question}

Query result ({note})
{format_result_sample(df, max_rows)}
Write the short answer now."""
