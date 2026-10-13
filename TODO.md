# TODO: Upload dữ liệu và phân tích bằng ngôn ngữ tự nhiên

## Mục tiêu

Cho phép người dùng upload dữ liệu ngay trên UI, chuẩn hóa dữ liệu về SQLite để tái sử dụng pipeline hiện tại, chọn dataset vừa upload và hỏi bằng ngôn ngữ tự nhiên. Hệ thống trả câu trả lời dạng text, SQL và bảng kết quả làm bằng chứng; vẽ biểu đồ khi người dùng yêu cầu.

## Nguyên tắc thiết kế

- Mỗi lần upload tạo một dataset/database SQLite riêng, không ghép vào `demo_db/sales_demo.db` hoặc dataset khác.
- Lưu database upload trong vùng lưu trữ do backend quản lý; client chỉ nhận và gửi `dataset_id`, không cung cấp đường dẫn file.
- Các bảng/sheet trong cùng một file Excel được import vào cùng database SQLite để có thể truy vấn cùng nhau.
- Chỉ JOIN khi có quan hệ khóa ngoại hoặc quan hệ đã được người dùng xác nhận. Không tự nối bảng chỉ vì tên cột giống nhau.
- Nếu dữ liệu không có quan hệ, vẫn cho phép truy vấn từng bảng độc lập. Nếu câu hỏi đòi hỏi kết hợp bảng nhưng không xác định được quan hệ, yêu cầu người dùng làm rõ thay vì tự suy đoán.
- MVP không thực thi file script `.sql` tùy ý. Ưu tiên nhận CSV, Excel, JSON dạng bảng và SQLite database; làm rõ hỗ trợ `.sql` script trong giai đoạn sau.

## Giai đoạn 1 — MVP: Upload trên UI và chuẩn hóa thành SQLite

### Frontend

- [X] Thêm nút/khu vực upload dữ liệu trong giao diện Streamlit, dùng bộ chọn tệp và hiển thị tên tệp, định dạng, tiến độ/trạng thái.
- [X] Cho phép người dùng upload CSV, Excel (`.xlsx`) và SQLite (`.db`/`.sqlite`).
- [X] Sau khi upload thành công, hiển thị thông báo, `dataset_id`, các bảng đã import và số dòng; cho phép chọn dataset vừa upload làm dataset đang phân tích.
- [X] Hiển thị lỗi định dạng/import rõ ràng và giữ lại giao diện để người dùng có thể thử lại.

### Backend

- [X] Thêm API nhận upload qua multipart/form-data, ví dụ `POST /api/datasets`.
- [X] Kiểm tra phần mở rộng và nội dung tệp, kích thước, tệp rỗng/hỏng và cấu trúc trước khi import.
- [X] Chuẩn hóa dữ liệu về một SQLite database riêng cho mỗi upload:

  - [X] CSV: một tệp thành một bảng.
  - [X] Excel: mỗi sheet thành một bảng trong cùng database.
  - [X] SQLite: kiểm tra database hợp lệ và sao chép/import vào vùng lưu trữ do backend quản lý.
- [X] Chuẩn hóa tên bảng/cột và kiểu dữ liệu; xử lý tên trùng, ô rỗng, giá trị không đồng nhất và tên sheet không hợp lệ một cách nhất quán.
- [X] Lưu metadata gắn `dataset_id` với đường dẫn SQLite do server tạo, tên nguồn, định dạng, thời điểm upload và trạng thái import.
- [X] Trả `dataset_id`, schema/tên bảng, số dòng và thông tin lỗi/cảnh báo import về frontend.
- [X] Thêm giới hạn kích thước upload và dọn tệp tạm khi import thất bại.

### Nối vào backend hiện tại để chạy được

- [X] Thay giả định database cố định trong `backend/container.py` bằng khả năng tạo/chọn repository theo `dataset_id`.
- [X] Thêm registry/store dataset phía backend để tra `dataset_id` thành đường dẫn database; không dùng giá trị client gửi để tạo đường dẫn trực tiếp.
- [X] Cập nhật API schema/request để nhận `dataset_id` khi xem schema và chạy truy vấn.
- [X] Đảm bảo `get_schema`, `execute_sql` và pipeline cùng dùng đúng SQLite database của dataset đang chọn.
- [X] Giữ demo database làm dataset mặc định cho luồng cũ, nhưng không dùng nó làm nơi nhập dữ liệu upload.
- [X] Kiểm thử upload → nhận `dataset_id` → đọc schema → chạy truy vấn thành công trên SQLite vừa tạo; đồng thời xác minh dữ liệu demo không bị thay đổi.

**Thành phần dự kiến:** `frontend/app.py`, `frontend/api_client.py`, `frontend/ui/`; API và schema trong `backend/api/`; importer/registry mới trong `backend/`; repository, container và pipeline hiện có.

## Giai đoạn 2 — Hỏi đáp trên dataset được chọn

