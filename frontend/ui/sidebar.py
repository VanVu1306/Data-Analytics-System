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

        # ── Vẽ biểu đồ (Ollama) ──────────────────────────────
        st.subheader("🦙 Vẽ biểu đồ (Text-to-Python)")
        if health.ollama_running:
            try:
                local_models = client.models()
            except APIError:
                local_models = []
            st.markdown(
                '<span class="badge-ollama">🟢 Ollama running</span>',
                unsafe_allow_html=True,
            )
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
            st.markdown(
                '<span class="badge-mock">🟡 Ollama not running</span>',
                unsafe_allow_html=True,
            )
            st.caption("Start: `ollama serve` (terminal khác)")
            viz_use_ollama = False
            if health.llm_configured:
                st.info(f"Sẽ dùng {provider_label} làm fallback")

        viz_use_mock = st.checkbox("Dùng mock Viz code (debugging)", value=False)
        with st.expander("🦙 Hướng dẫn cài Ollama"):
            st.markdown(OLLAMA_GUIDE)

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
        has_cloud_llm_key=health.llm_configured,
        dataset_id=dataset_id,
        tables=tables,
        schema=schema,
    )
