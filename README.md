# Data-Analytics-System — kiến trúc Agentic (backend / frontend tách riêng)

**Natural Language → SQL → DataFrame → Biểu đồ.** Bản này là bản **refactor cấu trúc** của NL2Viz: chức năng giữ nguyên, nhưng code được tách thành các lớp rõ ràng (config, LLM, prompts, database, tools, agents, pipeline, API) và frontend chạy tách khỏi backend.

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
│   ├── container.py           # Composition root: dựng repo → llm → tools → agents → orchestrator
│   ├── main.py                # FastAPI app (uvicorn backend.main:app)
│   ├── api/
│   │   ├── routes.py          # /api/health, /api/schema, /api/models, /api/query (lớp mỏng)
│   │   └── schemas.py         # Pydantic: hợp đồng request/response backend ↔ frontend
│   ├── llm/
│   │   ├── base.py            # LLMClient (interface), LLMError
│   │   ├── deepseek_client.py # DeepSeek qua OpenAI SDK
│   │   └── ollama_client.py   # Ollama local (health check, list model, generate)
│   ├── prompts/
│   │   ├── sql_prompts.py     # DAIL-SQL: few-shot + schema + câu hỏi, system prompt
│   │   └── viz_prompts.py     # Zero-shot Context + Requirement + Constraint
│   ├── database/
│   │   └── sqlite_repo.py     # SQLiteRepository: schema, sample rows, execute (lớp duy nhất chạm DB)
│   ├── tools/
│   │   ├── base.py            # Tool (ABC) + ToolRegistry
│   │   ├── sql_tools.py       # get_schema, execute_sql
│   │   ├── viz_tools.py       # render_chart: exec code matplotlib → PNG
│   │   ├── mocks.py           # mock_sql, mock_viz_code
│   │   └── text_utils.py      # clean_code, is_valid_code, extract_sql (hàm thuần)
│   ├── agents/
│   │   ├── sql_agent.py       # Stage 1: câu hỏi → SQL
│   │   └── viz_agent.py       # Stage 3: DataFrame → code matplotlib (chuỗi fallback)
│   └── pipeline/
│       └── orchestrator.py    # Nối Stage 1→2→3, quyết định điểm dừng, trả PipelineResult
├── frontend/
│   ├── app.py                 # Entrypoint Streamlit
│   ├── api_client.py          # NL2VizClient: mọi lời gọi tới backend
│   ├── config.py              # API_BASE_URL, gợi ý câu hỏi, loại chart
│   └── ui/                    # styles.py, sidebar.py, main_view.py
├── scripts/                   # create_demo_db.py, setup_ollama.sh/.ps1, Modelfile_llama3
├── tests/                     # pytest (32 test)
├── requirements*.txt  .env.example  pytest.ini  .gitignore
```

**Ánh xạ từ code cũ:**

| Code cũ                       | Giờ nằm ở                                                                                                                 |
| ------------------------------ | ---------------------------------------------------------------------------------------------------------------------------- |
| `app.py`                     | `frontend/app.py` + `frontend/ui/*`                                                                                      |
| `modules/dail_sql.py`        | `prompts/sql_prompts.py`, `database/sqlite_repo.py`, `tools/sql_tools.py`, `tools/mocks.py`, `agents/sql_agent.py` |
| `modules/text_to_python.py`  | `prompts/viz_prompts.py`, `tools/viz_tools.py`, `tools/mocks.py`, `tools/text_utils.py`, `agents/viz_agent.py`     |
| `modules/ollama_client.py`   | `llm/ollama_client.py`                                                                                                     |
| hằng số cấu hình rải rác | `backend/config.py`                                                                                                        |

---

## 3. Luồng một request

`POST /api/query` → `PipelineOrchestrator.run()`:

```
1. SQLAgent.generate(question)            tool get_schema → prompt DAIL → DeepSeek → extract_sql
                                          (không có key / chọn mock → tool mock_sql)
2. tool execute_sql(sql)                  lỗi → status "sql_error"; 0 dòng → "empty_result" (dừng)
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

| Method | Path            | Mô tả                                                                                                                 |
| ------ | --------------- | ----------------------------------------------------------------------------------------------------------------------- |
| GET    | `/api/health` | `db_exists`, `deepseek_configured`, `ollama_running`, `ollama_default_model`                                    |
| GET    | `/api/schema` | `{tables: {tên_bảng: [cột...]}}` (503 nếu chưa có DB)                                                           |
| GET    | `/api/models` | Model Ollama đã pull + model mặc định                                                                              |
| POST   | `/api/query`  | Chạy pipeline. Body:`question`, `chart_hint`, `use_mock_sql`, `use_ollama`, `use_mock_viz`, `ollama_model` |

`/api/query` luôn trả HTTP 200 kèm `status` ∈ `ok | sql_error | empty_result | viz_error`, cùng mọi thứ đã tính được tới điểm dừng (SQL, prompt, bảng dữ liệu, code, ảnh PNG base64, `sql_source`/`viz_source`). Tài liệu tương tác: `http://127.0.0.1:8000/docs`.

---

## 5. Cấu hình

Đọc từ biến môi trường hoặc file `.env` ở thư mục gốc (biến môi trường thật được ưu tiên). Giá trị mặc định = giá trị của bản cũ.

| Biến                                                | Mặc định                                      | Ý nghĩa                                                                     |
| ---------------------------------------------------- | ------------------------------------------------ | ----------------------------------------------------------------------------- |
| `DEEPSEEK_API_KEY`                                 | *(rỗng)*                                      | Rỗng → Stage 1 chạy mock SQL                                               |
| `DEEPSEEK_BASE_URL` / `DEEPSEEK_MODEL`           | `https://api.deepseek.com` / `deepseek-chat` |                                                                               |
| `SQL_TEMPERATURE` / `SQL_MAX_TOKENS`             | `0` / `512`                                  | Stage 1                                                                       |
| `VIZ_CLOUD_TEMPERATURE` / `VIZ_CLOUD_MAX_TOKENS` | `0.1` / `800`                                | Stage 3 khi dùng DeepSeek                                                    |
| `OLLAMA_BASE_URL` / `OLLAMA_MODEL`               | `http://127.0.0.1:11434` / `llama3.2`        |                                                                               |
| `OLLAMA_TIMEOUT` / `OLLAMA_MAX_TOKENS`           | `120` / `512`                                |                                                                               |
| `OLLAMA_TEMPERATURE` / `OLLAMA_TOP_P`            | `0.1` / `0.9`                                |                                                                               |
| `NL2VIZ_DB_PATH`                                   | `demo_db/sales_demo.db`                        |                                                                               |
| `VIZ_SAMPLE_ROWS` / `CHART_DPI`                  | `3` / `100`                                  | Số dòng mẫu trong prompt Stage 3 / DPI ảnh                                |
| `NL2VIZ_API_URL`                                   | `http://127.0.0.1:8000`                        | **Frontend** — đọc từ môi trường của tiến trình `streamlit` |

Dùng model Ollama tùy chỉnh: `ollama create llama3-fast -f scripts/Modelfile_llama3`, rồi đặt `OLLAMA_MODEL=llama3-fast`.

---

## 6. Cài đặt & chạy

Yêu cầu Python 3.10+ (đã chạy thử trên 3.13).

```bash
cd Data-Analytics-System
python -m venv venv && source venv/bin/activate      # Windows: .\venv\Scripts\Activate.ps1
pip install -r requirements.txt

python scripts/create_demo_db.py                      # tạo demo_db/sales_demo.db (bắt buộc, lần đầu)
cp .env.example .env                                  # rồi điền DEEPSEEK_API_KEY (tùy chọn)

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

---

## 7. Cách mở rộng

- **Thêm tool:** tạo class kế thừa `Tool` trong `backend/tools/` (đặt `name`, `description`, cài `run`), đăng ký trong `container.build_container`, rồi agent/pipeline gọi `tools.get("tên").run(...)`.
- **Thêm agent:** tạo file trong `backend/agents/` nhận `LLMClient` + `ToolRegistry`, trả về dataclass kết quả; thêm bước tương ứng vào `PipelineOrchestrator.run`.
- **Thêm LLM provider:** kế thừa `LLMClient` (`is_available`, `complete`) trong `backend/llm/`, tiêm vào `build_container`. Agents không cần sửa.
- **Đổi loại database:** viết repository mới có cùng các phương thức (`exists`, `get_schema`, `get_schema_dict`, `execute`) và đổi chỗ khởi tạo trong `container.py`.
- **Thêm endpoint:** thêm schema vào `api/schemas.py` và route vào `api/routes.py`; route chỉ gọi `Container`.
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
| **SQL không bị giới hạn read-only**; DB mở ở chế độ ghi                                                                                  | `database/sqlite_repo.py` → `execute`                                |
| **Privacy:** khi Ollama lỗi/không chạy và có key, **3 dòng dữ liệu thật vẫn gửi lên DeepSeek** (đã xác nhận khi chạy thử) | `prompts/viz_prompts.py`, `agents/viz_agent.py` → `_cloud_or_mock` |
| `mock_sql`: nhánh `"product"` đứng trước `"top"` nên câu "Top 5 best-selling products" trả về doanh thu theo category                    | `tools/mocks.py`                                                        |
| DeepSeek lỗi →`-- Error calling DeepSeek: ...` được trả về như một câu SQL rồi đem đi chạy                                              | `agents/sql_agent.py`, `pipeline/orchestrator.py`                     |
| Metric "Mode" suy ra từ cấu hình, không phản ánh đường chạy thực tế (API đã có`viz_source` để dùng)                                 | `frontend/ui/main_view.py`                                              |
| Kết quả biến mất khi đổi widget (chỉ render trong`if run_btn`)                                                                                 | `frontend/app.py` → `session_state`                                  |
| Không retry khi SQL/code lỗi;`OLLAMA_MAX_TOKENS=512` có thể cắt cụt code → rơi về mock                                                       | `pipeline/orchestrator.py`, cấu hình                                  |
| Few-shot cố định, chưa chọn theo độ tương đồng (phần cốt lõi của DAIL-SQL)                                                               | `prompts/sql_prompts.py`, `agents/sql_agent.py`                       |
| Chưa upload được file`.db`                                                                                                                        | `api/routes.py`, `container.py` (repo theo từng request)             |
| `extract_sql` có nhánh regex không bao giờ khớp (vô hại)                                                                                       | `tools/text_utils.py`                                                   |

---

## 10. Phạm vi đã kiểm chứng

- **32 test pytest đạt** — chuỗi fallback của hai agents, repository, tools (kể cả báo lỗi khi code lỗi/không ra biểu đồ) và toàn bộ API qua HTTP với LLM giả.
- **Chạy thật** backend bằng uvicorn trên demo DB: `/api/health` và `/api/query` ở chế độ mock trả đúng 4 dòng × 2 cột và ảnh PNG.
- **Frontend** chạy bằng bộ test giao diện của Streamlit, nối với backend thật: tải trang, bấm gợi ý, chọn loại chart, chạy pipeline → có bảng, ảnh, 3 tab, metric. Đạt trên Streamlit 1.50.0 và 1.65.0; **1.45.0 không chạy được** (nên yêu cầu ≥ 1.50). Bước này chạy thủ công, không nằm trong bộ pytest đi kèm.
- `OllamaClient` và `DeepSeekClient` đã chạy HTTP thật nhưng tới **server giả cục bộ**, kiểm tra payload, model chọn từ sidebar, tham số sampling và fallback khi Ollama trả 500. **Chưa thử với Ollama thật hoặc DeepSeek API key thật** — bạn nên chạy thử một lần với môi trường của mình.
