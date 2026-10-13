"""frontend/ui/styles.py — CSS tùy chỉnh (giữ nguyên bản gốc)."""
import streamlit as st

CUSTOM_CSS = """
<style>
    .main { max-width: 1200px; }
    .stCodeBlock { font-size: 13px; }
    .badge-mock {
        background:#FFF3CD; color:#856404;
        padding:4px 10px; border-radius:12px;
        font-size:12px; font-weight:600;
    }
    .badge-real {
        background:#D1ECF1; color:#0C5460;
        padding:4px 10px; border-radius:12px;
        font-size:12px; font-weight:600;
    }
    .badge-ollama {
        background:#D4EDDA; color:#155724;
        padding:4px 10px; border-radius:12px;
        font-size:12px; font-weight:600;
    }
    .step-label {
        background:#F0F2F6; border-left:3px solid #4C7EFF;
        padding:6px 12px; border-radius:4px;
        font-weight:600; font-size:14px; margin:8px 0;
    }

    [data-testid="stChatMessage"] { scroll-margin-top: 4.5rem; }
    .st-key-scroll_hook { position: absolute; height: 0; overflow: hidden; }

    /* Bong bóng gợi ý câu hỏi phía trên ô nhập */
    .st-key-suggestions button {
        border-radius: 999px; padding: 4px 14px; font-size: 13px;
        min-height: 0; line-height: 1.3; height: 100%;
    }
    .st-key-suggestions button p { white-space: normal; overflow: visible; text-overflow: clip; }
    .st-key-suggestions [data-testid="stHorizontalBlock"] { gap: 0.5rem; }
</style>
"""


def inject_styles() -> None:
    st.markdown(CUSTOM_CSS, unsafe_allow_html=True)


def step_label(text: str) -> None:
    st.markdown(f'<div class="step-label">{text}</div>', unsafe_allow_html=True)
