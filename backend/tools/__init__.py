"""Tools và hàm thuần mà agents/pipeline dùng.

Phân loại (Giai đoạn 7):
  * Tool THỰC THI (đăng ký trong ToolRegistry, gọi theo tên): schema/SQL/dataset (`data_tools`), 5 EDA tool (`eda_tools`),
    dựng + vẽ biểu đồ (`chart_tools`, `chart_render`, `viz_tools`), mock (`mocks`).
  * Template FALLBACK cho câu trả lời: `answer_templates` — một tool `answer_template` (query / EDA / query + EDA).
  * Hàm TIỆN ÍCH THUẦN (không đăng ký): `answer_evidence` (bằng chứng, ngữ cảnh LLM, kiểm tra bám số liệu),
    `text_utils` (làm sạch output LLM, nhận biết ngôn ngữ), `intent` (nhận diện yêu cầu vẽ/xem SQL), `planner`
    (validator kế hoạch), `chart_select` (chọn loại biểu đồ).
"""
from backend.tools.answer_evidence import (
    AnswerEvidence,
    EvidenceContext,
    QueryEvidence,
    answer_is_grounded,
    build_evidence_context,
)
from backend.tools.answer_templates import AnswerTemplateTool, describe_payload, missing_caveats, template_answer
from backend.tools.base import Tool, ToolRegistry
from backend.tools.eda_tools import EDA_TOOL_CLASSES, EdaPayload
from backend.tools.intent import CHART_GOALS, CHART_TYPES, detect_chart_goal, detect_chart_request, detect_sql_request, resolve_chart_plan
from backend.tools.mocks import MockSQLTool, MockVizCodeTool, mock_sql, mock_viz_code
from backend.tools.data_tools import (
    DescribeSchemaTool,
    ExecuteSQLTool,
    GetDatasetTool,
    GetSchemaTool,
    ImportDatasetTool,
    LinkTablesTool,
    ListTablesTool,
    UnlinkTablesTool,
)
from backend.tools.text_utils import clean_code, extract_sql, is_valid_code, is_vietnamese
from backend.tools.chart_render import RenderChartSpecTool, render_png
from backend.tools.chart_select import ChartError, ChartLimits, build_from_frame
from backend.tools.chart_tools import BuildChartSpecTool
from backend.tools.viz_tools import RenderChartTool

__all__ = [
    "Tool", "ToolRegistry",
    # tool thực thi
    "GetSchemaTool", "ListTablesTool", "DescribeSchemaTool", "ExecuteSQLTool",
    "ImportDatasetTool", "GetDatasetTool", "LinkTablesTool", "UnlinkTablesTool",
    "EDA_TOOL_CLASSES", "EdaPayload",
    "BuildChartSpecTool", "RenderChartSpecTool", "RenderChartTool", "render_png",
    "MockSQLTool", "MockVizCodeTool", "mock_sql", "mock_viz_code",
    # câu trả lời text: bằng chứng, template fallback, kiểm tra bám số liệu
    "AnswerEvidence", "QueryEvidence", "EvidenceContext", "build_evidence_context", "answer_is_grounded",
    "AnswerTemplateTool", "template_answer", "describe_payload", "missing_caveats",
    # hàm thuần: biểu đồ, ý định, văn bản
    "ChartError", "ChartLimits", "build_from_frame",
    "CHART_GOALS", "CHART_TYPES", "detect_chart_goal", "detect_chart_request", "detect_sql_request", "resolve_chart_plan",
    "clean_code", "extract_sql", "is_valid_code", "is_vietnamese",
]
