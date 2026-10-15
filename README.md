# Data-Analytics-System — kiến trúc Agentic (backend / frontend tách riêng)

**Một khung chat: câu hỏi → SQL → bảng kết quả → câu trả lời text (+ biểu đồ khi được yêu cầu)**, trên demo DB hoặc **dữ liệu người dùng tự upload** (CSV / Excel / SQLite). Code được tách thành các lớp rõ ràng (config, LLM, prompts, database, tools, agents, pipeline, API) và frontend chạy tách khỏi backend.

---

## 1. Kiến trúc tổng thể

```
┌────────────────────┐   HTTP/JSON    ┌──────────────────────────────────────────────┐
│ frontend/ (Streamlit)│ ─────────────► │ backend/ (FastAPI)                            │
│ chỉ vẽ giao diện     │ ◄───────────── │                                              │
└────────────────────┘                │  api ─► pipeline (Orchestrator)               │
                                      │            │                                  │
                                      │            ├─► agents/SQLAgent ─► llm/DeepSeek │
                                      │            ├─► agents/AnswerAgent ─► llm/cloud │
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
│   │   ├── answer_prompts.py  # prompt câu trả lời text (chỉ gửi tối đa N dòng kết quả)
│   │   ├── planner_prompts.py # prompt planner: LLM lập kế hoạch JSON {steps, chart, clarification}; chỉ nhận schema
│   │   └── viz_prompts.py     # Zero-shot Context + Requirement + Constraint
│   ├── database/
│   │   ├── sqlite_repo.py     # SQLiteRepository: schema, execute CHỈ-ĐỌC (timeout, giới hạn dòng/kích thước)
│   │   ├── importers.py       # CSV / Excel / SQLite -> SQLite chuẩn hóa (kiểu, tên, giới hạn, rào chắn) + metadata kiểu nguồn/suy luận/ghi chú
│   │   └── dataset_store.py   # Registry: dataset_id -> thư mục server tạo; repository(dataset_id); quan hệ thủ công
│   ├── tools/
│   │   ├── base.py            # Tool (ABC) + ToolRegistry
│   │   ├── data_tools.py      # get_schema, list_tables, execute_sql, import_dataset, get_dataset, link/unlink_tables
│   │   ├── viz_tools.py       # render_chart: exec code matplotlib → PNG
│   │   ├── planner.py         # kế hoạch xử lý: validator hợp đồng JSON của LLM (steps/chart) + quy tắc dự phòng — hàm thuần
│   │   ├── eda_tools.py       # 5 EDA tool xác định: overview, data_quality, numeric_summary, categorical_summary, correlation
│   │   ├── eda_text.py        # câu trả lời text cho EDA (template, số liệu lấy từ payload)
│   │   ├── intent.py          # nhận diện yêu cầu vẽ biểu đồ / xem SQL trong câu hỏi (hàm thuần)
│   │   ├── answer_tools.py    # template_answer + answer_is_grounded (kiểm tra số liệu)
│   │   ├── mocks.py           # mock_sql, mock_viz_code
│   │   └── text_utils.py      # clean_code, is_valid_code, extract_sql (hàm thuần)
│   ├── agents/                # planner_agent.py (LLM lập kế hoạch query/EDA/chart), sql_agent.py (SQL), answer_agent.py (câu trả lời text), viz_agent.py (biểu đồ)
│   └── pipeline/
│       └── orchestrator.py    # PipelineOrchestrator (kế hoạch → query và/hoặc EDA → câu trả lời → biểu đồ tùy chọn) + DatasetService (cầu nối api → tools)
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

`POST /api/query` → `PipelineOrchestrator.run()` (một luồng duy nhất, không có "chế độ"). Bước 0 là **kế hoạch xử lý do LLM lập** (giai đoạn 4 + 6, xem mục 6c và 6e); phần dưới là bước `query`:

```
0. PlannerAgent.plan(question, dataset_id)  → ProcessingPlan {steps, chart, show_sql} đã xác thực. LLM lập kế hoạch từ schema;
                                            LLM không dùng được (mock/PLANNER_MODE=rules/lỗi) → quy tắc dự phòng (mục 6e)
1. SQLAgent.generate(question, dataset_id)  prompt theo schema → LLM → SQL; `-- CLARIFY:` → "needs_clarification" (dừng)
                                            lỗi gọi LLM → "sql_error" (không đem câu chú thích đi chạy)
2. tool execute_sql(sql, dataset_id)        chỉ-đọc, timeout, giới hạn dòng; lỗi → "sql_error"; 0 dòng → "empty_result"
3. AnswerAgent.generate(question, df)       LUÔN chạy khi truy vấn thành công: câu trả lời text ngắn
                                            LLM (chỉ nhận cột + tối đa ANSWER_MAX_ROWS dòng đầu) → answer_is_grounded
                                            → nếu số liệu không có trong kết quả / LLM lỗi / mock → tool template_answer
4. (tùy chọn) EDA                          các bước EDA trong kế hoạch (mục 6c)
5. (tùy chọn) biểu đồ                       chỉ khi người dùng yêu cầu: tool build_chart_spec (chọn loại + chuẩn bị dữ liệu từ
                                            kết quả query/EDA) → tool render_chart_spec (giai đoạn 5, mục 6d)
                                            lỗi ở đây chỉ ghi vào `chart.status="error"`; text + bảng + EDA vẫn trả về
