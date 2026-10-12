"""
backend/tools/viz_tools.py
--------------------------
Tool chạy code matplotlib do LLM sinh ra, trả về ảnh PNG.

LƯU Ý BẢO MẬT: `exec` bên dưới CHƯA phải sandbox thật (xem README, mục
"Hành vi giữ nguyên"). Chỉ chạy backend trên localhost cho tới khi có sandbox.
"""
from __future__ import annotations

import ast
import io
import threading
import traceback
from dataclasses import dataclass
from typing import Optional, Tuple

import matplotlib

matplotlib.use("Agg")  # non-interactive backend
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

from backend.tools.base import Tool  # noqa: E402

# pyplot dùng state toàn cục (figure hiện tại) -> tuần tự hóa các lần vẽ
# vì FastAPI chạy endpoint đồng bộ trong thread pool.
_PLOT_LOCK = threading.Lock()


@dataclass
class ChartRender:
    png: Optional[bytes]
    error: Optional[str]


def execute_viz_code(
    code: str,
    df: pd.DataFrame,
) -> Tuple[Optional[plt.Figure], Optional[str]]:
    """
    Thực thi code Python với `df` có sẵn.
    AST transform: thay Ellipsis (...) bằng `df` để tránh lỗi data=Ellipsis.

    Returns: (figure, error_msg)
    """
    plt.close("all")  # dọn figure cũ

    try:
        tree = ast.parse(code)

        class EllipsisToDf(ast.NodeTransformer):
            def visit_Constant(self, node):
                if node.value is Ellipsis:
                    return ast.copy_location(ast.Name(id="df", ctx=ast.Load()), node)
                return node

            # Fallback cho Python cũ
            def visit_Ellipsis(self, node):
                return ast.copy_location(ast.Name(id="df", ctx=ast.Load()), node)

        tree = EllipsisToDf().visit(tree)
        ast.fix_missing_locations(tree)
        compiled = compile(tree, "<viz_code>", "exec")
    except SyntaxError as e:
        return None, f"SyntaxError: {e}\n\nCode:\n{code[:500]}"

    namespace = {
        "df": df.copy(),
        "pd": pd,
        "plt": plt,
    }
    try:
        import seaborn as sns

        namespace["sns"] = sns
    except ImportError:
        pass
    try:
        import numpy as np

        namespace["np"] = np
    except ImportError:
        pass

    try:
        exec(compiled, namespace)
        fig = plt.gcf()
        if fig.get_axes():
            return fig, None
        else:
            return None, "Code chạy nhưng không tạo ra biểu đồ nào (no axes)."
    except Exception:
        return None, traceback.format_exc()


class RenderChartTool(Tool):
    name = "render_chart"
    description = "Chạy code matplotlib trên DataFrame và trả về ảnh PNG (hoặc lỗi)."

    def __init__(self, dpi: int = 100):
        self._dpi = dpi

    def run(self, code: str, df: pd.DataFrame) -> ChartRender:
        with _PLOT_LOCK:
            fig, error = execute_viz_code(code, df)
            if error or fig is None:
                plt.close("all")
                return ChartRender(png=None, error=error)
            try:
                buf = io.BytesIO()
                fig.savefig(buf, format="png", dpi=self._dpi, bbox_inches="tight")
                return ChartRender(png=buf.getvalue(), error=None)
            except Exception:
                return ChartRender(png=None, error=traceback.format_exc())
            finally:
                plt.close("all")
