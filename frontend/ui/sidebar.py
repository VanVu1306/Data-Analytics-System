"""
frontend/ui/sidebar.py
----------------------
Sidebar: chọn dataset, trạng thái Stage 1 / Stage 3, chọn model, schema + quan hệ, gợi ý câu hỏi.
Trả về SidebarState để phần chính biết người dùng đã chọn gì.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

import streamlit as st

from frontend.api_client import APIError, Health, NL2VizClient
from frontend.ui.datasets import render_dataset_picker, render_schema_and_relations, suggestions_for


@dataclass
class SidebarState:
    sql_use_mock: bool
    viz_use_ollama: bool
    viz_use_mock: bool
    ollama_model: Optional[str]
    ollama_running: bool
    has_deepseek_key: bool
    dataset_id: Optional[str] = None  # None = demo DB
    tables: Optional[List[str]] = None  # None = không có dataset nào để hỏi


def render_sidebar(client: NL2VizClient, health: Health) -> SidebarState:
    ollama_model: Optional[str] = None

    with st.sidebar:
        st.title("⚙️ Cấu hình")

        # ── Dataset ───────────────────────────────────────────────────────
        st.subheader("🗂️ Dataset")
        dataset_id = render_dataset_picker(demo_available=health.db_exists)
        has_dataset = health.db_exists or dataset_id is not None
        if not has_dataset:
            st.info("Chưa có dataset. Hãy upload tệp dữ liệu ở trang chính\n(hoặc chạy `python scripts/create_demo_db.py` để tạo demo DB).")

        st.divider()

        # ── Stage 1: DAIL-SQL (DeepSeek) ──────────────────────────────────
        st.subheader("🔮 Stage 1: DAIL-SQL")
        if health.deepseek_configured:
            st.markdown(
                '<span class="badge-real">🟢 DeepSeek API connected</span>',
                unsafe_allow_html=True,
            )
            sql_use_mock = st.checkbox("Use mock SQL (debugging)", value=False)
        else:
            st.markdown(
                '<span class="badge-mock">🟡 No DeepSeek key — mock mode</span>',
                unsafe_allow_html=True,
            )
            st.caption("Set DEEPSEEK_API_KEY env var to enable real DAIL-SQL.")
            sql_use_mock = True

        st.divider()

        # ── Stage 3: Text-to-Python (Ollama) ──────────────────────────────
        st.subheader("🦙 Stage 3: Text-to-Python")
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
            if health.deepseek_configured:
                st.info("Sẽ dùng DeepSeek làm fallback")

        viz_use_mock = st.checkbox("Use mock Viz code (debugging)", value=False)

        st.divider()

        # ── Schema + quan hệ + gợi ý câu hỏi ──────────────────────────────
        tables: Optional[List[str]] = None
        if has_dataset:
            st.subheader("📋 Schema")
            try:
                schema = render_schema_and_relations(client, dataset_id)
                tables = list(schema.tables)
            except APIError as e:
                st.error(f"Cannot read DB: {e}")

            st.divider()
            st.subheader("💡 Gợi ý câu hỏi")
            for sug in suggestions_for(tables or [], dataset_id):
                if st.button(sug, key=f"sug_{dataset_id}_{sug}", width="stretch"):
                    st.session_state["question_input"] = sug

    return SidebarState(
        sql_use_mock=sql_use_mock,
        viz_use_ollama=viz_use_ollama,
        viz_use_mock=viz_use_mock,
        ollama_model=ollama_model,
        ollama_running=health.ollama_running,
        has_deepseek_key=health.deepseek_configured,
        dataset_id=dataset_id,
        tables=tables,
    )
