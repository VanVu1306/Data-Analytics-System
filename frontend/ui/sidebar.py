"""
frontend/ui/sidebar.py
----------------------
Sidebar: chọn dataset, cấu hình LLM (SQL + câu trả lời) và vẽ biểu đồ, upload, schema + quan hệ, hướng dẫn Ollama.
Trả về SidebarState để phần chính biết người dùng đã chọn gì.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

import streamlit as st

from frontend.api_client import APIError, Health, NL2VizClient
from frontend.api_client import Schema
from frontend.ui.datasets import render_dataset_picker, render_schema_and_relations, render_upload_panel


OLLAMA_GUIDE = """
1. **Tải Ollama:** https://ollama.com/download
2. **Pull model:** `ollama pull llama3.2`
3. **Start server (giữ terminal chạy):** `ollama serve`
4. **Verify:** `curl http://localhost:11434/api/tags`
5. **Restart streamlit app** để app nhận ra Ollama.
"""


# Tùy chọn biểu đồ ở sidebar -> `chart_hint` của API. "Theo câu hỏi" để backend tự nhận diện từ prompt;
# các lựa chọn khác ưu tiên hơn prompt (hợp đồng API hiện tại).
CHART_OPTIONS = {
    "Theo câu hỏi": "detect",
    "Không vẽ": "none",
    "Tự chọn loại phù hợp": "auto",
    "Biểu đồ cột (bar)": "bar chart",
    "Biểu đồ đường (line)": "line chart",
    "Biểu đồ tròn (pie)": "pie chart",
    "Phân tán (scatter)": "scatter plot",
    "Histogram": "histogram",
    "Mật độ (KDE)": "kde plot",
    "Box plot": "box plot",
    "Violin plot": "violin plot",
    "Heatmap tương quan": "heatmap",
    "Đếm tần suất (count)": "count plot",
}


@dataclass
class SidebarState:
    sql_use_mock: bool
    viz_use_ollama: bool
    viz_use_mock: bool
    ollama_model: Optional[str]
    ollama_running: bool
    llm_provider: str
    has_cloud_llm_key: bool
    dataset_id: Optional[str] = None  # None = demo DB
    tables: Optional[List[str]] = None  # None = không có dataset nào để hỏi
    schema: Optional[Schema] = None
    chart_hint: str = "detect"  # xem CHART_OPTIONS
    viz_mode: str = "spec"


def render_sidebar(client: NL2VizClient, health: Health) -> SidebarState:
    ollama_model: Optional[str] = None

    with st.sidebar:
        st.title("⚙️ Cấu hình")

        # ── Dataset ───────────────────────────────────────────────────────
        st.subheader("🗂️ Dataset")
        dataset_id = render_dataset_picker(demo_available=health.db_exists)
        render_upload_panel(client, health.max_upload_mb)  # ngay dưới ô chọn dataset
        has_dataset = health.db_exists or dataset_id is not None
        if not has_dataset:
            st.info("Chưa có dataset. Hãy upload tệp dữ liệu ở trên\n(hoặc chạy `python scripts/create_demo_db.py` để tạo demo DB).")

        st.divider()

        # ── Sinh SQL & trả lời ──────────────────────────────────────
        provider_label = "OpenAI GPT" if health.llm_provider == "openai" else "DeepSeek"
        st.subheader(f"🔮 Sinh SQL & trả lời ({provider_label})")
        if health.llm_configured:
            st.markdown(
                f'<span class="badge-real">🟢 {provider_label} API configured</span>',
                unsafe_allow_html=True,
            )
            sql_use_mock = st.checkbox(
                "Dùng mock (không gọi LLM) cho SQL & câu trả lời", value=False
            )
        else:
            st.markdown(
                f'<span class="badge-mock">🟡 No {provider_label} key — mock mode</span>',
                unsafe_allow_html=True,
            )
            key_name = "OPENAI_API_KEY" if health.llm_provider == "openai" else "DEEPSEEK_API_KEY"
            st.caption(f"Set {key_name} env var to enable cloud SQL generation.")
            sql_use_mock = True

        st.divider()

        # ── Biểu đồ ───────────────────────────────────────────────────────
        st.subheader("📈 Biểu đồ")
        chart_label = st.selectbox(
            "Loại biểu đồ:",
            options=list(CHART_OPTIONS),
            index=0,
            help="'Theo câu hỏi': chỉ vẽ khi câu hỏi yêu cầu. Lựa chọn khác sẽ ưu tiên hơn nội dung câu hỏi.",
        )
        chart_hint = CHART_OPTIONS[chart_label]
        viz_use_ollama = False
        viz_use_mock = False
        if health.viz_mode == "code":  # chế độ cũ: LLM sinh code matplotlib
            st.caption("Chế độ VIZ_MODE=code: LLM sinh code vẽ biểu đồ.")
            if health.ollama_running:
                try:
                    local_models = client.models()
                except APIError:
                    local_models = []
                st.markdown('<span class="badge-ollama">🟢 Ollama running</span>', unsafe_allow_html=True)
                if local_models:
                    default = health.ollama_default_model
                    ollama_model = st.selectbox(
                        "Llama model:",
                        options=local_models,
                        index=local_models.index(default) if default in local_models else 0,
                    )
                else:
                    st.warning("No models found. Run: `ollama pull llama3.2`")
                viz_use_ollama = st.checkbox("Use Ollama local (privacy mode)", value=True)
            else:
                st.markdown('<span class="badge-mock">🟡 Ollama not running</span>', unsafe_allow_html=True)
                st.caption("Start: `ollama serve` (terminal khác)")
                if health.llm_configured:
                    st.info(f"Sẽ dùng {provider_label} làm fallback")
            viz_use_mock = st.checkbox("Dùng mock Viz code (debugging)", value=False)
            with st.expander("🦙 Hướng dẫn cài Ollama"):
                st.markdown(OLLAMA_GUIDE)
        else:
            st.caption("Biểu đồ được vẽ bằng bộ vẽ xác định (không chạy code do LLM sinh).")

        st.divider()

        # ── Schema + quan hệ + gợi ý câu hỏi ──────────────────────────────
        tables: Optional[List[str]] = None
        schema = None
        if has_dataset:
            st.subheader("📋 Schema")
            try:
                schema = render_schema_and_relations(client, dataset_id)
                tables = list(schema.tables)
            except APIError as e:
                st.error(f"Cannot read DB: {e}")

    return SidebarState(
        sql_use_mock=sql_use_mock,
        viz_use_ollama=viz_use_ollama,
        viz_use_mock=viz_use_mock,
        ollama_model=ollama_model,
        ollama_running=health.ollama_running,
        llm_provider=health.llm_provider,
        chart_hint=chart_hint,
        viz_mode=health.viz_mode,
        has_cloud_llm_key=health.llm_configured,
        dataset_id=dataset_id,
        tables=tables,
        schema=schema,
    )