```

**Khi nào vẽ biểu đồ** (`tools/intent.py` + tùy chọn `chart_hint`): mặc định `detect` — chỉ vẽ nếu câu hỏi có yêu cầu ("vẽ biểu đồ…", "plot…", "chart…"; loại cụ thể như "biểu đồ tròn/cột/đường/phân tán/histogram/mật độ/boxplot/violin/heatmap/count plot"); `none` — không vẽ kể cả khi câu hỏi yêu cầu; `auto` — vẽ, tự chọn loại; hoặc một loại cụ thể. Câu phủ định ("không cần vẽ biểu đồ") không kích hoạt vẽ. **Khi nào mở sẵn SQL:** `show_sql=true` hoặc câu hỏi nhắc tới SQL ("cho xem SQL", "câu lệnh truy vấn"); SQL luôn có trong `evidence`.

Trong bước SQL agent dùng câu hỏi gốc; loại biểu đồ (`"... (as a pie chart)"`) chỉ được thêm vào nhánh biểu đồ.

**Chuỗi fallback của VizAgent** (chỉ dùng khi `VIZ_MODE=code`; mặc định là `spec`, xem mục 6d):

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

| Method | Path                                     | Mô tả                                                                                                                                                                                                                                                                                                                                                  |
| ------ | ---------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| GET    | `/api/health`                          | `db_exists`, `llm_provider`, `llm_configured`, `ollama_running`, `ollama_default_model`, `max_upload_mb`                                                                                                                                                                                                                                     |
| GET    | `/api/schema?dataset_id=`              | `{tables: {tên_bảng: [cột...]}, relations: [...]}`; bỏ `dataset_id` = demo DB (503 nếu chưa tạo)                                                                                                                                                                                                                                              |
| GET    | `/api/models`                          | Model Ollama đã pull + model mặc định                                                                                                                                                                                                                                                                                                               |
| POST   | `/api/datasets`                        | Upload (multipart`file`): CSV / `.xlsx` / `.db` `.sqlite` → `201` + `dataset_id`, bảng, số dòng, cảnh báo                                                                                                                                                                                                                              |
| GET    | `/api/datasets/{dataset_id}`           | Metadata dataset (bảng, cột, khóa ngoại, quan hệ đã xác nhận)                                                                                                                                                                                                                                                                                   |
| POST   | `/api/datasets/{dataset_id}/relations` | Xác nhận quan hệ`{from_table, from_column, to_table, to_column}` (kiểm tra bảng/cột tồn tại)                                                                                                                                                                                                                                                   |
| DELETE | `/api/datasets/{dataset_id}/relations` | Xóa một quan hệ đã xác nhận (cùng body)                                                                                                                                                                                                                                                                                                          |
| POST   | `/api/query`                           | Hỏi đáp. Body:`question`, `dataset_id` (tùy chọn), `chart_hint` (`detect`\|`none`\|`auto`\|`bar chart`\|`line chart`\|`pie chart`\|`scatter plot`\|`histogram`\|`kde plot`\|`box plot`\|`violin plot`\|`heatmap`\|`count plot`), `show_sql`, `use_mock_sql`, `use_ollama`, `use_mock_viz`, `ollama_model` |

`/api/query` trả HTTP 200 với hợp đồng tách rõ từng phần:

```
status    ok | sql_error | empty_result | needs_clarification | eda_error   (phần truy vấn/trả lời; eda_error: kế hoạch chỉ có EDA và mọi operation lỗi)
answer    {text, source, fallback_reason}  câu trả lời text (có cả khi kết quả rỗng; null khi lỗi SQL/cần làm rõ)
clarification   câu hỏi làm rõ (khi needs_clarification)
table     {columns, rows}                  bảng kết quả (bằng chứng)
evidence  {sql, sql_source, sql_prompt, sql_error, show_sql, row_count, column_count, truncated, elapsed_ms}
chart     {status: not_requested|ok|error, requested_by: none|option|prompt, chart_type, png_base64, error, code, prompt, source}
plan      {source: llm|rules|rules_fallback, fallback_reason, steps: [{operation, table, columns, params, question}],
            chart: {requested, type, goal, origin}, chart_requested, show_sql}
eda       [{operation, table, columns, status: ok|not_applicable|error, error, summary, table_data{columns,rows}, warnings,
            definitions, exact, sampled, sample_info, truncated, queries, elapsed_ms, text}]
```

SQL/code/prompt nội bộ nằm trong `evidence`/`chart`, không thay thế `answer`. Lỗi biểu đồ **không** đổi `status`. **`dataset_id` không tồn tại/không hợp lệ → 404, không bao giờ fallback sang demo DB.** Lỗi dataset có dạng `{"detail": {"code", "message"}}`: `unsupported_format` 415, `file_too_large` 413, `invalid_file`/`unsafe_content`/`import_failed`/`invalid_relation` 422, `dataset_not_found`/`relation_not_found` 404. Tài liệu tương tác: `http://127.0.0.1:8000/docs`.

---

## 5. Cấu hình

Đọc từ biến môi trường hoặc file `.env` ở thư mục gốc (biến môi trường thật được ưu tiên). Giá trị mặc định = giá trị của bản cũ.

