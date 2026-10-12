"""
frontend/app.py — NL2Viz Streamlit UI
=====================================
Frontend thuần giao diện: gọi backend qua REST, không import gì từ `backend/`.

Chạy (cần backend đang chạy):
    uvicorn backend.main:app --host 127.0.0.1 --port 8000     # terminal 1
    streamlit run frontend/app.py                              # terminal 2
"""
import os
import sys

import streamlit as st

# Cho phép `from frontend...` khi chạy `streamlit run frontend/app.py` từ thư mục gốc
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from frontend.api_client import APIError, NL2VizClient  # noqa: E402
from frontend.ui.main_view import (  # noqa: E402
    render_header,
    render_intro,
    render_question_form,
    render_results,
)
from frontend.ui.sidebar import render_sidebar  # noqa: E402
from frontend.ui.styles import inject_styles  # noqa: E402

st.set_page_config(
    page_title="NL2Viz — Text to SQL to Chart",
    page_icon="📊",
    layout="wide",
)
inject_styles()

client = NL2VizClient()

try:
    health = client.health()
except APIError as e:
    st.error(f"❌ {e}")
    st.stop()

state = render_sidebar(client, health)

render_header()
run_btn, question, chart_hint = render_question_form()

if run_btn and question:
    with st.spinner("🔄 Đang chạy pipeline: sinh SQL → thực thi → vẽ biểu đồ..."):
        try:
            result = client.query(
                question,
                chart_hint=chart_hint,
                use_mock_sql=state.sql_use_mock,
                use_ollama=state.viz_use_ollama,
                use_mock_viz=state.viz_use_mock,
                ollama_model=state.ollama_model,
            )
        except APIError as e:
            st.error(f"❌ {e}")
            st.stop()
    render_results(result, state)

elif not run_btn:
    render_intro()