- [X] Truyền `dataset_id` qua request truy vấn; từ chối dataset không tồn tại hoặc không khả dụng, không fallback sang demo database.
- [X] Đọc schema của dataset đang chọn và dùng schema đó trong prompt sinh SQL.
- [X] Không dùng few-shot bán hàng cố định nếu chúng không phù hợp với schema của dataset; cập nhật prompt để SQL chỉ tham chiếu các bảng/cột hiện có.
- [X] Tận dụng khóa ngoại SQLite khi dựng schema/prompt. Cho phép các bảng không liên quan được truy vấn riêng.
- [X] Với bảng không có khóa ngoại, không tự JOIN dựa riêng vào tên cột. Nếu câu hỏi cần JOIN, yêu cầu người dùng xác nhận bảng/cột liên kết hoặc làm rõ câu hỏi.
- [X] Thêm cơ chế khai báo quan hệ thủ công giữa bảng trong cùng dataset; chỉ đưa quan hệ đã xác nhận vào prompt.
- [X] Chỉ cho phép truy vấn đọc; từ chối lệnh ghi/thay đổi schema và nhiều statement.
- [X] Giới hạn thời gian thực thi, số dòng và kích thước kết quả.
- [X] Kiểm thử truy vấn thành công, SQL lỗi, kết quả rỗng, SQL không an toàn, dataset sai ID và không có quan hệ JOIN.

**Thành phần dự kiến:** `backend/agents/sql_agent.py`, `backend/prompts/sql_prompts.py`, `backend/pipeline/orchestrator.py`, `backend/tools/sql_tools.py`, `backend/database/`, `backend/api/schemas.py`.

## Giai đoạn 3 — Kết quả text, bằng chứng bảng và biểu đồ theo yêu cầu

- [ ] Bổ sung trả lời text ngắn cho kết quả truy vấn (hiện tại nhập prompt --> hiện trực tiếp SQL); không khẳng định số liệu không có trong kết quả.
- [ ] Hiển thị SQL đã thực thi và bảng kết quả làm bằng chứng cho câu trả lời.
- [ ] Cho phép người dùng chọn không vẽ, tự chọn loại biểu đồ hoặc yêu cầu loại biểu đồ cụ thể.
- [ ] Chỉ chạy `VizAgent`/`RenderChartTool` khi biểu đồ được yêu cầu; tái sử dụng phần vẽ hiện tại nếu phù hợp.
- [ ] Nếu tạo biểu đồ lỗi, vẫn giữ câu trả lời và bảng dữ liệu đã truy vấn thành công.
- [ ] Kiểm thử trường hợp câu trả lời có kết quả, không có dữ liệu, truy vấn lỗi, biểu đồ không được yêu cầu và biểu đồ lỗi.

**Thành phần dự kiến:** mở rộng `PipelineRequest`, `PipelineResult`, `PipelineOrchestrator`, API response và phần hiển thị kết quả trong `frontend/ui/main_view.py`.

## Giai đoạn 4 — Quản lý dataset và quan hệ

- [ ] Thêm API xem danh sách dataset nếu chính sách lưu trữ cho phép.
- [ ] Thêm API xem schema/preview dữ liệu của dataset, giới hạn số dòng trả về.
- [ ] Thêm UI chọn dataset đã upload, xem schema/preview và quản lý quan hệ giữa các bảng.
- [ ] Thêm API/UI xóa dataset, xóa cả metadata và file SQLite tương ứng.
- [ ] Xác định chính sách lưu dữ liệu, thời hạn tồn tại và dọn dataset hết hạn.
- [ ] Nếu chưa có tài khoản người dùng, xác định rõ dataset thuộc phiên nào và cách cô lập giữa các phiên; nếu có đăng nhập, kiểm tra quyền sở hữu ở mọi endpoint.
- [ ] Kiểm thử truy cập chéo dataset/phiên bị từ chối và thao tác xóa không ảnh hưởng dataset khác.

## Giai đoạn 5 — Hoàn thiện, an toàn và tài liệu

- [ ] Không tin cậy tên tệp, MIME type, `dataset_id` hoặc đường dẫn do client gửi.
- [ ] Đặt giới hạn upload/import/truy vấn; không thực thi `.sql` script tùy ý trên môi trường ứng dụng.
- [ ] Không ghi dữ liệu nhạy cảm vào log hoặc gửi toàn bộ dữ liệu lên LLM khi không cần thiết; chỉ gửi schema và lượng mẫu tối thiểu phù hợp.
- [ ] Cập nhật README về định dạng được hỗ trợ, quy tắc chuẩn hóa, giới hạn, chính sách lưu/xóa và cách sử dụng chức năng upload.
- [ ] Chạy các kiểm thử backend/API và kiểm tra thủ công luồng hoàn chỉnh trên UI.

## Mở rộng — Nếu được yêu cầu

- [ ] Tạo luồng điều phối LangGraph để agent hóa toàn bộ hệ thống.

## Tiêu chí hoàn thành MVP

- Người dùng upload được CSV, Excel hoặc SQLite từ UI và nhận trạng thái import rõ ràng.
- Mỗi upload thành một SQLite database riêng; upload không ghi đè demo database hay dataset khác.
- Người dùng chọn dataset vừa upload và chạy được pipeline hiện tại trên đúng dữ liệu đó.
- Schema và bảng kết quả phản ánh dataset đã chọn; không tự fallback sang database khác.
- Các bảng không liên quan vẫn truy vấn riêng được; không JOIN nếu không có quan hệ xác định/được xác nhận.
- Tệp sai định dạng, hỏng, vượt giới hạn hoặc JSON chưa hỗ trợ bị từ chối với lỗi dễ hiểu.

## Tiêu chí hoàn thành toàn bộ

- Câu trả lời text có SQL và bảng truy vấn làm bằng chứng.
- Biểu đồ chỉ được tạo khi người dùng yêu cầu; lỗi biểu đồ không làm mất kết quả truy vấn.
- Dataset được cô lập, có thể xem/xóa theo chính sách đã định.
- Các kiểm thử import, schema, truy vấn, cô lập dataset, bảo vệ SQL và hiển thị kết quả đều đạt.