| Biến                                                                | Mặc định                                      | Ý nghĩa                                                                                                                      |
| -------------------------------------------------------------------- | ------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------ |
| `LLM_PROVIDER`                                                     | `deepseek`                                     | Chọn cloud provider:`deepseek` hoặc `openai`                                                                             |
| `DEEPSEEK_API_KEY`                                                 | *(rỗng)*                                      | Dùng khi`LLM_PROVIDER=deepseek`; rỗng → SQL và câu trả lời chạy mock                                                 |
| `DEEPSEEK_BASE_URL` / `DEEPSEEK_MODEL`                           | `https://api.deepseek.com` / `deepseek-chat` | DeepSeek endpoint và model                                                                                                    |
| `OPENAI_API_KEY` / `OPENAI_MODEL`                                | *(rỗng)* / `gpt-5.6`                        | Dùng khi`LLM_PROVIDER=openai`; client gửi giới hạn đầu ra bằng `max_completion_tokens`                              |
| `SQL_TEMPERATURE` / `SQL_MAX_TOKENS`                             | `0` / `512`                                  | Stage 1                                                                                                                        |
| `VIZ_CLOUD_TEMPERATURE` / `VIZ_CLOUD_MAX_TOKENS`                 | `0.1` / `800`                                | Stage 3 khi dùng cloud provider                                                                                               |
| `OLLAMA_BASE_URL` / `OLLAMA_MODEL`                               | `http://127.0.0.1:11434` / `llama3.2`        |                                                                                                                                |
| `OLLAMA_TIMEOUT` / `OLLAMA_MAX_TOKENS`                           | `120` / `512`                                |                                                                                                                                |
| `OLLAMA_TEMPERATURE` / `OLLAMA_TOP_P`                            | `0.1` / `0.9`                                |                                                                                                                                |
| `NL2VIZ_DB_PATH`                                                   | `demo_db/sales_demo.db`                        | Demo DB (dataset mặc định, chỉ đọc)                                                                                      |
| `NL2VIZ_DATASET_DIR`                                               | `data/datasets`                                | Nơi lưu dataset upload                                                                                                       |
| `NL2VIZ_MAX_UPLOAD_MB`                                             | `50`                                           | Giới hạn kích thước tệp upload                                                                                           |
| `NL2VIZ_IMPORT_TIMEOUT` / `NL2VIZ_MAX_ROWS_PER_TABLE`            | `60` / `200000`                              | Thời gian import / số dòng tối đa mỗi bảng                                                                              |
| `NL2VIZ_QUERY_TIMEOUT` / `NL2VIZ_QUERY_MAX_ROWS`                 | `10` / `1000`                                | Thời gian thực thi SQL / số dòng kết quả tối đa                                                                        |
| `VIZ_SAMPLE_ROWS` / `CHART_DPI`                                  | `3` / `100`                                  | Số dòng mẫu trong prompt biểu đồ / DPI ảnh                                                                              |
| `ANSWER_MAX_ROWS` / `ANSWER_MAX_TOKENS` / `ANSWER_TEMPERATURE` | `20` / `300` / `0`                         | Câu trả lời text: số dòng kết quả tối đa gửi cho LLM / giới hạn đầu ra / nhiệt độ                             |
| `PLANNER_MODE` / `PLANNER_MAX_TOKENS`                            | `llm` / `700`                                | Planner: `llm` = LLM lập kế hoạch (mặc định); `rules` = chỉ quy tắc, không gọi LLM planner / giới hạn đầu ra |
| `NL2VIZ_API_URL`                                                   | `http://127.0.0.1:8000`                        | **Frontend** — đọc từ môi trường của tiến trình `streamlit`                                                  |

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

1. Mở sidebar → **📁 Upload dữ liệu** (ngay dưới ô chọn dataset) → chọn tệp → **Import dữ liệu**. Sau import chỉ hiện **một** thông báo ngắn (dataset vừa upload tự được chọn); các lưu ý import được gộp (một dòng cho mỗi bảng) trong mục **⚠️ Lưu ý khi import** ở sidebar, chi tiết từng cột nằm ở cột `import_notes` của “Tổng quan dataset”.
2. Chọn dataset ở sidebar (demo hoặc các tệp đã upload trong phiên), xem schema, rồi hỏi ở ô chat (hoặc bấm một bong bóng gợi ý phía trên ô nhập; bong bóng **Tổng quan dataset** chỉ là một câu hỏi bình thường: planner định tuyến sang `table_overview` cho các bảng của dataset (số dòng/cột, kiểu, NULL, số giá trị phân biệt, ghi chú import), không có luồng riêng; khung chat tự cuộn tới tin nhắn mới nhất). Phản hồi gồm câu trả lời + bảng; SQL nằm trong **🔎 Bằng chứng / SQL** (đóng sẵn, tự mở khi bạn hỏi “cho xem SQL”). Muốn biểu đồ, nêu loại ngay trong câu hỏi: “vẽ biểu đồ cột / đường / tròn / phân tán …”. Hướng dẫn cài Ollama nằm trong sidebar, mục vẽ biểu đồ.
3. Bảng không có khóa ngoại sẽ **không** bị tự JOIN. Nếu câu hỏi cần JOIN, hệ thống hỏi lại; bạn khai báo quan hệ ở **🔗 Quan hệ giữa các bảng** (sidebar) rồi hỏi lại.

