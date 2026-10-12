"""
frontend/ui/main_view.py
------------------------
Phần nội dung chính: tiêu đề, ô nhập câu hỏi, hiển thị kết quả 3 stage, màn hình giới thiệu.
"""
from __future__ import annotations

from typing import Tuple

import streamlit as st

from frontend.api_client import QueryResult
from frontend.config import CHART_HINTS, DEFAULT_QUESTION
from frontend.ui.sidebar import SidebarState
from frontend.ui.styles import step_label


def render_header() -> None:
    st.title("📊 NL2Viz — Natural Language to Visualization")
    st.caption(
        "Pipeline: Câu hỏi → **DAIL-SQL** (DeepSeek) → SQL → DataFrame → "
        "**Text-to-Python** (Ollama Llama3 local) → Biểu đồ"
    )


def render_question_form() -> Tuple[bool, str, str]:
    """Trả về (run_btn, question, chart_hint)."""
    # Giá trị mặc định đặt qua session_state (sidebar cũng ghi vào key này khi bấm gợi ý)
    st.session_state.setdefault("question_input", DEFAULT_QUESTION)
    question = st.text_input(
        "Nhập câu hỏi của bạn:",
        placeholder="Ví dụ: What is the revenue by region?",
        key="question_input",
    )

    col_run, col_chart_type = st.columns([3, 1])
    with col_run:
        run_btn = st.button("▶ Chạy Pipeline", type="primary", width="stretch")
    with col_chart_type:
        chart_hint = st.selectbox(
            "Gợi ý chart",
            CHART_HINTS,
            index=0,
            label_visibility="collapsed",
        )
    return run_btn, question, chart_hint


def render_results(result: QueryResult, state: SidebarState) -> None:
    # ═══ STAGE 1: DAIL-SQL → SQL ═══
    step_label("Stage 1 · DAIL-SQL → SQL Query")
    col1, col2 = st.columns([1, 1])
    with col1:
        st.caption("**SQL được sinh ra:**")
        st.code(result.sql, language="sql")
    with col2:
        with st.expander("📄 Xem DAIL Prompt"):
            st.code(result.sql_prompt, language="text")

    # ═══ STAGE 2: Execute SQL → DataFrame ═══
    step_label("Stage 2 · Thực thi SQL → DataFrame")

    if result.status == "sql_error":
        st.error(f"❌ Lỗi SQL: {result.sql_error}")
        st.stop()

    if result.status == "empty_result" or result.df is None or result.df.empty:
        st.warning("⚠️ Query thành công nhưng không có dữ liệu trả về.")
        st.stop()

    df = result.df
    st.success(f"✅ Trả về {len(df)} dòng × {len(df.columns)} cột")
    st.dataframe(df, width="stretch", height=200)

    # ═══ STAGE 3: Text-to-Python → Chart ═══
    step_label("Stage 3 · Text-to-Python → Biểu đồ")

    tab_chart, tab_code, tab_prompt = st.tabs(["📊 Biểu đồ", "🐍 Python Code", "📄 Viz Prompt"])

    with tab_chart:
        if result.viz_error:
            st.error(f"❌ Lỗi khi vẽ biểu đồ:\n```\n{result.viz_error}\n```")
        elif result.chart_png:
            st.image(result.chart_png, width="stretch")
            st.success("✅ Biểu đồ tạo thành công!")
        else:
            st.warning("Không tạo được biểu đồ.")

    with tab_code:
        st.caption("Mã Python được sinh bởi LLM:")
        st.code(result.viz_code, language="python")

    with tab_prompt:
        st.caption("Prompt gửi cho Text-to-Python module:")
        st.code(result.viz_prompt, language="text")

    # Summary
    st.divider()
    m1, m2, m3 = st.columns(3)
    m1.metric("Rows returned", len(df))
    m2.metric("Columns", len(df.columns))
    m3.metric(
        "Mode",
        "Llama3 Local"
        if (state.viz_use_ollama and state.ollama_running and not state.viz_use_mock)
        else ("DeepSeek" if (state.has_deepseek_key and not state.viz_use_mock) else "Mock"),
    )


def render_intro() -> None:
    st.info("👈 Nhập câu hỏi hoặc chọn gợi ý từ sidebar, rồi nhấn **Chạy Pipeline**.")

    with st.expander("📖 Architecture overview"):
        st.markdown("""
**Stage 1 — DAIL-SQL (DeepSeek API)**
- Code Representation Prompt (CRP)
- Few-shot examples với Q+SQL pairs
- Output: SQL query

**Stage 2 — SQLite Executor**
- Run SQL trên local DB
- Return DataFrame

**Stage 3 — Local-Python-Viz (Ollama Llama3)**
- Zero-shot prompt (Context + Requirement + Constraint)
- Llama3 chạy local → privacy-preserving
- Sinh matplotlib code → sandbox exec → Figure
        """)

    with st.expander("🦙 Hướng dẫn cài Ollama"):
        st.markdown("""
1. **Tải Ollama:** https://ollama.com/download

2. **Pull model:**
```bash
ollama pull llama3.2
```

3. **Start server (giữ terminal chạy):**
```bash
ollama serve
```

4. **Verify:**
```bash
curl http://localhost:11434/api/tags
```

5. **Restart streamlit app** để app detect được Ollama.
        """)
