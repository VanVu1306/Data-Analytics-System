# Kế hoạch: Upload dữ liệu và phân tích bằng ngôn ngữ tự nhiên

## Mục tiêu

Cho phép người dùng tải dữ liệu của họ lên, đặt câu hỏi bằng ngôn ngữ tự nhiên và nhận:

- Câu trả lời dạng văn bản dựa trên dữ liệu.
- SQL đã thực thi và bảng kết quả làm bằng chứng.
- Biểu đồ khi người dùng yêu cầu.

## Phạm vi và quyết định cần chốt

- [ ] Xác định định dạng cần hỗ trợ ở phiên bản đầu: Excel (`.xlsx`), JSON (`.json`), CSV và SQLite (`.db`/`.sqlite`).
- [ ] Làm rõ “SQL”: file database SQLite hay file script `.sql`. Nếu hỗ trợ script, import vào database cô lập; không chạy script trên database hệ thống.
- [ ] Chọn chính sách lưu dữ liệu: chỉ trong phiên hay lưu lâu dài; xác định thời hạn lưu và quy tắc xóa.
- [ ] Chốt giới hạn kích thước tệp, số dòng, số bảng/sheet và cấu trúc JSON được chấp nhận.
- [ ] Xác định mô hình định danh và cô lập dataset theo người dùng/phiên. Không dùng ID do client tự chọn làm đường dẫn lưu trực tiếp.

## Giai đoạn 1 — Upload và import dataset

- [ ] Thêm API upload, ví dụ `POST /api/datasets`, nhận tệp multipart/form-data.
- [ ] Kiểm tra định dạng theo nội dung và phần mở rộng, dung lượng, cấu trúc, dữ liệu lỗi và tên tệp.
- [ ] Tạo bộ import theo định dạng:
  - [ ] Excel: import từng sheet thành bảng, xử lý tên bảng/cột và kiểu dữ liệu.
  - [ ] JSON: xác định cấu trúc hợp lệ và ánh xạ bản ghi thành bảng/cột.
  - [ ] CSV: import thành bảng với kiểu dữ liệu phù hợp.
  - [ ] SQLite: xác thực database và chỉ cho phép đọc dữ liệu.
  - [ ] SQL script (nếu thuộc phạm vi): chỉ import trong database tạm/cô lập với chính sách câu lệnh được phép.
- [ ] Lưu dataset ở vị trí do backend quản lý; tạo `dataset_id` không đoán được và metadata cần thiết.
- [ ] Trả về `dataset_id`, tên dataset, danh sách bảng, trạng thái import và thông báo lỗi cụ thể.
- [ ] Thêm kiểm thử cho upload hợp lệ, tệp rỗng/hỏng, định dạng không hỗ trợ, vượt giới hạn và lỗi import.

**Backend dự kiến:** module import/dataset mới dưới `backend/`, API và schema trong `backend/api/`, cấu hình giới hạn/lưu trữ trong `backend/config.py`.

## Giai đoạn 2 — Khám phá và quản lý dataset

- [ ] Thêm API lấy schema theo dataset, ví dụ `GET /api/datasets/{dataset_id}/schema`.
- [ ] Thêm API xem preview giới hạn số dòng, ví dụ `GET /api/datasets/{dataset_id}/preview`.
- [ ] Thêm API liệt kê dataset hiện có nếu chính sách lưu trữ cho phép.
- [ ] Thêm API xóa dataset, ví dụ `DELETE /api/datasets/{dataset_id}`, xóa cả tệp dữ liệu và metadata liên quan.
- [ ] Trả lỗi rõ ràng khi dataset không tồn tại, hết hạn hoặc người gọi không có quyền truy cập.
- [ ] Kiểm thử đảm bảo dataset của phiên/người dùng này không thể truy cập từ phiên/người dùng khác.

**Backend dự kiến:** thay thế giả định một database cố định trong `backend/container.py` bằng cách chọn repository theo dataset đã xác thực. Repository cần cung cấp schema, mẫu dữ liệu và truy vấn cho đúng dataset.

## Giai đoạn 3 — Hỏi đáp trên dataset đã chọn

- [ ] Mở rộng request truy vấn để nhận `dataset_id`, câu hỏi và tùy chọn biểu đồ.
- [ ] Truyền schema cùng một số dòng mẫu của dataset đã chọn vào bước sinh SQL.
- [ ] Thay các ví dụ few-shot cố định theo database bán hàng bằng ví dụ phù hợp hơn với schema, hoặc loại bỏ ví dụ không liên quan.
- [ ] Thực thi truy vấn chỉ đọc; từ chối lệnh ghi/thay đổi schema như `INSERT`, `UPDATE`, `DELETE`, `DROP` và các câu lệnh nhiều statement.
- [ ] Giới hạn thời gian chạy, số dòng và kích thước kết quả để tránh truy vấn tốn tài nguyên.
- [ ] Trả SQL, lỗi truy vấn và bảng kết quả nhất quán; không biến lỗi sinh SQL thành một câu SQL có vẻ hợp lệ.
- [ ] Kiểm thử truy vấn thành công, lỗi SQL, kết quả rỗng, truy vấn bị từ chối và giới hạn kết quả.