Lưu ý: danh sách dataset của phiên lưu trong `st.session_state` nên mất khi tải lại trang (dữ liệu vẫn nằm trong `data/datasets/`; xem/xóa/hết hạn dataset là giai đoạn 6).

Ví dụ câu hỏi EDA: “Tổng quan dữ liệu”, “Kiểm tra giá trị thiếu và trùng lặp của bảng orders”, “Thống kê mô tả cột revenue của orders”, “Top giá trị phổ biến của cột region”, “Tương quan giữa các cột số của orders”, và câu kết hợp “Doanh thu theo vùng và kiểm tra giá trị thiếu của orders”.

---

## 6b. Upload dataset — quy tắc

- **Định dạng:** CSV (một bảng), Excel `.xlsx` (mỗi sheet một bảng trong cùng database), SQLite `.db/.sqlite`. `.sql` (script), `.json`, `.xls` bị từ chối kèm giải thích. Mỗi upload = một SQLite riêng (`data/datasets/<dataset_id>/data.db`); client chỉ biết `dataset_id` (uuid hex, kiểm tra định dạng), tên tệp/MIME của client không bao giờ thành đường dẫn. Tệp gốc bị xóa sau import; import lỗi thì xóa sạch thư mục.
- **Chuẩn hóa tên:** giữ chữ Unicode (tiếng Việt có dấu), ký tự khác → `_`; rỗng → `column_N`/`sheet_N`; bắt đầu bằng số → tiền tố `c_`/`t_`; trùng (không phân biệt hoa/thường) → `_2`, `_3`; tối đa 64 ký tự. SQLite upload giữ nguyên tên bảng/cột.
- **Kiểu dữ liệu:** cột thuần số nguyên → INTEGER, có số thực → REAL, còn lại TEXT; ngày giờ → TEXT ISO; số 0 đứng đầu (`007`) giữ TEXT; số nguyên vượt int64 → TEXT; ô rỗng, lỗi công thức Excel (`#N/A`…) → NULL; cột số có lẫn `NA`/`N/A`/`null`/`nan` → các token đó thành NULL (chỉ khi phần còn lại đều là số); cột lẫn số và chữ → TEXT + ghi chú cột (cảnh báo import được gộp một dòng mỗi bảng); cột ngày ISO nhận diện nhưng vẫn lưu TEXT, ngày `d/m/y` mơ hồ giữ nguyên TEXT.
- **Metadata kiểu theo cột** (trong `meta.json` và `GET /api/datasets/{id}`): `type` (SQLite), `source_type` (CSV: `text`; Excel: kiểu ô quan sát; SQLite: kiểu khai báo), `inferred_type` (`integer|real|text|date|datetime|boolean|mixed|empty|blob|numeric`) và `notes` (ô trống→NULL, NA→NULL, số 0 đứng đầu, kiểu hỗn hợp...). EDA dùng chúng để không hiểu nhầm dữ liệu đã chuẩn hóa.
- **Excel:** bỏ sheet ẩn/trống/chỉ có tiêu đề (có cảnh báo); bỏ dòng ghi chú phía trên tiêu đề; công thức đọc theo giá trị cache lần lưu cuối.
- **SQLite:** mở chỉ đọc rồi *tạo lại* bảng trong DB mới (giữ khóa ngoại/ràng buộc); bỏ view, trigger, bảng ảo, index; authorizer chặn ATTACH/PRAGMA/hàm nguy hiểm.
- **Giới hạn mặc định:** 50 MB/tệp (kiểm tra cả Content-Length trước khi nhận), 300 MB sau giải nén `.xlsx`, 500 MB SQLite tạo ra, 200.000 dòng/bảng, 500 cột, 50 sheet, 60 giây import.
- **Truy vấn:** kết nối `mode=ro` + authorizer (chỉ SELECT; INSERT/UPDATE/DELETE/DDL/PRAGMA/ATTACH và nhiều statement bị từ chối), timeout 10 giây, tối đa 1000 dòng / 2 MB kết quả (cắt thì `truncated=true`). Áp dụng cho cả demo DB.
- **Hạn chế đã biết:** CSV dấu thập phân kiểu Việt (`12,5`) giữ TEXT; CSV luôn coi dòng đầu là tiêu đề; Excel ô gộp không được trải; việc "không JOIN khi chưa có quan hệ" dựa vào prompt (chưa có bộ kiểm tra SQL sau sinh); chưa có liệt kê/xóa/hết hạn dataset và cô lập theo phiên (giai đoạn 6).

---

## 6c. EDA (giai đoạn 4)

SQLite vẫn là định dạng lưu trữ chuẩn. EDA **không** phải chatbot riêng: nó là các bước trong cùng một lượt chat, do planner quyết định.

**Kế hoạch xử lý** (`tools/planner.py`, `agents/planner_agent.py`): `ProcessingPlan` = danh sách bước `{operation, table, columns, params}` + yêu cầu biểu đồ + yêu cầu hiển thị SQL.

> **Từ giai đoạn 6, LLM là bộ quyết định chính** (mục 6e). Bảng định tuyến bằng quy tắc dưới đây là **phương án dự phòng** khi không dùng được LLM planner (mock, `PLANNER_MODE=rules`, chưa cấu hình LLM, LLM lỗi).

