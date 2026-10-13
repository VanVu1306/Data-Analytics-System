# Data-Analytics-System — kiến trúc Agentic (backend / frontend tách riêng)

**Natural Language → SQL → DataFrame → Biểu đồ**, trên demo DB hoặc **dữ liệu người dùng tự upload** (CSV / Excel / SQLite). Code được tách thành các lớp rõ ràng (config, LLM, prompts, database, tools, agents, pipeline, API) và frontend chạy tách khỏi backend.

---

## 1. Kiến trúc tổng thể

```
┌────────────────────┐   HTTP/JSON    ┌──────────────────────────────────────────────┐
│ frontend/ (Streamlit)│ ─────────────► │ backend/ (FastAPI)                            │
│ chỉ vẽ giao diện     │ ◄───────────── │                                              │
└────────────────────┘                │  api ─► pipeline (Orchestrator)               │
                                      │            │                                  │
                                      │            ├─► agents/SQLAgent ─► llm/DeepSeek │
                                      │            ├─► agents/VizAgent ─► llm/Ollama   │
                                      │            │                      llm/DeepSeek │
                                      │            └─► tools/ (execute_sql,            │
                                      │                       render_chart, mock_*...) │
                                      │                        │                       │
                                      │                        ▼                       │
                                      │                   database/ (SQLite)           │
                                      └──────────────────────────────────────────────┘
```

**Quy tắc phụ thuộc (chỉ gọi xuống, không gọi ngược lên):**

| Lớp                               | Được phép dùng                            | Không được dùng                 |
| ---------------------------------- | ---------------------------------------------- | ------------------------------------ |
| `frontend`                       | chỉ HTTP tới backend                         | bất kỳ module nào của`backend` |
| `api`                            | `container`, `pipeline`, `schemas`       | trực tiếp LLM/tools                |
| `pipeline`                       | `agents`, `tools`                          | `llm`, `database` trực tiếp    |
| `agents`                         | `llm`, `prompts`, `tools` (qua registry) | `database`, `api`                |
| `tools`                          | `database`, thư viện (matplotlib, pandas)  | `llm`, `agents`                  |
| `llm`, `database`, `prompts` | `config`                                     | các lớp phía trên                |

`config.py` là nơi duy nhất đọc biến môi trường; `container.py` là nơi duy nhất khởi tạo và nối các thành phần.

---

## 2. Cấu trúc thư mục

```
Data-Analytics-System/
├── backend/
│   ├── config.py              # Settings (frozen dataclass) + loader .env / biến môi trường
│   ├── container.py           # Composition root: dựng store → llm → tools → agents → orchestrator
│   ├── errors.py              # Lỗi nghiệp vụ dataset (code ổn định) — module lá, ai cũng import được
│   ├── main.py                # FastAPI app: router, handler lỗi, chặn upload quá lớn theo Content-Length
│   ├── api/
│   │   ├── routes.py          # /api/health, /schema, /models, /query, /datasets... (lớp mỏng)
│   │   └── schemas.py         # Pydantic: hợp đồng request/response backend ↔ frontend
│   ├── llm/                   # base.py, deepseek_client.py, ollama_client.py
│   ├── prompts/
│   │   ├── sql_prompts.py     # DAIL-SQL cho demo DB; prompt theo schema (không few-shot) cho dataset upload
│   │   └── viz_prompts.py     # Zero-shot Context + Requirement + Constraint
│   ├── database/
│   │   ├── sqlite_repo.py     # SQLiteRepository: schema, execute CHỈ-ĐỌC (timeout, giới hạn dòng/kích thước)
│   │   ├── importers.py       # CSV / Excel / SQLite -> SQLite chuẩn hóa (kiểu, tên, giới hạn, rào chắn)
│   │   └── dataset_store.py   # Registry: dataset_id -> thư mục server tạo; repository(dataset_id); quan hệ thủ công
│   ├── tools/
│   │   ├── base.py            # Tool (ABC) + ToolRegistry
│   │   ├── data_tools.py      # get_schema, list_tables, execute_sql, import_dataset, get_dataset, link/unlink_tables
│   │   ├── viz_tools.py       # render_chart: exec code matplotlib → PNG
│   │   ├── mocks.py           # mock_sql, mock_viz_code
│   │   └── text_utils.py      # clean_code, is_valid_code, extract_sql (hàm thuần)
│   ├── agents/                # sql_agent.py (Stage 1), viz_agent.py (Stage 3)
│   └── pipeline/
│       └── orchestrator.py    # PipelineOrchestrator (Stage 1→2→3) + DatasetService (cầu nối api → tools)
├── frontend/
│   ├── app.py                 # Entrypoint Streamlit
│   ├── api_client.py          # NL2VizClient: mọi lời gọi tới backend
│   ├── config.py              # API_BASE_URL, loại tệp upload, gợi ý câu hỏi, loại chart
│   └── ui/                    # styles.py, sidebar.py, datasets.py (upload/chọn dataset/quan hệ), main_view.py
├── .streamlit/config.toml     # maxUploadSize khớp giới hạn backend
├── scripts/                   # create_demo_db.py, setup_ollama.sh/.ps1, Modelfile_llama3
├── tests/                     # pytest
├── data/datasets/             # (tự tạo, gitignore) mỗi upload một thư mục: data.db + meta.json
├── requirements*.txt  .env.example  pytest.ini  .gitignore
```

