"""
frontend/app.py — NL2Viz Streamlit UI
=====================================
Frontend thuần giao diện: gọi backend qua REST, không import gì từ `backend/`.
Một khung chat duy nhất: người dùng chỉ nhập câu hỏi; câu trả lời text + bảng là mặc định,
SQL nằm trong "Bằng chứng / SQL", biểu đồ chỉ vẽ khi được yêu cầu.

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
from frontend.ui.datasets import dataset_label, suggestions_for  # noqa: E402
from frontend.ui.main_view import (  # noqa: E402
    CHAT_PLACEHOLDER,
    messages,
    render_assistant_body,
    render_suggestions,
    render_header,
    render_intro,
    render_message,
    render_scroll_hook,
)
from frontend.ui.sidebar import render_sidebar  # noqa: E402
from frontend.ui.styles import inject_styles  # noqa: E402

st.set_page_config(
    page_title="Data Analytics Chatbot",
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

render_header()
state = render_sidebar(client, health)

if state.tables is None:
    st.info("👈 Chưa có dataset để phân tích. Hãy upload tệp dữ liệu ở sidebar.")
    st.stop()

history = messages()
current_label = dataset_label(state.dataset_id)

# Đổi dataset: giữ lịch sử nhưng đánh dấu để không nhầm câu trả lời của dataset khác
if st.session_state.get("_chat_dataset", "__unset__") != state.dataset_id:
    if "_chat_dataset" in st.session_state and history:
        history.append({"role": "notice", "content": f"Đã chuyển sang dataset: {current_label}"})
    st.session_state["_chat_dataset"] = state.dataset_id

# ── Lịch sử hội thoại ──────────────────────────────────────────────────────
if history:
    if st.button("🗑️ Xóa hội thoại", key="clear_chat"):
        history.clear()
        st.rerun()
    for msg in history:
        render_message(msg)
intro_slot = st.empty()  # màn hình giới thiệu chỉ hiện khi chưa có tin nhắn nào
if not history:
    with intro_slot.container():
        render_intro()

# ── Gợi ý (bong bóng) + ô nhập, ghim ở cuối trang ──────────────────────────
with st.bottom:
    schema_tables = state.schema.tables if state.schema else {}
    clicked = render_suggestions(suggestions_for(schema_tables, state.dataset_id)[:5])
    prompt = st.chat_input(CHAT_PLACEHOLDER)
prompt = prompt or clicked

if prompt and prompt.strip():
    prompt = prompt.strip()
    intro_slot.empty()
    history.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)
    with st.chat_message("assistant"):
        with st.spinner("🔄 Đang xử lý câu hỏi và soạn câu trả lời..."):
            try:
                result = client.query(
                    prompt,
                    use_mock_sql=state.sql_use_mock,
                    use_ollama=state.viz_use_ollama,
                    use_mock_viz=state.viz_use_mock,
                    ollama_model=state.ollama_model,
                    dataset_id=state.dataset_id,
                    chart_hint=state.chart_hint,
                )
                reply = {"role": "assistant", "result": result, "dataset": current_label}
            except APIError as e:
                reply = {"role": "assistant", "error": str(e)}
        history.append(reply)
        render_assistant_body(reply)

render_scroll_hook()  # luôn cuộn tới tin nhắn mới nhất