| Tình huống                                                                                                                                                                                                                  | Cách định tuyến                                                                                                     |
| ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------- |
| Không có từ khóa EDA (“doanh thu theo vùng”, “top 5 sản phẩm”)                                                                                                                                                     | `[query]` bằng quy tắc; **không** gọi LLM planner, **không** chạy EDA                               |
| Từ khóa EDA rõ ràng, không có phần truy vấn (“tổng quan”, “giá trị thiếu”, “trùng lặp”, “thống kê mô tả”, “phân phối”, “top giá trị”, “tương quan”; có cả tiếng Anh và không dấu) | các bước EDA bằng quy tắc (bảng/cột nhận từ tên được nhắc tới)                                           |
| Kết hợp (“doanh thu theo vùng**và** kiểm tra giá trị thiếu”)                                                                                                                                                  | LLM trả kế hoạch JSON; không có LLM/mock → bản nháp của quy tắc; LLM không gọi được →`rules_fallback` |
| Kế hoạch sai/mơ hồ (operation ngoài allowlist, bảng/cột không tồn tại, tham số lỗi, nhiều bảng mà không rõ bảng nào)                                                                                       | `validate_plan` từ chối → `needs_clarification` với câu hỏi làm rõ; không chạy gì                        |

`validate_plan` áp cho mọi kế hoạch (quy tắc lẫn LLM): operation ∈ allowlist, tên bảng/cột phải có trong schema (chuẩn hóa hoa/thường), `top_k` ∈ [1, 50], `method` ∈ {pearson, spearman}, tối đa một bước `query`, tối đa 6 bước; tham số lạ bị loại. Tổng quan/chất lượng không nêu bảng thì áp dụng cho các bảng của dataset (tối đa `EDA_MAX_TABLES`); các operation khác cần bảng rõ ràng.

**EDA tools** (xác định, chỉ đọc, đăng ký trong `container.py`; LLM không sinh hay chạy code phân tích):

| Tool                        | Nội dung                                                                                                                  | Cách tính                                                                                                                                                                                                               |
| --------------------------- | -------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `eda_table_overview`      | số dòng/cột; kiểu khai báo, kiểu quan sát (`typeof`), kiểu/ghi chú lúc import; NULL; số giá trị phân biệt | SQL (chính xác)                                                                                                                                                                                                         |
| `eda_data_quality`        | NULL theo cột (số và %), ô chuỗi rỗng (đếm riêng), dòng trùng lặp                                              | SQL.**Trùng lặp = số dòng thừa so với bản đầu của dòng giống hệt trên mọi cột** (hoặc trên các cột chỉ định); NULL coi là bằng nhau                                                        |
| `eda_numeric_summary`     | count, missing, non_numeric, mean, std, min, q1, median, q3, max                                                           | count/mean/min/max/std bằng SQL (std mẫu, 2 lượt); quartile bằng pandas (nội suy tuyến tính). Chỉ giá trị số (`typeof` integer/real) được tính, giá trị chữ lẫn trong cột bị bỏ qua + cảnh báo |
| `eda_categorical_summary` | số nhóm, top-k giá trị (số lần, %)                                                                                   | SQL`GROUP BY`                                                                                                                                                                                                           |
| `eda_correlation`         | ma trận Pearson/Spearman + cặp mạnh nhất                                                                               | pandas;**pairwise deletion** (bỏ NULL/giá trị không phải số theo từng cặp, cần ≥ 3 cặp); cột hằng số bị loại                                                                                        |

**Giới hạn** (`.env`): `EDA_MAX_COLUMNS` 50, `EDA_TOP_K` 10, `EDA_SAMPLE_ROWS` 50000 (dữ liệu đưa sang pandas; vượt thì lấy mẫu có hệ thống mỗi k dòng), `EDA_TIMEOUT` 20 s mỗi operation (cộng timeout 10 s mỗi câu SQL), `EDA_CORR_MAX_COLUMNS` 15, `EDA_MAX_TABLES` 10. Payload ghi rõ: `exact` (False nếu lấy mẫu / cắt cột / hết thời gian), `sampled` + `sample_info`, `truncated`, `warnings`, `definitions` (cách tính) và `queries` (SQL đã chạy, hiển thị trong “Bằng chứng / SQL”). Câu trả lời text của EDA là template xác định dựng từ payload nên không có số nào ngoài kết quả.

**Chưa làm (giai đoạn sau):** phát hiện ngoại lai; quản lý dataset (giai đoạn 6). Biểu đồ từ kết quả EDA: xem mục 6d.

---

## 6d. Biểu đồ (giai đoạn 5)

Biểu đồ vẫn là bước phụ của cùng lượt chat (không có luồng riêng), dùng lại **kết quả query / EDA đã có** thay vì nạp lại cả bảng.

**Chế độ vẽ** (`VIZ_MODE`, hiển thị ở `/api/health`):

- `spec` (**mặc định**): bộ vẽ xác định. `build_chart_spec` chọn loại và dựng dữ liệu, `render_chart_spec` vẽ bằng matplotlib. Không `exec`, không chạy code do LLM sinh; chỉ các loại trong allowlist `CHART_TYPES`.
- `code`: đường cũ (LLM sinh code matplotlib rồi `exec` trong `tools/viz_tools.py`, **chưa sandbox**). Chỉ dùng khi chạy trên localhost với dữ liệu tin cậy; chế độ này không vẽ được từ kết quả EDA.

