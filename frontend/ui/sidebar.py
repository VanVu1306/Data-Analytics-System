"""
frontend/ui/sidebar.py
----------------------
Sidebar: trạng thái Stage 1 / Stage 3, chọn model, schema DB, gợi ý câu hỏi.
Trả về SidebarState để phần chính biết người dùng đã chọn gì.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import streamlit as st

from frontend.api_client import APIError, Health, NL2VizClient
from frontend.config import SUGGESTIONS


@dataclass
class SidebarState:
    sql_use_mock: bool
    viz_use_ollama: bool
    viz_use_mock: bool
    ollama_model: Optional[str]
    ollama_running: bool
    has_deepseek_key: bool


def render_sidebar(client: NL2VizClient, health: Health) -> SidebarState:
    ollama_model: Optional[str] = None

    with st.sidebar:
        st.title("⚙️ Cấu hình")

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

        # ── DB schema ─────────────────────────────────────────────────────
        st.subheader("🗄️ Database Schema")
        if not health.db_exists:
            st.error("Demo DB chưa tạo. Chạy:\n```\npython scripts/create_demo_db.py\n```")
            st.stop()

        try:
            for table, cols in client.schema().items():
                with st.expander(f"📋 {table} ({len(cols)} cols)"):
                    for c in cols:
                        st.caption(f"• {c}")
        except APIError as e:
            st.error(f"Cannot read DB: {e}")
            st.stop()

        # ── Gợi ý câu hỏi ─────────────────────────────────────────────────
        st.divider()
        st.subheader("💡 Gợi ý câu hỏi")
        for s in SUGGESTIONS:
            if st.button(s, key=f"sug_{s}", width="stretch"):
                st.session_state["question_input"] = s

    return SidebarState(
        sql_use_mock=sql_use_mock,
        viz_use_ollama=viz_use_ollama,
        viz_use_mock=viz_use_mock,
        ollama_model=ollama_model,
        ollama_running=health.ollama_running,
        has_deepseek_key=health.deepseek_configured,
    )
