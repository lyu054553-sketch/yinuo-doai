"""衣诺后端入口：uvicorn app.main:app --port 8100"""

from __future__ import annotations

import logging
import urllib.request

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from .config import settings
from .routers import assist, media, resources, samples, sessions

logger = logging.getLogger("yinuo")

app = FastAPI(title="衣诺 · 需求解构后端", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:4173",
        "http://127.0.0.1:4173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------- 统一错误格式


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"error": str(exc.detail)})


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={"error": "请求参数不正确，请检查请求内容后再试。"},
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """兜底：任何没预料到的异常都回中文 JSON 500。

    只把一句中文说明给业务员，不返回堆栈、不返回异常原文（异常原文里可能带
    接口密钥、配置路径等内部信息）。

    服务端日志同样只记方法、路由和异常类型，**不记录异常原文、也不带堆栈
    （不加 exc_info）**：异常原文可能含接口密钥，一旦写进日志就等于泄漏。
    需要更多线索时按时间点和方法、路由去查对应的业务日志。
    """
    logger.error(
        "未处理异常：%s %s（异常类型：%s）",
        request.method,
        request.url.path,
        type(exc).__name__,
    )
    return JSONResponse(
        status_code=500,
        content={
            "error": "服务器内部出现异常，本次操作没有完成，请稍后重试；如果一直出现，请联系项目负责人。"
        },
    )


# ---------------------------------------------------------------- 健康检查


def _vision_ok() -> bool:
    url = settings.vision_url.rstrip("/") + "/api/health"
    try:
        with urllib.request.urlopen(url, timeout=1.0) as response:
            return 200 <= response.status < 300
    except Exception:
        return False


@app.get("/api/health")
def health() -> dict:
    return {
        "ok": True,
        "mock_mode": settings.mock_mode,
        "llm_model": settings.llm_model,
        "image_provider": settings.image_provider,
        "vision_ok": _vision_ok(),
    }


# ---------------------------------------------------------------- 路由与静态文件

app.include_router(sessions.router)
app.include_router(samples.router)
app.include_router(resources.router)
app.include_router(media.router)
app.include_router(assist.router)

settings.files_dir.mkdir(parents=True, exist_ok=True)
_images_dir = settings.data_dir / "images"
if _images_dir.exists():
    app.mount("/files/images", StaticFiles(directory=str(_images_dir)), name="data-images")
app.mount("/files", StaticFiles(directory=str(settings.files_dir)), name="files")