**Loại biểu đồ:** `bar chart`, `line chart`, `pie chart`, `scatter plot`, `histogram`, `kde plot`, `box plot`, `violin plot`, `heatmap` (tương quan, hoặc bảng chéo 2 cột nhóm × 1 cột số), `count plot` (tần suất nhóm).

**Chọn loại** (`tools/chart_select.py`): người dùng chỉ định loại (trong prompt hoặc ở sidebar) thì luôn giữ đúng loại đó; dữ liệu không hợp (pie có giá trị âm, scatter < 2 cột số, line 1 điểm, KDE < 5 giá trị...) → `chart.status="error"` kèm lý do + gợi ý, **không âm thầm đổi loại**. Loại chỉ định ít hợp mục tiêu trong câu hỏi thì vẫn vẽ nhưng thêm cảnh báo. Khi tự chọn (`auto`, hoặc câu hỏi chỉ nói “vẽ biểu đồ”) loại được chọn theo **mục tiêu** (`detect_chart_goal`: so sánh / xu hướng / phân phối / quan hệ / thành phần) và **kiểu cột** (số, thời gian, phân loại):

| Dữ liệu / mục tiêu                                                     | Loại tự chọn                                        |
| -------------------------------------------------------------------------- | ------------------------------------------------------ |
| thành phần, 1 nhóm + 1 số không âm, ≤`CHART_PIE_MAX_SLICES` nhóm | pie (nhiều nhóm/âm → bar)                          |
| cột thời gian (hoặc year/month dạng số) + cột số                    | line                                                   |
| quan hệ, ≥ 3 cột số / 2 cột số                                       | heatmap / scatter                                      |
| phân phối: 1 cột số / số + nhóm / chỉ nhóm                         | histogram / box / count                                |
| nhóm + số (mặc định)                                                  | bar (sắp theo giá trị, top`CHART_MAX_CATEGORIES`) |
| 1 cột số / chỉ cột phân loại                                         | histogram / count                                      |

**Nguồn dữ liệu** (`chart.meta.source`): `query_result` (bảng truy vấn, đã bị giới hạn dòng/byte khi chạy); `eda_payload` (dùng số liệu EDA có sẵn: tương quan → heatmap, top giá trị → count/bar/pie, % NULL → bar, quartile → box); `sql_aggregate` (histogram của cột số: gom `CHART_HIST_BINS` khoảng bằng SQL, **chính xác, không lấy mẫu**); `table_sample` (KDE/violin của cột số: mẫu có hệ thống tối đa `CHART_SAMPLE_ROWS` dòng). Lượt có cả query lẫn EDA ưu tiên bảng query (trừ heatmap khi có payload tương quan). Overview bảng không có dạng số liệu để vẽ → lỗi rõ ràng.

**Thông tin kèm biểu đồ** (`chart.meta`): nguồn, `rows_total`/`rows_used`, phương pháp (gộp tổng, mẫu mỗi k dòng, bin SQL...), loại được chọn + lý do, cột x/y/nhóm, tiêu đề, nhãn trục, đơn vị (chỉ suy ra khi tên cột nói rõ: `price (USD)`, `growth_pct`...), `warnings` (cắt top-N, gộp “Khác”, lấy mẫu, kết quả bị cắt, cột bị loại...). UI hiện cảnh báo dưới ảnh và mục “📈 Cách vẽ biểu đồ”; sidebar có ô “Loại biểu đồ” (Theo câu hỏi / Không vẽ / Tự chọn / loại cụ thể).

**Giới hạn** (`.env`): `CHART_MAX_CATEGORIES` 30, `CHART_PIE_MAX_SLICES` 8, `CHART_MAX_POINTS` 2000, `CHART_SAMPLE_ROWS` 20000, `CHART_HIST_BINS` 30.

**Lỗi biểu đồ không làm mất kết quả:** mọi lỗi (dữ liệu không hợp, lỗi bất ngờ khi vẽ) chỉ đặt `chart.status="error"`; câu trả lời, bảng query và kết quả EDA vẫn trả về.

---

## 6e. Planner Agent do LLM lập kế hoạch (giai đoạn 6)

LLM lập **một kế hoạch thống nhất** cho mỗi câu hỏi; regex không còn là cơ chế quyết định chính. Ranh giới trách nhiệm:

| Thành phần                         | Việc làm                                                                                                   |
| ------------------------------------ | ------------------------------------------------------------------------------------------------------------ |
| **Planner** (`PlannerAgent`) | quyết định *cần làm gì*: bước nào, bảng/cột nào, có vẽ biểu đồ không, hay cần hỏi lại |
| **SQL Agent**                  | sinh SQL cho bước `query` (nhận câu hỏi con của Planner)                                             |
| **EDA tools**                  | tính toán xác định (overview, chất lượng, thống kê, tần suất, tương quan)                      |
| **Viz**                        | `VIZ_MODE=code`: Viz Agent sinh mã vẽ · `VIZ_MODE=spec`: chart tools dựng/render spec                |

**Hợp đồng JSON** LLM phải trả (chỉ nhận **schema**: tên bảng/cột/kiểu, không gửi dữ liệu; danh sách operation và loại chart nằm trong prompt):

