"""
backend/tools/intent.py
-----------------------
Hàm thuần nhận biết ý định trong câu hỏi tự nhiên (không gọi LLM):

  * người dùng có yêu cầu vẽ biểu đồ không, và loại biểu đồ nào;
  * người dùng có yêu cầu xem SQL không.

Kết hợp với tùy chọn phụ `chart_hint` của API thành một `ChartPlan`.
Mặc định KHÔNG vẽ nếu người dùng không yêu cầu.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Optional

CHART_TYPES = ("bar chart", "line chart", "pie chart", "scatter plot")
CHART_HINTS = ("detect", "none", "auto") + CHART_TYPES

_CHART_WORD = re.compile(
    r"biểu\s*đồ|đồ\s*thị|sơ\s*đồ|trực\s*quan|\bvẽ\b|"
    r"\bcharts?\b|\bplots?\b|\bgraphs?\b|\bvisuali[sz]\w*|\bhistogram\b|\bdraw\b"
)
_NEGATED_CHART = re.compile(
    r"(?:không|đừng|chẳng|khỏi|\bno\b|\bwithout\b|\bdon'?t\b|\bdo not\b)\s+"
    r"(?:cần\s+|phải\s+|need\s+|want\s+)?(?:(?:a|any|the)\s+)?"
    r"(?:vẽ|biểu\s*đồ|đồ\s*thị|chart|plot|graph|draw|visuali)"
)
# Thứ tự ưu tiên: pie > scatter > line > bar. Chỉ xét khi đã có từ chỉ biểu đồ
# (vì "cột" còn nghĩa là column, "đường" có thể là nghĩa khác).
_TYPE_PATTERNS = (
    ("pie chart", re.compile(r"\bpie\b|\bdonut\b|hình\s*tròn|\btròn\b|hình\s*quạt")),
    ("scatter plot", re.compile(r"\bscatter\b|phân\s*tán")),
    ("line chart", re.compile(r"\bline\b|\bđường\b|xu\s*hướng|\btrends?\b")),
    ("bar chart", re.compile(r"\bbar\b|\bcột\b|\bcolumns?\b|\bhistogram\b")),
)
_SQL_REQUEST = re.compile(
    r"\bsql\b|câu\s*(?:lệnh\s*)?truy\s*vấn|\bthe\s+query\b|\bwhich\s+query\b"
)


def _norm(text: str) -> str:
    return unicodedata.normalize("NFC", text or "").lower()


@dataclass(frozen=True)
class ChartPlan:
    requested: bool
    chart_type: Optional[str] = None  # None = tự chọn loại
    origin: str = "none"              # none | option | prompt


def detect_chart_request(question: str) -> Optional[ChartPlan]:
    """Trả về ChartPlan(origin="prompt") nếu câu hỏi yêu cầu vẽ biểu đồ, ngược lại None."""
    q = _norm(question)
    if _NEGATED_CHART.search(q) or not _CHART_WORD.search(q):
        return None
    return ChartPlan(True, _detect_type(q), "prompt")


def _detect_type(normalized_question: str) -> Optional[str]:
    for name, pattern in _TYPE_PATTERNS:
        if pattern.search(normalized_question):
            return name
    return None


def resolve_chart_plan(question: str, chart_hint: str = "detect") -> ChartPlan:
    """
    Kết hợp tùy chọn phụ và nội dung câu hỏi:
      none     -> không vẽ (kể cả khi câu hỏi yêu cầu)
      auto     -> vẽ, tự chọn loại (loại nêu trong câu hỏi, nếu có, được ưu tiên)
      <loại>   -> vẽ đúng loại đó
      detect   -> (mặc định) chỉ vẽ khi câu hỏi yêu cầu
    """
    if chart_hint == "none":
        return ChartPlan(False, None, "option")
    if chart_hint in CHART_TYPES:
        return ChartPlan(True, chart_hint, "option")
    if chart_hint == "auto":
        return ChartPlan(True, _detect_type(_norm(question)), "option")
    return detect_chart_request(question) or ChartPlan(False)


def detect_sql_request(question: str) -> bool:
    """Câu hỏi có yêu cầu xem SQL/câu truy vấn không."""
    return bool(_SQL_REQUEST.search(_norm(question)))