**Backend dự kiến:** cập nhật `backend/agents/sql_agent.py`, `backend/pipeline/orchestrator.py`, `backend/tools/sql_tools.py`, `backend/database/` và hợp đồng API trong `backend/api/schemas.py`.

## Giai đoạn 4 — Câu trả lời dạng text và biểu đồ tùy chọn

- [ ] Bổ sung bước tạo câu trả lời ngắn dựa trên câu hỏi và kết quả truy vấn.
- [ ] Đảm bảo câu trả lời không đưa ra con số/nhận định không có trong kết quả truy vấn; báo rõ khi dữ liệu không đủ.
- [ ] Trả lời API có các trường: câu trả lời, SQL, bảng kết quả, trạng thái, lỗi và thông tin biểu đồ.
- [ ] Chỉ chạy bước tạo biểu đồ khi người dùng yêu cầu; hỗ trợ lựa chọn loại biểu đồ hoặc chế độ tự chọn.
- [ ] Tái sử dụng `VizAgent` và `RenderChartTool` nếu phù hợp; không để lỗi biểu đồ làm mất câu trả lời và bảng truy vấn đã có.
- [ ] Kiểm thử câu trả lời có căn cứ, kết quả rỗng/lỗi và biểu đồ được yêu cầu/không được yêu cầu.

**Backend dự kiến:** mở rộng `PipelineRequest`, `PipelineResult`, `PipelineOrchestrator` và response schema; có thể bổ sung agent tạo câu trả lời text nếu cần tách trách nhiệm.

## Giai đoạn 5 — Giao diện người dùng

- [ ] Thêm khu vực upload và hiển thị tiến độ/trạng thái import.
- [ ] Cho phép chọn dataset đang hoạt động.
- [ ] Hiển thị schema và preview để người dùng kiểm tra dữ liệu đã nạp.
- [ ] Gắn câu hỏi và tùy chọn biểu đồ với dataset đang chọn.
- [ ] Hiển thị câu trả lời text, SQL, bảng bằng chứng và biểu đồ (nếu có).
- [ ] Cho phép xem danh sách/xóa dataset theo chính sách lưu trữ.
- [ ] Hiển thị lỗi upload, truy vấn và quyền truy cập một cách rõ ràng.

**Frontend dự kiến:** bổ sung API client trong `frontend/api_client.py`; cập nhật luồng trang trong `frontend/app.py` và giao diện trong `frontend/ui/`.

## Giai đoạn 6 — Bảo vệ dữ liệu và hoàn thiện

- [ ] Không tin cậy tên tệp, phần mở rộng, MIME type hoặc `dataset_id` từ client.
- [ ] Cô lập dữ liệu và quyền truy cập giữa các dataset; không để truy vấn rơi về demo database khi dataset lỗi/không tồn tại.
- [ ] Đặt giới hạn upload, import, thời gian truy vấn và kích thước phản hồi.
- [ ] Có cơ chế dọn dataset hết hạn và xử lý tệp tạm khi import thất bại.
- [ ] Không ghi nội dung dữ liệu nhạy cảm vào log hoặc gửi toàn bộ dữ liệu lên dịch vụ LLM nếu không cần thiết.
- [ ] Cập nhật README về định dạng hỗ trợ, giới hạn, cách chạy, chính sách lưu/xóa và các biến cấu hình mới.
- [ ] Chạy kiểm thử backend/API và kiểm tra thủ công luồng frontend hoàn chỉnh.

## Tiêu chí hoàn thành

- Người dùng có thể tải định dạng nằm trong phạm vi hỗ trợ và thấy schema/preview của dataset đã import.
- Câu hỏi được thực thi trên đúng dataset đã chọn; dữ liệu giữa các dataset được cô lập.
- Phản hồi có câu trả lời text và bảng kết quả cùng SQL làm bằng chứng.
- Biểu đồ chỉ được tạo khi được yêu cầu; lỗi biểu đồ không làm mất kết quả truy vấn.
- Upload lỗi, dataset không hợp lệ/không tồn tại và SQL không an toàn đều bị từ chối với thông báo rõ ràng.
- Các kiểm thử liên quan đến import, API, cô lập dataset và pipeline đều đạt.
