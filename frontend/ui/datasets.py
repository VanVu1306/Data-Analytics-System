"""
frontend/ui/datasets.py
-----------------------
Quản lý dataset trên UI: upload (giai đoạn 1), chọn dataset đang phân tích,
xem schema và khai báo quan hệ giữa các bảng (giai đoạn 2).

Trạng thái trong `st.session_state`:
  datasets          {dataset_id: info}  các dataset đã upload trong phiên này
  _dataset_picker   nhãn dataset đang chọn (widget ở sidebar)
  active_dataset    dataset_id | None   None = demo DB (luồng cũ), suy ra từ widget
  _upload_toasts    toast thông báo kết quả import, hiện sau rerun
  uploader_n        số đếm để làm mới ô chọn tệp sau khi import xong
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import streamlit as st

from frontend.api_client import APIError, NL2VizClient
from frontend.config import SUGGESTIONS, UPLOAD_TYPES

DEMO_LABEL = "Demo — sales_demo.db"
OVERVIEW_PROMPT = "Tổng quan dataset"  # câu hỏi EDA thông thường, đi qua cùng pipeline như mọi câu hỏi khác


def _datasets() -> Dict[str, Dict[str, Any]]:
    return st.session_state.setdefault("datasets", {})


def dataset_label(dataset_id: Optional[str]) -> str:
    if dataset_id is None:
        return DEMO_LABEL
    info = _datasets().get(dataset_id)
    return f"{info['filename']} · {dataset_id[:6]}" if info else dataset_id


def suggestions_for(schema_tables: Dict[str, List[str]], dataset_id: Optional[str]) -> List[str]:
    """Gợi ý câu hỏi (kể cả yêu cầu vẽ từng loại biểu đồ) theo schema của dataset đang chọn."""
    if dataset_id is None:
        return [OVERVIEW_PROMPT] + SUGGESTIONS
    if not schema_tables:
        return []
    table, cols = next(iter(schema_tables.items()))
    out = [OVERVIEW_PROMPT, f"Kiểm tra giá trị thiếu và trùng lặp của bảng {table}",
           f"Show the first 10 rows of {table}", f"Thống kê mô tả các cột số của bảng {table}"]
    if cols:
        c = cols[0]
        out += [
            f"Vẽ biểu đồ cột số dòng theo {c} của bảng {table}",
            f"Vẽ biểu đồ tròn tỷ lệ số dòng theo {c} của bảng {table}",
        ]
    if len(cols) >= 2:
        out.append(f"Vẽ biểu đồ phân tán {cols[0]} và {cols[1]} của bảng {table}")
    return out


# ── Upload (trong sidebar, ngay dưới ô chọn dataset) ───────────────────────
def render_upload_panel(client: NL2VizClient, max_upload_mb: int) -> None:
    """Ô chọn tệp + nút import, gọi trong `with st.sidebar` sau `render_dataset_picker`.

    Chọn dataset vừa upload: widget chọn dataset đã được dựng nên không thể ghi trực tiếp vào state của nó;
    ta ghi `_select_dataset_label` rồi rerun, `render_dataset_picker` áp dụng giá trị này trước khi dựng widget.
    Thông báo thành công/cảnh báo là toast, lưu vào `_upload_toasts` để hiện sau rerun.
    """
    for icon, text in st.session_state.pop("_upload_toasts", []):
        st.toast(text, icon=icon)

    datasets = _datasets()
    n = st.session_state.setdefault("uploader_n", 0)
    file = st.file_uploader(
        "📁 Upload dữ liệu (CSV · Excel · SQLite)",
        type=UPLOAD_TYPES,
        key=f"uploader_{n}",
        help=f"Tối đa {max_upload_mb} MB. Mỗi lần upload tạo một dataset riêng; "
        "mỗi sheet Excel thành một bảng trong cùng dataset. Không hỗ trợ file .sql/.json.",
    )
    if file is not None:
        ext = file.name.rsplit(".", 1)[-1].lower() if "." in file.name else ""
        fmt = {"csv": "CSV", "xlsx": "Excel", "xlsm": "Excel"}.get(ext, "SQLite")
        st.caption(f"📄 **{file.name}** · {fmt} · {file.size / 1024:,.0f} KB")

    if st.button("⬆ Import dữ liệu", type="primary", disabled=file is None, width="stretch"):
        try:
            with st.spinner("Đang kiểm tra và chuẩn hóa dữ liệu về SQLite..."):
                info = client.upload_dataset(file.name, file.getvalue())
        except APIError as e:
            st.error(f"❌ Import thất bại: {e}")  # giữ nguyên tệp đã chọn để người dùng thử lại
        else:
            datasets[info["dataset_id"]] = info
            st.session_state["_select_dataset_label"] = dataset_label(info["dataset_id"])
            n_warn = len(info.get("warnings", []))
            st.session_state["_upload_toasts"] = [(
                "✅",
                f"Đã import **{info['filename']}** — đang dùng làm dataset phân tích."
                + (f" Có {n_warn} lưu ý, xem ở mục Schema." if n_warn else ""),
            )]
            st.session_state["uploader_n"] = n + 1  # làm trống ô chọn tệp
            st.rerun()


# ── Chọn dataset + schema + quan hệ (sidebar) ──────────────────────────────
def render_dataset_picker(demo_available: bool) -> Optional[str]:
    """Selectbox chọn dataset. Trả về dataset_id đang chọn (None = demo). Gọi trong `with st.sidebar`."""
    ids: List[Optional[str]] = ([None] if demo_available else []) + list(_datasets())
    if not ids:
        return None
    labels = {dataset_label(i): i for i in ids}
    names = list(labels)
    # Widget (`_dataset_picker`, lưu nhãn) là nguồn sự thật; upload yêu cầu chọn dataset mới qua khóa riêng.
    wanted = st.session_state.pop("_select_dataset_label", None)
    if wanted in labels:
        st.session_state["_dataset_picker"] = wanted
    if st.session_state.get("_dataset_picker") not in labels:
        st.session_state["_dataset_picker"] = names[0]
    choice = st.selectbox("Dataset đang phân tích", names, key="_dataset_picker")
    st.session_state["active_dataset"] = labels[choice]
    return labels[choice]


def render_schema_and_relations(client: NL2VizClient, dataset_id: Optional[str]):
    """Hiển thị schema; với dataset upload cho phép khai báo quan hệ. Trả về Schema."""
    schema = client.schema(dataset_id)
    for table, cols in schema.tables.items():
        with st.expander(f"📋 {table} ({len(cols)} cols)"):
            for c in cols:
                st.caption(f"• {c}")

    if dataset_id is None:
        return schema

    info = _datasets().get(dataset_id) or client.get_dataset(dataset_id)
    if info.get("warnings"):
        with st.expander(f"⚠️ Lưu ý khi import ({len(info['warnings'])})"):
            for w in info["warnings"]:
                st.caption(f"• {w}")
    declared, confirmed = info.get("relations", []), info.get("confirmed_relations", [])
    with st.expander(f"🔗 Quan hệ giữa các bảng ({len(declared) + len(confirmed)})"):
        st.caption(
            "Hệ thống chỉ JOIN các bảng có quan hệ dưới đây. Bảng không có quan hệ vẫn được truy vấn riêng; "
            "nếu câu hỏi cần JOIN, bạn sẽ được yêu cầu xác nhận quan hệ."
        )
        for r in declared:
            st.caption(f"🔒 {r['from_table']}.{r['from_column']} → {r['to_table']}.{r['to_column'] or '(PK)'}  _(khóa ngoại)_")
        for i, r in enumerate(confirmed):
            c1, c2 = st.columns([5, 1])
            c1.caption(f"✅ {r['from_table']}.{r['from_column']} → {r['to_table']}.{r['to_column']}")
            if c2.button("🗑", key=f"unlink_{dataset_id}_{i}", help="Xóa quan hệ"):
                _update_relation(client, dataset_id, r, remove=True)

        tables = list(schema.tables)
        if tables:
            st.markdown("**Thêm quan hệ**")
            ft = st.selectbox("Bảng nguồn", tables, key=f"rel_ft_{dataset_id}")
            fc = st.selectbox("Cột nguồn", schema.tables[ft], key=f"rel_fc_{dataset_id}_{ft}")
            tt = st.selectbox("Bảng đích", tables, key=f"rel_tt_{dataset_id}")
            tc = st.selectbox("Cột đích", schema.tables[tt], key=f"rel_tc_{dataset_id}_{tt}")
            if st.button("Xác nhận quan hệ", key=f"rel_add_{dataset_id}", width="stretch"):
                _update_relation(
                    client, dataset_id, dict(from_table=ft, from_column=fc, to_table=tt, to_column=tc), remove=False
                )
    return schema


def _update_relation(client: NL2VizClient, dataset_id: str, relation: Dict[str, str], *, remove: bool) -> None:
    try:
        rel = {k: relation[k] for k in ("from_table", "from_column", "to_table", "to_column")}
        info = client.remove_relation(dataset_id, rel) if remove else client.add_relation(dataset_id, rel)
    except APIError as e:
        st.error(f"❌ {e}")
        return
    _datasets()[dataset_id] = info
    st.rerun()
