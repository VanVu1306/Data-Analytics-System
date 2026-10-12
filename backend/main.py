"""
backend/main.py
---------------
Entrypoint FastAPI.

Chạy (chỉ bind localhost cho tới khi có sandbox thật cho code do LLM sinh ra):
    uvicorn backend.main:app --host 127.0.0.1 --port 8000
"""
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from backend.api.routes import dataset_error_handler, router
from backend.config import get_settings
from backend.errors import DatasetError

_MULTIPART_OVERHEAD = 1024 * 1024  # biên/tên trường của multipart


def create_app() -> FastAPI:
    app = FastAPI(
        title="NL2Viz API",
        description="Natural Language -> SQL -> DataFrame -> Chart",
        version="0.2.0",
    )
    app.add_exception_handler(DatasetError, dataset_error_handler)

    @app.middleware("http")
    async def limit_upload_size(request: Request, call_next):
        # Starlette ghi cả multipart ra đĩa tạm trước khi vào route; chặn sớm theo Content-Length.
        if request.method == "POST" and request.url.path == "/api/datasets":
            limit = get_settings().max_upload_mb * 1024 * 1024 + _MULTIPART_OVERHEAD
            try:
                declared = int(request.headers.get("content-length", "0"))
            except ValueError:
                declared = 0
            if declared > limit:
                return JSONResponse(
                    status_code=413,
                    content={"detail": {"code": "file_too_large", "message": "Tệp vượt giới hạn upload cho phép."}},
                )
        return await call_next(request)

    app.include_router(router)
    return app


app = create_app()
