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

- [X] Giữ trải nghiệm **một chatbot / một ô nhập prompt**: người dùng hỏi dữ liệu bằng ngôn ngữ tự nhiên, không phải chọn trước chế độ “Hỏi đáp”, “SQL” hay “Biểu đồ”.
- [X] Với câu hỏi dữ liệu, mặc định chạy luồng `câu hỏi → sinh SQL → thực thi trên dataset đã chọn → trả lời text ngắn + bảng kết quả`. Không chỉ hiển thị SQL/code truy vấn làm phản hồi chính.
- [X] Hiển thị câu trả lời text và bảng kết quả trực tiếp, không suy diễn hoặc đưa số liệu không có trong kết quả. Đưa SQL đã thực thi vào phần **“Bằng chứng / SQL”** có thể mở rộng.
- [X] Xem “phân tích bằng SQL” là cùng luồng hỏi đáp, không tạo một tool/chế độ độc lập chỉ để chạy SQL. Khi người dùng yêu cầu SQL hoặc bật tùy chọn tương ứng, mở/hiển thị SQL và các thông tin thực thi liên quan.
- [X] Xem biểu đồ là một nhánh/bước tùy chọn sau khi đã có kết quả truy vấn: nhận biết yêu cầu vẽ trong prompt, đồng thời cung cấp lựa chọn phụ “Không vẽ / Tự chọn / loại biểu đồ cụ thể”. Mặc định không vẽ nếu người dùng không yêu cầu.
- [X] Nếu bước tạo/render biểu đồ thất bại, vẫn trả câu trả lời text và bảng đã truy vấn thành công; thể hiện lỗi biểu đồ riêng.
- [X] Mở rộng hợp đồng API để phân biệt kết quả truy vấn, câu trả lời text, SQL/bằng chứng, trạng thái làm rõ và kết quả/lỗi biểu đồ; các trường SQL/code/prompt nội bộ không thay thế câu trả lời cho người dùng.
- [X] Kiểm thử: trả lời text khớp kết quả; SQL chỉ hiện khi được yêu cầu hoặc mở phần bằng chứng; câu hỏi có/không yêu cầu biểu đồ; loại biểu đồ cụ thể; truy vấn lỗi/rỗng; cần làm rõ; biểu đồ lỗi nhưng vẫn giữ bảng và text.

**Thành phần dự kiến**

- `backend/pipeline/orchestrator.py`: điều phối luồng chung; chạy SQL và truy vấn, luôn tạo phản hồi text cho kết quả hợp lệ, chỉ gọi nhánh biểu đồ khi được yêu cầu.
- `backend/agents/sql_agent.py` và `backend/tools/sql_tools.py`: tái sử dụng sinh SQL và thực thi truy vấn chỉ đọc hiện có.
- Agent/tool trả lời text mới hoặc bước tương ứng trong pipeline: nhận câu hỏi + kết quả truy vấn giới hạn, tạo câu trả lời ngắn có căn cứ.
- `backend/agents/viz_agent.py` và `backend/tools/viz_tools.py`: tái sử dụng để tạo/render biểu đồ theo yêu cầu; tách lỗi biểu đồ khỏi kết quả truy vấn.
- `backend/api/schemas.py` và `backend/api/routes.py`: mở rộng request/response; nhận tùy chọn phụ về hiển thị SQL và biểu đồ, trả answer/table/SQL/chart với trạng thái riêng.
- `frontend/api_client.py`, `frontend/app.py`, `frontend/ui/main_view.py`: giữ một ô chat; hiển thị text + bảng mặc định, SQL trong expander, tùy chọn biểu đồ ở cạnh ô nhập.

**Phạm vi kiến trúc:** chưa cần LangGraph cho luồng MVP này.

## Giai đoạn 4 — Bổ sung khả năng thực hiện EDA

