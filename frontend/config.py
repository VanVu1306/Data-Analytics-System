"""
frontend/config.py
------------------
Cấu hình phía frontend. Frontend chỉ biết địa chỉ backend, không biết gì về LLM hay DB.
"""
import os

API_BASE_URL = os.getenv("NL2VIZ_API_URL", "http://127.0.0.1:8000")
HEALTH_TIMEOUT = 5      # giây
QUERY_TIMEOUT = 300     # giây (Llama3 local có thể chậm)
UPLOAD_TIMEOUT = 120    # giây (import tệp lớn)

DEFAULT_QUESTION = "Show monthly revenue in 2024"
CHART_HINTS = ["auto", "bar chart", "line chart", "pie chart", "scatter plot"]
SUGGESTIONS = [
    "Show monthly revenue in 2024",
    "Revenue by product category",
    "Revenue by region",
    "Top 5 best-selling products",
    "Revenue by customer",
]

# Định dạng cho phép upload (backend vẫn kiểm tra lại nội dung, không tin phần mở rộng)
UPLOAD_TYPES = ["csv", "xlsx", "xlsm", "db", "sqlite", "sqlite3"]
FORMAT_LABELS = {"csv": "CSV", "excel": "Excel", "sqlite": "SQLite"}