```json
{"steps": [{"op": "query|table_overview|data_quality|numeric_summary|categorical_summary|correlation",
            "question": "câu hỏi con", "table": "...", "columns": ["..."], "params": {}}],
 "chart": {"requested": false, "type": null, "goal": null},
 "clarification": null}
```

- `steps`: tối đa **một** bước `query` và/hoặc các operation EDA; tối đa 6 bước; `params`: `top_k` ∈ [1, 50], `method` ∈ {pearson, spearman}.
- `chart.requested`: người dùng có yêu cầu vẽ không (không tự ý vẽ). `type` ∈ {bar chart, line chart, pie chart, scatter plot, histogram, kde plot, box plot, violin plot, heatmap, count plot} (nhận cả cách gọi ngắn như `bar`, `pie`) hoặc `null`; `goal` ∈ {comparison, trend, distribution, relationship, composition} hoặc `null`. `null` = để hệ thống chọn loại phù hợp khi đã có dữ liệu (mục 6d).
- `clarification`: câu hỏi làm rõ khi thiếu thông tin/mơ hồ (cùng ngôn ngữ người dùng).

**Kiểm tra trước khi chạy** (`validate_llm_plan`): JSON hợp lệ; operation ∈ allowlist; số bước/số bước `query`; tên bảng/cột có trong schema (chuẩn hóa hoa/thường); tham số; `chart.requested` là boolean, `type`/`goal` ∈ allowlist. **Bất kỳ phần nào sai → `needs_clarification`, không chạy một phần kế hoạch** (không SQL, không EDA, không biểu đồ).

**Ưu tiên tùy chọn phụ:** `chart_hint=none` luôn không vẽ; một loại cụ thể thắng `chart.type` của LLM; `auto` luôn vẽ (loại/mục tiêu theo LLM, `null` = tự chọn); `detect` vẽ khi LLM xác định có yêu cầu. `show_sql` = tùy chọn hoặc câu hỏi nhắc tới SQL.

**Khi LLM planner không dùng được** → bản nháp bằng quy tắc (vẫn qua `validate_plan`), ghi rõ trong `plan.source` / `plan.fallback_reason`:

| `source`         | `fallback_reason`    | Khi nào                                      |
| ------------------ | ---------------------- | --------------------------------------------- |
| `llm`            | —                     | LLM lập kế hoạch                           |
| `rules`          | `mock`               | `use_mock_sql=true` (không gọi cloud LLM) |
| `rules`          | `planner_mode_rules` | `PLANNER_MODE=rules`                        |
| `rules`          | `llm_unavailable`    | chưa cấu hình LLM (không có API key)     |
| `rules_fallback` | `llm_error: ...`     | gọi LLM lỗi (mạng/hết hạn mức)          |

**Biểu đồ không khả thi** (chế độ `code` mà kế hoạch chỉ có EDA; loại chart không hợp với dữ liệu; overview không có số liệu để vẽ) → `chart.status="error"` kèm lý do; câu trả lời, bảng query và kết quả EDA vẫn trả về. Mỗi câu hỏi tốn thêm một lời gọi LLM ngắn cho Planner; đặt `PLANNER_MODE=rules` nếu muốn tiết kiệm (đổi lại quy tắc nhận diện hạn chế hơn).

---

## 7. Cách mở rộng

- **Thêm tool:** tạo class kế thừa `Tool` trong `backend/tools/` (đặt `name`, `description`, cài `run`), đăng ký trong `container.build_container`, rồi agent/pipeline gọi `tools.get("tên").run(...)`.
- **Thêm agent:** tạo file trong `backend/agents/` nhận `LLMClient` + `ToolRegistry`, trả về dataclass kết quả; thêm bước tương ứng vào `PipelineOrchestrator.run`.
- **Thêm LLM provider:** kế thừa `LLMClient` (`is_available`, `complete`) trong `backend/llm/`, tiêm vào `build_container`. Agents không cần sửa.
- **Đổi loại database:** viết repository mới có cùng các phương thức (`exists`, `get_schema`, `get_schema_dict`, `execute`) và cho `DatasetStore.repository()` trả về nó.
- **Thêm endpoint:** thêm schema vào `api/schemas.py` và route vào `api/routes.py`; route chỉ gọi `Container` (orchestrator / dataset_service).
- **Test với LLM giả:** `build_container(settings, deepseek=FakeLLM(...), ollama=FakeLLM(...))` — xem `tests/conftest.py`. `FakeLLM` tách lời gọi của Planner (`planner_calls`, `planner_response`; mặc định trả kế hoạch một bước `query`) khỏi lời gọi SQL/answer (`calls`, `response`, `.script(...)`).

---

**Giai đoạn 5 đổi hành vi mặc định:** biểu đồ vẽ bằng bộ vẽ xác định (`VIZ_MODE=spec`) thay vì code do LLM sinh; `/api/health` có thêm `viz_mode`; `chart.code/prompt` rỗng ở chế độ này và có thêm `chart.meta`; từ “cột” đứng một mình không còn được hiểu là biểu đồ cột (chỉ “biểu đồ cột”, “bar”...), “histogram” giờ là loại riêng. Đặt `VIZ_MODE=code` để dùng lại đường cũ.