**Ánh xạ từ code cũ:**

| Code cũ                       | Giờ nằm ở                                                                                                                  |
| ------------------------------ | ----------------------------------------------------------------------------------------------------------------------------- |
| `app.py`                     | `frontend/app.py` + `frontend/ui/*`                                                                                       |
| `modules/dail_sql.py`        | `prompts/sql_prompts.py`, `database/sqlite_repo.py`, `tools/data_tools.py`, `tools/mocks.py`, `agents/sql_agent.py` |
| `modules/text_to_python.py`  | `prompts/viz_prompts.py`, `tools/viz_tools.py`, `tools/mocks.py`, `tools/text_utils.py`, `agents/viz_agent.py`      |
| `modules/ollama_client.py`   | `llm/ollama_client.py`                                                                                                      |
| hằng số cấu hình rải rác | `backend/config.py`                                                                                                         |

---

## 3. Luồng một request

`POST /api/query` → `PipelineOrchestrator.run()`:

```
1. SQLAgent.generate(question, dataset_id) tool get_schema(dataset_id) → prompt → DeepSeek → extract_sql
                                          demo (dataset_id=None): prompt DAIL + few-shot bán hàng
                                          dataset upload: prompt chỉ có schema + luật JOIN (không few-shot)
                                          LLM trả `-- CLARIFY: ...` → status "needs_clarification" (dừng, không chạy SQL)
                                          (không có key / chọn mock → tool mock_sql)
2. tool execute_sql(sql, dataset_id)      chỉ-đọc, timeout, giới hạn dòng
                                          lỗi → "sql_error"; 0 dòng → "empty_result" (dừng)
3. VizAgent.generate_code(question+hint)  Ollama → (lỗi/không chạy) DeepSeek → mock
4. tool render_chart(code, df)            exec → PNG; lỗi → status "viz_error"
```

Trong Stage 1 agent dùng câu hỏi gốc; gợi ý chart (`"... (as a pie chart)"`) chỉ được thêm vào Stage 3.

**Chuỗi fallback của VizAgent** (giữ nguyên hành vi cũ, có test):

```
use_mock                 → mock
use_ollama:
  Ollama chạy            → Llama3 local ─ code hợp lệ → dùng
                                          ─ code không hợp lệ → mock   (không thử DeepSeek)
                                          ─ lỗi gọi → DeepSeek (nếu có key) → mock
  Ollama không chạy      → DeepSeek (nếu có key) → mock
không dùng Ollama        → DeepSeek (nếu có key) → mock
```

---

## 4. REST API

