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

# Gợi ý câu hỏi cho demo DB, hiển thị dạng bong bóng phía trên ô nhập. Loại biểu đồ do người dùng nêu
# trực tiếp trong câu hỏi (cột / đường / tròn / phân tán).
SUGGESTIONS = [
    "Show monthly revenue in 2024",
    "Vẽ biểu đồ cột doanh thu theo danh mục sản phẩm",
    "Vẽ biểu đồ đường doanh thu theo tháng năm 2024",
    "Vẽ biểu đồ tròn tỷ lệ doanh thu theo vùng",
    "Vẽ biểu đồ phân tán số lượng và doanh thu của đơn hàng",
]

# Định dạng cho phép upload (backend vẫn kiểm tra lại nội dung, không tin phần mở rộng)
UPLOAD_TYPES = ["csv", "xlsx", "xlsm", "db", "sqlite", "sqlite3"]
FORMAT_LABELS = {"csv": "CSV", "excel": "Excel", "sqlite": "SQLite"}
