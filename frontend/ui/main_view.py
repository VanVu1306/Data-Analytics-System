"""
frontend/ui/main_view.py
------------------------
Phần nội dung chính: MỘT khung chat. Người dùng chỉ nhập câu hỏi; mỗi phản hồi của hệ thống gồm
câu trả lời text -> bảng kết quả -> (biểu đồ nếu được yêu cầu) -> phần "Bằng chứng / SQL" có thể mở rộng.

Trạng thái trong `st.session_state`:
  messages   list các tin nhắn:
               {"role": "user", "content": str}
               {"role": "assistant", "result": QueryResult, "dataset": nhãn dataset}
               {"role": "assistant", "summary": dict}             (thống kê dataset, không gọi LLM)
               {"role": "assistant", "error": str}              (lỗi gọi backend)
               {"role": "notice", "content": str}               (ví dụ: đã đổi dataset)
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import streamlit as st
import streamlit.components.v1 as components

from frontend.api_client import QueryResult
from frontend.ui.datasets import render_dataset_summary

CHAT_PLACEHOLDER = "Hỏi về dữ liệu của bạn… (ví dụ: Doanh thu theo vùng, vẽ biểu đồ cột)"


def render_header() -> None:
    st.title("📊 Data Analytics Chatbot")
    st.caption(
        "**Stage 1** Câu hỏi → SQL  ·  **Stage 2** Thực thi SQL → bảng kết quả + câu trả lời  ·  "
        "**Stage 3** Vẽ biểu đồ (chỉ khi bạn yêu cầu)"
    )


def messages() -> List[Dict[str, Any]]:
    return st.session_state.setdefault("messages", [])


# ── Tin nhắn ───────────────────────────────────────────────────────────────
def render_message(msg: Dict[str, Any]) -> None:
    role = msg["role"]
    if role == "notice":
        st.caption(f"— {msg['content']} —")
    elif role == "user":
        with st.chat_message("user"):
            st.markdown(msg["content"])
    else:
        with st.chat_message("assistant"):
            render_assistant_body(msg)


def render_assistant_body(msg: Dict[str, Any]) -> None:
    if "error" in msg:
        st.error(f"❌ {msg['error']}")
        return
    if "summary" in msg:
        render_dataset_summary(msg["summary"])
        return
    result: QueryResult = msg["result"]

    if result.status == "needs_clarification":
        st.info(
            f"🤔 Cần làm rõ trước khi truy vấn: {result.clarification}\n\n"
            "Hãy diễn đạt lại câu hỏi, hoặc khai báo quan hệ giữa các bảng ở mục **🔗 Quan hệ giữa các bảng** "
            "trong sidebar rồi hỏi lại."
        )
    elif result.status == "sql_error":
        st.error(
            "❌ Không thực hiện được truy vấn cho câu hỏi này. Hãy thử diễn đạt lại, "
            "hoặc mở phần **Bằng chứng / SQL** bên dưới để xem chi tiết lỗi."
        )
    else:
        _render_answer_and_table(result)
        _render_chart(result)

    _render_evidence(result)
    if msg.get("dataset"):
        st.caption(f"Dataset: {msg['dataset']}")


def _render_answer_and_table(result: QueryResult) -> None:
    if result.answer:
        st.markdown(result.answer.text)
    if result.status == "empty_result":
        return
    if result.evidence.truncated:
        st.warning(
            "⚠️ Kết quả quá lớn nên đã bị cắt bớt theo giới hạn của hệ thống. "
            "Hãy thu hẹp câu hỏi (lọc/gộp/LIMIT)."
        )
    if result.df is not None:
        st.dataframe(result.df, width="stretch", height=min(400, 38 + 35 * len(result.df)))


def _render_chart(result: QueryResult) -> None:
    chart = result.chart
    if chart.status == "ok" and chart.png:
        st.image(chart.png, width="stretch")
    elif chart.status == "error":
        st.warning(
            "⚠️ Không vẽ được biểu đồ — câu trả lời và bảng kết quả ở trên vẫn đúng. "
            "Chi tiết lỗi ở phần **Chi tiết biểu đồ**."
        )
    if chart.status != "not_requested" and (chart.code or chart.error):
        with st.expander("🐍 Chi tiết biểu đồ", expanded=False):
            tabs = st.tabs(["Mã Python", "Prompt", "Lỗi"] if chart.error else ["Mã Python", "Prompt"])
            with tabs[0]:
                st.caption(f"Nguồn: {chart.source or '—'} · loại: {chart.chart_type or 'tự chọn'}")
                if chart.code:
                    st.code(chart.code, language="python")
            with tabs[1]:
                st.code(chart.prompt or "", language="text")
            if chart.error:
                with tabs[2]:
                    st.code(chart.error, language="text")


def _render_evidence(result: QueryResult) -> None:
    ev = result.evidence
    if not (ev.sql or ev.sql_error):
        return  # ví dụ: cần làm rõ, chưa có SQL nào được thực thi
    with st.expander("🔎 Bằng chứng / SQL", expanded=ev.show_sql):
        tab_sql, tab_exec, tab_prompt = st.tabs(["SQL đã thực thi", "Thông tin thực thi", "Prompt (debug)"])
        with tab_sql:
            if ev.sql:
                st.code(ev.sql, language="sql")
            if ev.sql_error:
                st.error(f"Lỗi: {ev.sql_error}")
        with tab_exec:
            rows = f"{ev.row_count:,} dòng × {ev.column_count} cột" if ev.row_count is not None else "—"
            st.markdown(
                f"- **Kết quả:** {rows}" + (" _(đã bị cắt theo giới hạn hệ thống)_" if ev.truncated else "") + "\n"
                f"- **Thời gian thực thi SQL:** {f'{ev.elapsed_ms} ms' if ev.elapsed_ms is not None else '—'}\n"
                f"- **Nguồn SQL:** {ev.sql_source}\n"
                f"- **Nguồn câu trả lời:** {result.answer.source if result.answer else '—'}"
                + (f" _(dùng câu mẫu: {result.answer.fallback_reason})_" if result.answer and result.answer.fallback_reason else "")
            )
        with tab_prompt:
            st.caption("Prompt gửi cho bước sinh SQL:")
            st.code(ev.sql_prompt, language="text")


# ── Gợi ý câu hỏi (bong bóng phía trên ô nhập) ─────────────────────────────
def render_suggestions(suggestions: List[str]) -> Optional[str]:
    """Các bong bóng gợi ý; trả về câu được bấm (gửi như một tin nhắn chat) hoặc None."""
    if not suggestions:
        return None
    clicked = None
    with st.container(key="suggestions"):
        cols = st.columns(len(suggestions))
        for i, (col, text) in enumerate(zip(cols, suggestions)):
            if col.button(text, key=f"sug_{i}", width="stretch"):
                clicked = text
    return clicked


# Mã chạy trong ngữ cảnh của trang cha (không phải iframe) để observer/timer không bị hủy khi Streamlit
# dựng lại iframe ở các lần rerun.
_PARENT_JS = """
(function () {
  if (window.__nl2vizAutoScroll) return;      // chỉ cài một lần cho mỗi phiên trình duyệt
  window.__nl2vizAutoScroll = true;
  const SEL = '[data-testid="stChatMessage"]';
  let count = 0, timer = null;
  const scrollToLatest = () => {
    const msgs = document.querySelectorAll(SEL);
    if (msgs.length > count) msgs[msgs.length - 1].scrollIntoView({ behavior: "smooth", block: "start" });
    count = msgs.length;
  };
  new MutationObserver(() => { clearTimeout(timer); timer = setTimeout(scrollToLatest, 150); })
    .observe(document.body, { childList: true, subtree: true });
  scrollToLatest();
})();
"""

_SCROLL_JS = (
    "<script>(function(){const d=window.parent.document;"
    "if(d.getElementById('nl2viz-autoscroll'))return;"
    "const s=d.createElement('script');s.id='nl2viz-autoscroll';"
    f"s.textContent={json.dumps(_PARENT_JS)};d.head.appendChild(s);}})();</script>"
)


def render_scroll_hook() -> None:
    """Cuộn khung chat tới tin nhắn mới nhất (của người dùng hoặc hệ thống) mỗi khi có tin nhắn được thêm."""
    with st.container(key="scroll_hook"):
        if hasattr(st, "iframe"):  # Streamlit mới: components.v1.html sắp bị gỡ
            st.iframe(_SCROLL_JS, height=1)
        else:
            components.html(_SCROLL_JS, height=0)


def render_intro() -> None:
    st.info(
        "👈 Upload dữ liệu ở sidebar (hoặc dùng demo), rồi hỏi bằng ngôn ngữ tự nhiên ở ô chat bên dưới. "
        "Bấm một bong bóng gợi ý để thử nhanh."
    )