| Method | Path                                     | Mô tả                                                                                                                                              |
| ------ | ---------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------- |
| GET    | `/api/health`                          | `db_exists`, `llm_provider`, `llm_configured`, `ollama_running`, `ollama_default_model`, `max_upload_mb`                                 |
| GET    | `/api/schema?dataset_id=`              | `{tables: {tên_bảng: [cột...]}, relations: [...]}`; bỏ `dataset_id` = demo DB (503 nếu chưa tạo)                                          |
| GET    | `/api/models`                          | Model Ollama đã pull + model mặc định                                                                                                           |
| POST   | `/api/datasets`                        | Upload (multipart`file`): CSV / `.xlsx` / `.db` `.sqlite` → `201` + `dataset_id`, bảng, số dòng, cảnh báo                          |
| GET    | `/api/datasets/{dataset_id}`           | Metadata dataset (bảng, cột, khóa ngoại, quan hệ đã xác nhận)                                                                               |
| POST   | `/api/datasets/{dataset_id}/relations` | Xác nhận quan hệ`{from_table, from_column, to_table, to_column}` (kiểm tra bảng/cột tồn tại)                                               |
| DELETE | `/api/datasets/{dataset_id}/relations` | Xóa một quan hệ đã xác nhận (cùng body)                                                                                                      |
| POST   | `/api/query`                           | Chạy pipeline. Body:`question`, `dataset_id` (tùy chọn), `chart_hint`, `use_mock_sql`, `use_ollama`, `use_mock_viz`, `ollama_model` |

`/api/query` trả HTTP 200 kèm `status` ∈ `ok | sql_error | empty_result | viz_error | needs_clarification`, cùng mọi thứ đã tính được tới điểm dừng (SQL, prompt, bảng dữ liệu, `truncated`, `clarification`, code, ảnh PNG base64). **`dataset_id` không tồn tại/không hợp lệ → 404, không bao giờ fallback sang demo DB.** Lỗi dataset có dạng `{"detail": {"code", "message"}}`: `unsupported_format` 415, `file_too_large` 413, `invalid_file`/`unsafe_content`/`import_failed`/`invalid_relation` 422, `dataset_not_found`/`relation_not_found` 404. Tài liệu tương tác: `http://127.0.0.1:8000/docs`.

---

## 5. Cấu hình

Đọc từ biến môi trường hoặc file `.env` ở thư mục gốc (biến môi trường thật được ưu tiên). Giá trị mặc định = giá trị của bản cũ.

| Biến                                                     | Mặc định                                      | Ý nghĩa                                                                                         |
| --------------------------------------------------------- | ------------------------------------------------ | ------------------------------------------------------------------------------------------------- |
| `LLM_PROVIDER`                                          | `deepseek`                                     | Chọn cloud provider:`deepseek` hoặc `openai`                                                |
| `DEEPSEEK_API_KEY`                                      | *(rỗng)*                                      | Dùng khi`LLM_PROVIDER=deepseek`; rỗng → Stage 1 chạy mock SQL                               |
| `DEEPSEEK_BASE_URL` / `DEEPSEEK_MODEL`                | `https://api.deepseek.com` / `deepseek-chat` | DeepSeek endpoint và model                                                                       |
| `OPENAI_API_KEY` / `OPENAI_MODEL`                     | *(rỗng)* / `gpt-5.6`                        | Dùng khi`LLM_PROVIDER=openai`; client gửi giới hạn đầu ra bằng `max_completion_tokens` |
| `SQL_TEMPERATURE` / `SQL_MAX_TOKENS`                  | `0` / `512`                                  | Stage 1                                                                                           |
| `VIZ_CLOUD_TEMPERATURE` / `VIZ_CLOUD_MAX_TOKENS`      | `0.1` / `800`                                | Stage 3 khi dùng cloud provider                                                                  |
| `OLLAMA_BASE_URL` / `OLLAMA_MODEL`                    | `http://127.0.0.1:11434` / `llama3.2`        |                                                                                                   |
| `OLLAMA_TIMEOUT` / `OLLAMA_MAX_TOKENS`                | `120` / `512`                                |                                                                                                   |
| `OLLAMA_TEMPERATURE` / `OLLAMA_TOP_P`                 | `0.1` / `0.9`                                |                                                                                                   |
| `NL2VIZ_DB_PATH`                                        | `demo_db/sales_demo.db`                        | Demo DB (dataset mặc định, chỉ đọc)                                                         |
| `NL2VIZ_DATASET_DIR`                                    | `data/datasets`                                | Nơi lưu dataset upload                                                                          |
| `NL2VIZ_MAX_UPLOAD_MB`                                  | `50`                                           | Giới hạn kích thước tệp upload                                                              |
| `NL2VIZ_IMPORT_TIMEOUT` / `NL2VIZ_MAX_ROWS_PER_TABLE` | `60` / `200000`                              | Thời gian import / số dòng tối đa mỗi bảng                                                 |
| `NL2VIZ_QUERY_TIMEOUT` / `NL2VIZ_QUERY_MAX_ROWS`      | `10` / `1000`                                | Thời gian thực thi SQL / số dòng kết quả tối đa                                           |
| `VIZ_SAMPLE_ROWS` / `CHART_DPI`                       | `3` / `100`                                  | Số dòng mẫu trong prompt Stage 3 / DPI ảnh                                                    |
| `NL2VIZ_API_URL`                                        | `http://127.0.0.1:8000`                        | **Frontend** — đọc từ môi trường của tiến trình `streamlit`                     |