- [X] **Giữ nguyên SQLite làm định dạng lưu trữ chuẩn.** Không cần đổi database để hỗ trợ EDA: dùng SQL aggregate cho thống kê phù hợp, và pandas trên kết quả/mẫu có giới hạn cho phép tính khó biểu đạt bằng SQL.
- [X] Rà soát import ở `backend/database/importers/`: thống nhất xử lý NULL/rỗng, kiểu dữ liệu hỗn hợp, số và ngày tháng; lưu kiểu nguồn/kiểu suy luận và cảnh báo import để EDA không hiểu nhầm dữ liệu đã chuẩn hóa.
- [X] Mở rộng orchestrator bằng **kế hoạch xử lý có cấu trúc**, tối thiểu gồm danh sách operation, bảng/cột mục tiêu đã xác thực, yêu cầu biểu đồ và yêu cầu hiển thị SQL.
- [X] Định tuyến bằng quy tắc cho ý định rõ ràng và LLM trả kế hoạch có schema cho câu hỏi phức tạp/kết hợp. Kiểm tra operation, tên bảng/cột và tham số với schema/allowlist trước khi chạy; kế hoạch không hợp lệ hoặc mơ hồ thì hỏi lại người dùng.
- [X] Cho phép query và EDA phối hợp trong cùng một lượt, không coi EDA là chatbot riêng:

  - [X] Câu hỏi cần một kết quả cụ thể (ví dụ doanh thu theo tháng) → `query`.
  - [X] Tổng quan/chất lượng/phân phối dữ liệu → operation EDA tương ứng.
  - [X] Câu hỏi kết hợp → chạy một hoặc nhiều operation cần thiết rồi hợp nhất phản hồi.
  - [X] Không chạy full-profile EDA cho mỗi truy vấn đơn giản.
- [X] Xây dựng các EDA tool xác định, có giới hạn; không cho LLM tự sinh và thực thi code phân tích tùy ý:

  - [X] Tổng quan bảng: số dòng/cột, kiểu dữ liệu và số giá trị phân biệt.
  - [X] Chất lượng: giá trị thiếu/NULL và dòng trùng lặp (định nghĩa duplicate phải rõ).
  - [X] Cột số: count, mean, std, min, quartiles/median và max.
  - [X] Cột phân loại: số nhóm và top values có giới hạn.
  - [X] Tương quan giữa các cột số khi số lượng cột phù hợp; nêu rõ loại tương quan và cách xử lý NULL.
- [X] Đặt timeout, giới hạn số cột/nhóm/sample; ghi kết quả là exact hay sampled/truncated. Ưu tiên SQL cho count/mean/min/max/group-by; chỉ đưa dữ liệu đã giới hạn sang pandas cho quartile/tương quan hoặc phép tính cần thiết.
- [X] Trả payload EDA có cấu trúc (operation, bảng/cột, số liệu, warnings, sampling/truncation); tạo câu trả lời text ngắn chỉ từ các kết quả này và hiển thị trong luồng chat cùng bảng/bằng chứng. SQL truy vấn ở expander khi người dùng yêu cầu hoặc bật tùy chọn.
- [X] Kiểm thử planner/validator và từng tool: schema khác nhau, bảng rỗng/lớn, NULL/duplicate, kiểu dữ liệu hỗn hợp, giới hạn/sample, cột không tồn tại, câu hỏi mơ hồ và câu hỏi phối hợp query + EDA.

**Thành phần dự kiến:** importer/metadata trong `backend/database/importers/` và `backend/database/dataset_store.py`; EDA tools trong `backend/tools/` (hoặc `backend/analysis/`) đăng ký tại `backend/container.py`; planner/validator trong `backend/tools/intent.py` hoặc module riêng; điều phối trong `backend/pipeline/orchestrator.py`; response trong `backend/api/schemas.py`/`backend/api/routes.py`; hiển thị chung trong `frontend/api_client.py`/`frontend/ui/main_view.py`.

## Giai đoạn 5 — Mở rộng loại biểu đồ và chọn biểu đồ phù hợp

- [X] Dùng chung kết quả query/EDA làm đầu vào vẽ; không tạo luồng chatbot/phân tích độc lập và không tải toàn bộ bảng lớn chỉ để vẽ.
- [X] Bổ sung histogram/KDE cho phân phối, box plot/violin plot cho phân phối/ngoại lệ, heatmap cho tương quan và count plot cho tần suất nhóm.
- [X] Chọn chart theo mục tiêu và kiểu dữ liệu (so sánh, xu hướng, phân phối, quan hệ, thành phần), không chỉ dựa vào từ khóa hoặc mặc định bar chart.
- [X] Giữ yêu cầu chart trong prompt; tùy chọn phụ “không vẽ / tự chọn / loại cụ thể” được ưu tiên theo hợp đồng API hiện tại, không bắt người dùng chọn chế độ trước khi chat.
- [X] Chuẩn bị dữ liệu chart bằng SQL aggregate hoặc sample có giới hạn; ghi số dòng gốc, số dòng dùng và phương pháp aggregate/sample.
- [X] Hiển thị tiêu đề, nhãn trục, đơn vị nếu biết và cảnh báo về sample/truncation hoặc dữ liệu không phù hợp với chart.
- [X] Ưu tiên renderer/hàm vẽ xác định với loại chart trong allowlist. Nếu tiếp tục chạy code do LLM sinh, xử lý rủi ro thực thi tại `backend/tools/viz_tools.py` trước khi dùng trên dữ liệu người dùng.
- [X] Kiểm thử từng chart với dữ liệu thiếu/ít điểm/cột không phù hợp/dữ liệu lớn/yêu cầu mâu thuẫn; lỗi chart không làm mất câu trả lời, bảng query hoặc kết quả EDA.

