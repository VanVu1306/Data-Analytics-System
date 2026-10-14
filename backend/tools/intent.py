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

CHART_TYPES = (
    "bar chart", "line chart", "pie chart", "scatter plot",          # so sánh / xu hướng / thành phần / quan hệ
    "histogram", "kde plot", "box plot", "violin plot",              # phân phối / ngoại lai
    "heatmap", "count plot",                                         # tương quan / tần suất nhóm
)
CHART_GOALS = ("comparison", "trend", "distribution", "relationship", "composition")
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
# Thứ tự ưu tiên: loại chuyên biệt (violin > box > kde > heatmap > histogram > count) rồi pie > scatter > line > bar.
# Chỉ xét khi đã có từ chỉ biểu đồ (vì "cột" còn nghĩa là column, "đường" có thể là nghĩa khác).
_TYPE_PATTERNS = (
    ("violin plot", re.compile(r"\bviolin\b")),
    ("box plot", re.compile(r"\bbox\s*-?plots?\b|\bboxplots?\b|biểu\s*đồ\s*hộp|\bhộp\s*râu|hộp\s*và\s*râu")),
    ("kde plot", re.compile(r"\bkde\b|mật\s*độ|\bdensity\b")),
    ("heatmap", re.compile(r"\bheat\s*-?map\b|bản\s*đồ\s*nhiệt|ma\s*trận\s*tương\s*quan|correlation\s*matrix")),
    ("histogram", re.compile(r"\bhistograms?\b|biểu\s*đồ\s*tần\s*suất|tần\s*số\s*phân\s*bố")),
    ("count plot", re.compile(r"\bcount\s*-?plots?\b|biểu\s*đồ\s*đếm|đếm\s*(?:số\s*)?(?:lần|lượng)\s*xuất\s*hiện")),
    ("pie chart", re.compile(r"\bpie\b|\bdonut\b|hình\s*tròn|\btròn\b|hình\s*quạt")),
    ("scatter plot", re.compile(r"\bscatter\b|phân\s*tán")),
    ("line chart", re.compile(r"\bline\b|\bđường\b|xu\s*hướng|\btrends?\b")),
    # "cột" đứng một mình thường là column của bảng: chỉ coi là biểu đồ khi đi kèm từ chỉ biểu đồ/kiểu vẽ
    ("bar chart", re.compile(r"\bbar\b|\bcolumn\s*chart\b|(?:biểu\s*đồ|vẽ|dạng|kiểu|chart)\s+(?:dạng\s+)?cột\b|\bcột\s*(?:chồng|nhóm)\b")),
)
# Mục tiêu trực quan (dùng để chọn chart khi không nêu loại). Thứ tự = ưu tiên khi nhiều mục tiêu cùng khớp.
_GOAL_PATTERNS = (
    ("trend", re.compile(r"xu\s*hướng|\btrends?\b|theo\s*thời\s*gian|over\s*time|tăng\s*trưởng|\bgrowth\b|biến\s*động|"
                         r"theo\s*(?:tháng|năm|quý|tuần|ngày)|\bby\s*(?:month|year|quarter|week|day)\b|\bmonthly\b|\bdaily\b")),
    ("composition", re.compile(r"tỷ\s*lệ|tỉ\s*lệ|cơ\s*cấu|tỷ\s*trọng|thành\s*phần|\bproportions?\b|\bshare\b|percentage|phần\s*trăm|\bcomposition\b")),
    ("relationship", re.compile(r"quan\s*hệ|tương\s*quan|\brelationship\b|\bcorrelat\w*|ảnh\s*hưởng|\bvs\.?\b|\bversus\b")),
    ("distribution", re.compile(r"phân\s*phối|phân\s*bố|\bdistribution\b|tần\s*suất|ngoại\s*lai|\boutliers?\b")),
    ("comparison", re.compile(r"so\s*sánh|\bcompar\w*|cao\s*nhất|thấp\s*nhất|xếp\s*hạng|\branking\b|\bhighest\b|\blowest\b")),
)
_SQL_REQUEST = re.compile(
    r"\bsql\b|câu\s*(?:lệnh\s*)?truy\s*vấn|\bthe\s+query\b|\bwhich\s+query\b"
)


def _norm(text: str) -> str:
    return unicodedata.normalize("NFC", text or "").lower()


@dataclass(frozen=True)
class ChartPlan:
    requested: bool
    chart_type: Optional[str] = None  # None = tự chọn loại theo mục tiêu + kiểu dữ liệu
    origin: str = "none"              # none | option | prompt
    goal: Optional[str] = None        # comparison | trend | distribution | relationship | composition (gợi ý khi tự chọn)


def detect_chart_request(question: str) -> Optional[ChartPlan]:
    """Trả về ChartPlan(origin="prompt") nếu câu hỏi yêu cầu vẽ biểu đồ, ngược lại None."""
    q = _norm(question)
    if _NEGATED_CHART.search(q) or not _CHART_WORD.search(q):
        return None
    return ChartPlan(True, _detect_type(q), "prompt", detect_chart_goal(question))


def detect_chart_goal(question: str) -> Optional[str]:
    """Mục tiêu trực quan nêu trong câu hỏi (so sánh, xu hướng, phân phối, quan hệ, thành phần); None nếu không rõ."""
    q = _norm(question)
    for goal, pattern in _GOAL_PATTERNS:
        if pattern.search(q):
            return goal
    return None


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
        return ChartPlan(True, chart_hint, "option", detect_chart_goal(question))
    if chart_hint == "auto":
        return ChartPlan(True, _detect_type(_norm(question)), "option", detect_chart_goal(question))
    return detect_chart_request(question) or ChartPlan(False)


def detect_sql_request(question: str) -> bool:
    """Câu hỏi có yêu cầu xem SQL/câu truy vấn không."""
    return bool(_SQL_REQUEST.search(_norm(question)))