Dùng model Ollama tùy chỉnh: `ollama create llama3-fast -f scripts/Modelfile_llama3`, rồi đặt `OLLAMA_MODEL=llama3-fast`.

Đổi cloud provider sang OpenAI bằng cách đặt `LLM_PROVIDER=openai`, cấu hình `OPENAI_API_KEY` và `OPENAI_MODEL` trong `.env`, sau đó khởi động lại backend. Chuyển về DeepSeek bằng `LLM_PROVIDER=deepseek`. Cả SQLAgent và fallback cloud của VizAgent dùng cùng provider được chọn; Ollama vẫn là lựa chọn local cho tạo biểu đồ.

---

## 6. Cài đặt & chạy

Yêu cầu Python 3.10+ (đã chạy thử trên 3.13).

```bash
cd Data-Analytics-System
python -m venv venv && source venv/bin/activate      # Windows: .\venv\Scripts\Activate.ps1
pip install -r requirements.txt

python scripts/create_demo_db.py                      # tạo demo_db/sales_demo.db (bắt buộc, lần đầu)
cp .env.example .env                                  # chọn LLM_PROVIDER và điền API key tương ứng

# Tùy chọn: Ollama cho Stage 3 local
bash scripts/setup_ollama.sh                          # Windows: .\scripts\setup_ollama.ps1

# Terminal 1 — backend
uvicorn backend.main:app --host 127.0.0.1 --port 8000
# Terminal 2 — frontend
streamlit run frontend/app.py
```

Mở `http://localhost:8501`. Không có key và không có Ollama thì app vẫn chạy ở chế độ mock.

> **Giữ `--host 127.0.0.1`.** Backend đang `exec` code do LLM sinh ra mà chưa có sandbox thật (xem mục 9). Đừng mở ra mạng ngoài.

**Test:** `pip install -r requirements-dev.txt && pytest`

### Dùng chức năng upload

1. Mở trang chính → **📁 Upload dữ liệu** → chọn tệp → **Import dữ liệu**. Kết quả import (dataset_id, bảng, số dòng, cảnh báo) hiện ngay; dataset vừa upload tự được chọn.
2. Chọn dataset ở sidebar (demo hoặc các tệp đã upload trong phiên), xem schema, nhập câu hỏi → **Chạy Pipeline**.
3. Bảng không có khóa ngoại sẽ **không** bị tự JOIN. Nếu câu hỏi cần JOIN, hệ thống hỏi lại; bạn khai báo quan hệ ở **🔗 Quan hệ giữa các bảng** (sidebar) rồi chạy lại.

Lưu ý: danh sách dataset của phiên lưu trong `st.session_state` nên mất khi tải lại trang (dữ liệu vẫn nằm trong `data/datasets/`; xem/xóa/hết hạn dataset là giai đoạn 4).

---

## 6b. Upload dataset — quy tắc