**Thành phần dự kiến:** mở rộng chart planner trong `backend/tools/intent.py`; renderer có kiểm soát trong `backend/tools/`; chuẩn bị dữ liệu từ kết quả query/EDA; cập nhật response/hiển thị trong `backend/api/schemas.py`, `backend/api/routes.py`, `frontend/api_client.py` và `frontend/ui/main_view.py`.

## Giai đoạn 6 — Agent hóa Planner: lập kế hoạch query/EDA/chart bằng LLM

- [ ] Mở rộng Planner Agent để LLM lập một kế hoạch thống nhất cho câu hỏi, thay vì dùng regex làm cơ chế quyết định chính cho query, EDA, câu hỏi kết hợp và yêu cầu biểu đồ.
- [ ] Thiết kế hợp đồng kế hoạch JSON có cấu trúc, gồm:

  - [ ] `steps`: một bước `query` tối đa và/hoặc các operation EDA được hỗ trợ, kèm câu hỏi con, bảng, cột và tham số cần thiết.
  - [ ] `chart`: `requested`, loại biểu đồ và mục tiêu trực quan; cho phép loại/mục tiêu chưa xác định để hệ thống chọn phù hợp khi có dữ liệu.
  - [ ] `clarification`: câu hỏi làm rõ nếu yêu cầu thiếu thông tin hoặc mơ hồ.
- [ ] Cung cấp cho LLM schema của dataset và danh sách operation/chart được hỗ trợ; không gửi dữ liệu bảng nếu không cần thiết để lập kế hoạch.
- [ ] Kiểm tra phản hồi LLM (JSON, operation, số bước, tên bảng/cột, tham số, chart type) bằng schema/allowlist trước khi chuyển Orchestrator thực thi; kế hoạch không hợp lệ hoặc mơ hồ thì yêu cầu làm rõ, không chạy một phần kế hoạch sai.
- [ ] Giữ ranh giới trách nhiệm: Planner quyết định cần làm gì; SQL Agent sinh SQL; EDA tools tính toán; Viz Agent sinh mã vẽ trong `VIZ_MODE=code` hoặc chart tools dựng/render spec trong `VIZ_MODE=spec`.
- [ ] Bảo toàn ràng buộc chế độ vẽ hiện tại, nếu kế hoạch yêu cầu chart nhưng không khả thi, trả trạng thái/lý do rõ ràng mà không làm mất câu trả lời và kết quả phân tích.
- [ ] Kiểm thử query thường, EDA, kết hợp query + EDA, yêu cầu/không yêu cầu chart, loại chart cụ thể, `chart_hint` ưu tiên, yêu cầu mơ hồ, phản hồi LLM sai schema và lỗi/không khả dụng của LLM.

**Thành phần dự kiến:** `backend/agents/planner_agent.py`, `backend/prompts/planner_prompts.py`, `backend/tools/planner.py`, `backend/tools/intent.py`, `backend/pipeline/orchestrator.py`, API plan schema/response và các kiểm thử Planner/pipeline liên quan.

## Giai đoạn 7 — Quản lý dataset và quan hệ

- [ ] Thêm API xem danh sách dataset nếu chính sách lưu trữ cho phép.
- [ ] Thêm API xem schema/preview dữ liệu của dataset, giới hạn số dòng trả về.
- [ ] Thêm UI chọn dataset đã upload, xem schema/preview và quản lý quan hệ giữa các bảng.
- [ ] Thêm API/UI xóa dataset, xóa cả metadata và file SQLite tương ứng.
- [ ] Xác định chính sách lưu dữ liệu, thời hạn tồn tại và dọn dataset hết hạn.
- [ ] Nếu chưa có tài khoản người dùng, xác định rõ dataset thuộc phiên nào và cách cô lập giữa các phiên; nếu có đăng nhập, kiểm tra quyền sở hữu ở mọi endpoint.
- [ ] Kiểm thử truy cập chéo dataset/phiên bị từ chối và thao tác xóa không ảnh hưởng dataset khác.

## Giai đoạn 8 — Hoàn thiện, an toàn và tài liệu

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