**Giai đoạn 6 đổi hành vi mặc định:** khi có LLM, mỗi câu hỏi tốn thêm một lời gọi Planner; câu hỏi có từ khóa EDA không còn được định tuyến cứng bằng regex; kế hoạch LLM sai schema → hỏi lại thay vì đoán; `plan` trong `/api/query` có thêm `chart{requested,type,goal,origin}`, `fallback_reason` và `steps[].question`; `PLANNER_MODE=rules` trả về hành vi cũ.

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

9. **Giai đoạn 3:** một khung chat thay cho form "Chạy Pipeline"; `/api/query` đổi sang hợp đồng lồng (`answer`/`table`/`evidence`/`chart`, không còn `viz_error` hay các trường `sql`, `viz_code`... ở cấp ngoài); biểu đồ mặc định KHÔNG vẽ; `chart_hint` mặc định `detect`; DeepSeek/OpenAI lỗi → `sql_error` thay vì chạy câu chú thích `-- Error ...` như SQL; metric "Mode" bị bỏ (thông tin nguồn thật nằm trong tab "Thông tin thực thi"); mock chart ưu tiên loại biểu đồ nêu rõ hơn từ khóa "tháng"; thông báo lỗi dùng tên provider "DeepSeek"/"OpenAI".

---

## 9. Vấn đề đã biết — cố ý CHƯA sửa trong lần refactor này

Vì yêu cầu là giữ nguyên chức năng, các điểm sau vẫn như bản cũ. Cột phải là chỗ cần sửa sau khi tách lớp:

| Vấn đề                                                                                                                                               | Nơi sửa                                                                 |
| ------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------- |
| **`exec` code LLM không phải sandbox** (`__builtins__` đầy đủ, không timeout)                                                          | `tools/viz_tools.py` → `RenderChartTool`                             |
| **Privacy:** khi Ollama lỗi/không chạy và có key, **3 dòng dữ liệu thật vẫn gửi lên DeepSeek** (đã xác nhận khi chạy thử) | `prompts/viz_prompts.py`, `agents/viz_agent.py` → `_cloud_or_mock` |
| `mock_sql`: nhánh `"product"` đứng trước `"top"` nên câu "Top 5 best-selling products" trả về doanh thu theo category                    | `tools/mocks.py`                                                        |
| Không retry khi SQL/code lỗi;`OLLAMA_MAX_TOKENS=512` có thể cắt cụt code → rơi về mock                                                       | `pipeline/orchestrator.py`, cấu hình                                  |
| Few-shot cố định, chưa chọn theo độ tương đồng (phần cốt lõi của DAIL-SQL)                                                               | `prompts/sql_prompts.py`, `agents/sql_agent.py`                       |
| `extract_sql` có nhánh regex không bao giờ khớp (vô hại)                                                                                       | `tools/text_utils.py`                                                   |

---

## 10. Phạm vi đã kiểm chứng

- **pytest (xem `pytest -q`):** fallback của hai agents, repository (kể cả chỉ-đọc/timeout/giới hạn dòng), tools, toàn bộ API qua HTTP với LLM giả, importer CSV/Excel/SQLite, upload → schema → truy vấn trên dataset, dataset sai ID → 404, quan hệ thủ công, làm rõ JOIN, demo DB không đổi (so hash).
- **Chạy thật** backend bằng uvicorn + frontend bằng Streamlit `AppTest`: demo, upload sai định dạng (lỗi hiển thị, giữ UI), upload CSV → tự chọn dataset → chạy pipeline (mock) ra đúng bảng, upload SQLite có khóa ngoại, thêm quan hệ, đổi lại demo.
- **Chưa thử** với Ollama thật hoặc DeepSeek API key thật (mới qua LLM giả): bạn nên chạy thử một lần với môi trường của mình, đặc biệt hành vi `-- CLARIFY:` của model thật.
- **Giai đoạn 3 (có test):** nhận diện yêu cầu vẽ/SQL (có và không, phủ định, "cột"≠biểu đồ cột), loại biểu đồ cụ thể, tùy chọn `none`/`auto`, lỗi biểu đồ vẫn giữ text + bảng + SQL, lỗi cả stage biểu đồ bị cô lập, kết quả rỗng / SQL lỗi / cần làm rõ, câu trả lời LLM bám số liệu và fallback câu mẫu khi số liệu không có trong kết quả, chỉ gửi tối đa `ANSWER_MAX_ROWS` dòng cho LLM, kết quả bị cắt được báo trong câu trả lời. Frontend kiểm tra bằng Streamlit `AppTest` và ảnh chụp trình duyệt thật (chế độ mock).
- **Chưa thử** câu trả lời của LLM thật: kiểm tra bám số liệu khá chặt (số tự tính, làm tròn, đổi đơn vị sẽ bị loại và dùng câu mẫu) — nên xem tỷ lệ `fallback_reason=ungrounded_numbers` trên model thật. Câu mẫu chọn tiếng Việt khi câu hỏi có dấu; tiếng Việt không dấu sẽ ra tiếng Anh.
- **Test cũ fail sẵn:** `tests/test_llm_client.py::test_retries_with_max_completion_tokens_when_model_rejects_max_tokens` (phụ thuộc cách patch `openai` của phiên bản thư viện hiện tại), không liên quan giai đoạn 3.