- **Định dạng:** CSV (một bảng), Excel `.xlsx` (mỗi sheet một bảng trong cùng database), SQLite `.db/.sqlite`. `.sql` (script), `.json`, `.xls` bị từ chối kèm giải thích. Mỗi upload = một SQLite riêng (`data/datasets/<dataset_id>/data.db`); client chỉ biết `dataset_id` (uuid hex, kiểm tra định dạng), tên tệp/MIME của client không bao giờ thành đường dẫn. Tệp gốc bị xóa sau import; import lỗi thì xóa sạch thư mục.
- **Chuẩn hóa tên:** giữ chữ Unicode (tiếng Việt có dấu), ký tự khác → `_`; rỗng → `column_N`/`sheet_N`; bắt đầu bằng số → tiền tố `c_`/`t_`; trùng (không phân biệt hoa/thường) → `_2`, `_3`; tối đa 64 ký tự. SQLite upload giữ nguyên tên bảng/cột.
- **Kiểu dữ liệu:** cột thuần số nguyên → INTEGER, có số thực → REAL, còn lại TEXT; ngày giờ → TEXT ISO; số 0 đứng đầu (`007`) giữ TEXT; số nguyên vượt int64 → TEXT; ô rỗng, `#N/A`… → NULL.
- **Excel:** bỏ sheet ẩn/trống/chỉ có tiêu đề (có cảnh báo); bỏ dòng ghi chú phía trên tiêu đề; công thức đọc theo giá trị cache lần lưu cuối.
- **SQLite:** mở chỉ đọc rồi *tạo lại* bảng trong DB mới (giữ khóa ngoại/ràng buộc); bỏ view, trigger, bảng ảo, index; authorizer chặn ATTACH/PRAGMA/hàm nguy hiểm.
- **Giới hạn mặc định:** 50 MB/tệp (kiểm tra cả Content-Length trước khi nhận), 300 MB sau giải nén `.xlsx`, 500 MB SQLite tạo ra, 200.000 dòng/bảng, 500 cột, 50 sheet, 60 giây import.
- **Truy vấn:** kết nối `mode=ro` + authorizer (chỉ SELECT; INSERT/UPDATE/DELETE/DDL/PRAGMA/ATTACH và nhiều statement bị từ chối), timeout 10 giây, tối đa 1000 dòng / 2 MB kết quả (cắt thì `truncated=true`). Áp dụng cho cả demo DB.
- **Hạn chế đã biết:** CSV dấu thập phân kiểu Việt (`12,5`) giữ TEXT; CSV luôn coi dòng đầu là tiêu đề; Excel ô gộp không được trải; việc "không JOIN khi chưa có quan hệ" dựa vào prompt (chưa có bộ kiểm tra SQL sau sinh); chưa có liệt kê/xóa/hết hạn dataset và cô lập theo phiên (giai đoạn 4).

---

## 7. Cách mở rộng

- **Thêm tool:** tạo class kế thừa `Tool` trong `backend/tools/` (đặt `name`, `description`, cài `run`), đăng ký trong `container.build_container`, rồi agent/pipeline gọi `tools.get("tên").run(...)`.
- **Thêm agent:** tạo file trong `backend/agents/` nhận `LLMClient` + `ToolRegistry`, trả về dataclass kết quả; thêm bước tương ứng vào `PipelineOrchestrator.run`.
- **Thêm LLM provider:** kế thừa `LLMClient` (`is_available`, `complete`) trong `backend/llm/`, tiêm vào `build_container`. Agents không cần sửa.
- **Đổi loại database:** viết repository mới có cùng các phương thức (`exists`, `get_schema`, `get_schema_dict`, `execute`) và cho `DatasetStore.repository()` trả về nó.
- **Thêm endpoint:** thêm schema vào `api/schemas.py` và route vào `api/routes.py`; route chỉ gọi `Container` (orchestrator / dataset_service).
- **Test với LLM giả:** `build_container(settings, deepseek=FakeLLM(...), ollama=FakeLLM(...))` — xem `tests/conftest.py`.

---

## 8. Khác biệt so với bản cũ

Phần lớn là chuyển code nguyên văn (prompt, few-shot, mock, làm sạch code, AST transform, tham số sampling đều giữ nguyên). Các thay đổi có chủ đích:

