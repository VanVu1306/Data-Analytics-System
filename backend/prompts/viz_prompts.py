"""
backend/prompts/viz_prompts.py
------------------------------
Prompt cho Stage 3 (Local-Python-Viz): Zero-Shot theo Khan et al. (2025),
cấu trúc Context + Requirement + Constraint.
Nội dung giữ nguyên như bản gốc modules/text_to_python.py.
"""
import pandas as pd

# System prompt khi fallback sang DeepSeek (Ollama không dùng system prompt riêng).
VIZ_CLOUD_SYSTEM_PROMPT = (
    "You are an expert Python data visualization programmer. "
    "Output ONLY valid Python code, no markdown, no explanation. "
    "The variable `df` is a pandas DataFrame already in memory."
)


def build_viz_prompt(question: str, df: pd.DataFrame, sample_rows: int = 3) -> str:
    """
    Zero-Shot prompt 3 phần:
    1. CONTEXT: tên cột + dtype (Quantitative/Categorical) + dòng mẫu
    2. REQUIREMENT: yêu cầu vẽ biểu đồ
    3. CONSTRAINT: định dạng output (chỉ Python code, không markdown)
    """
    col_info = []
    for col in df.columns:
        dtype = df[col].dtype
        kind = "Quantitative" if pd.api.types.is_numeric_dtype(dtype) else "Categorical"
        col_info.append(f"  - {col} ({kind}, dtype={dtype})")
    cols_str = "\n".join(col_info)

    sample_data = df.head(sample_rows).to_string(index=False)

    return f"""You are a Python data visualization expert.

DataFrame `df` columns:
{cols_str}

Sample data (first {sample_rows} rows):
{sample_data}

User request: {question}

Write Python code using matplotlib (and optionally seaborn) to create a clear, well-labeled chart.

Requirements:
- Use the variable `df` which is already loaded as a pandas DataFrame.
- Import only matplotlib.pyplot as plt and seaborn as sns (if needed).
- Always start with: plt.figure(figsize=(10, 6))
- Add title, axis labels, and legend if applicable.
- Use a clean style: plt.style.use('seaborn-v0_8-whitegrid') or 'ggplot'.
- End with plt.tight_layout() — do NOT call plt.show().
- When referring to columns with hyphens or spaces, use bracket notation like df['col-name'].

Output ONLY the Python code — no explanation, no markdown fences."""