1. **Chọn model Llama ở sidebar giờ thật sự có tác dụng.** Bản cũ set `OLLAMA_MODEL_OVERRIDE` nhưng không đâu đọc nó. Khi tách thành hai tiến trình, lựa chọn phải đi qua API (`ollama_model`) nên lỗi này tự hết. Đây là thay đổi hành vi duy nhất.
2. **Backend đọc `.env`** (README cũ hứa nhưng code không làm) và có thêm các biến môi trường ở mục 5, mặc định bằng giá trị cũ.
3. **Biểu đồ được render thành PNG ở backend**, frontend hiển thị bằng `st.image` thay cho `st.pyplot`.
4. **Một lần gọi cho cả pipeline:** kết quả Stage 1/2/3 hiện cùng lúc sau khi chạy xong, với một spinner. Bản cũ vẽ Stage 1 lên màn hình trước khi Stage 2 chạy.
5. **Backend trả 503 nếu chưa có DB** (tránh `sqlite3.connect` âm thầm tạo file rỗng), đóng kết nối bằng `contextlib.closing`, và có lock quanh bước vẽ vì pyplot dùng state toàn cục còn FastAPI chạy đa luồng.
6. **Streamlit ≥ 1.50:** dùng `width="stretch"` thay `use_container_width` (đã bị deprecate). Ô nhập câu hỏi khởi tạo qua `session_state` thay vì `value=` để hết cảnh báo; hành vi giống cũ.
7. API trả thêm `sql_source` / `viz_source` (`mock`, `deepseek`, `ollama`, `error`).
8. Không mang theo `docs/INSTALL.md` và `docs/ARCHITECTURE.md` cũ (đường dẫn lỗi thời) — file này thay thế. Các con số latency/accuracy trong tài liệu cũ chưa có bằng chứng đo nên không đưa vào.

---

## 9. Vấn đề đã biết — cố ý CHƯA sửa trong lần refactor này

Vì yêu cầu là giữ nguyên chức năng, các điểm sau vẫn như bản cũ. Cột phải là chỗ cần sửa sau khi tách lớp:

| Vấn đề                                                                                                                                               | Nơi sửa                                                                 |
| ------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------- |
| **`exec` code LLM không phải sandbox** (`__builtins__` đầy đủ, không timeout)                                                          | `tools/viz_tools.py` → `RenderChartTool`                             |
| **Privacy:** khi Ollama lỗi/không chạy và có key, **3 dòng dữ liệu thật vẫn gửi lên DeepSeek** (đã xác nhận khi chạy thử) | `prompts/viz_prompts.py`, `agents/viz_agent.py` → `_cloud_or_mock` |
| `mock_sql`: nhánh `"product"` đứng trước `"top"` nên câu "Top 5 best-selling products" trả về doanh thu theo category                    | `tools/mocks.py`                                                        |
| DeepSeek lỗi →`-- Error calling DeepSeek: ...` được trả về như một câu SQL rồi đem đi chạy                                              | `agents/sql_agent.py`, `pipeline/orchestrator.py`                     |
| Metric "Mode" suy ra từ cấu hình, không phản ánh đường chạy thực tế (API đã có`viz_source` để dùng)                                 | `frontend/ui/main_view.py`                                              |
| Không retry khi SQL/code lỗi;`OLLAMA_MAX_TOKENS=512` có thể cắt cụt code → rơi về mock                                                       | `pipeline/orchestrator.py`, cấu hình                                  |
| Few-shot cố định, chưa chọn theo độ tương đồng (phần cốt lõi của DAIL-SQL)                                                               | `prompts/sql_prompts.py`, `agents/sql_agent.py`                       |
| `extract_sql` có nhánh regex không bao giờ khớp (vô hại)                                                                                       | `tools/text_utils.py`                                                   |

---

## 10. Phạm vi đã kiểm chứng

- **pytest (xem `pytest -q`):** fallback của hai agents, repository (kể cả chỉ-đọc/timeout/giới hạn dòng), tools, toàn bộ API qua HTTP với LLM giả, importer CSV/Excel/SQLite, upload → schema → truy vấn trên dataset, dataset sai ID → 404, quan hệ thủ công, làm rõ JOIN, demo DB không đổi (so hash).
- **Chạy thật** backend bằng uvicorn + frontend bằng Streamlit `AppTest`: demo, upload sai định dạng (lỗi hiển thị, giữ UI), upload CSV → tự chọn dataset → chạy pipeline (mock) ra đúng bảng, upload SQLite có khóa ngoại, thêm quan hệ, đổi lại demo.
- **Chưa thử** với Ollama thật hoặc DeepSeek API key thật (mới qua LLM giả): bạn nên chạy thử một lần với môi trường của mình, đặc biệt hành vi `-- CLARIFY:` của model thật.
