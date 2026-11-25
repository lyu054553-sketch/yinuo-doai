"""Protected, isolated local gateway for a temporary external test environment.

Run only behind HTTPS; the backend must use its own test storage. Credentials
come from environment, and the in-process quota resets on restart.
"""
from __future__ import annotations

import asyncio
import hmac
import os
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import ClientDisconnect

USER = os.environ.get("YINUO_TEST_USER", "")
PASSWORD = os.environ.get("YINUO_TEST_PASSWORD", "")
if not USER or len(PASSWORD) < 20:
    raise RuntimeError("请配置测试用户名和至少 20 位随机密码")
BACKEND = os.environ.get("YINUO_TEST_BACKEND", "http://127.0.0.1:8102").rstrip("/")
QUOTA = int(os.environ.get("YINUO_TEST_REQUEST_QUOTA", "20"))
MAX_BODY = 13 * 1024 * 1024
ROOT = Path(__file__).resolve().parent.parent
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
_quota_lock = asyncio.Lock()
_used = 0


class Guard(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        import base64
        authorization = request.headers.get("authorization", "")
        try:
            scheme, token = authorization.split(" ", 1)
            decoded = base64.b64decode(token, validate=True).decode("utf-8")
            username, password = decoded.split(":", 1)
            valid = scheme.lower() == "basic" and hmac.compare_digest(username.encode("utf-8"), USER.encode("utf-8")) and hmac.compare_digest(password.encode("utf-8"), PASSWORD.encode("utf-8"))
        except (ValueError, UnicodeError):
            valid = False
        if not valid:
            return Response("需要测试访问凭据", status_code=401, headers={"WWW-Authenticate": 'Basic realm="Yinuo test", charset="UTF-8"'})
        if request.method not in {"GET", "HEAD", "POST", "PATCH"}:
            return Response(status_code=405)
        if request.method in {"POST", "PATCH"}:
            origin = request.headers.get("origin", "")
            parsed = urlsplit(origin)
            if parsed.scheme not in {"https", "http"} or parsed.netloc != request.headers.get("host"):
                return JSONResponse({"error": "请求来源不匹配。"}, status_code=403)
            try:
                length = int(request.headers.get("content-length", "-1"))
            except ValueError:
                return Response(status_code=400)
            if length < 0 or length > MAX_BODY:
                return JSONResponse({"error": "请求大小超出测试环境限制。"}, status_code=413)
        return await call_next(request)


app.add_middleware(Guard)


@app.api_route("/api/{path:path}", methods=["GET", "HEAD", "POST", "PATCH"])
@app.api_route("/files/{path:path}", methods=["GET", "HEAD"])
async def proxy(request: Request, path: str):
    global _used
    full_path = request.url.path
    if request.method == "POST" and (full_path == "/api/sessions" or full_path.endswith(("/reference", "/images", "/requirements-analysis"))):
        async with _quota_lock:
            if _used >= QUOTA:
                return JSONResponse({"error": "测试调用次数已用完，请联系项目负责人。"}, status_code=429)
            _used += 1
    try:
        body = await request.body()
        async with httpx.AsyncClient(timeout=240, follow_redirects=False) as client:
            result = await client.request(request.method, BACKEND + full_path, params=request.query_params,
                content=body, headers={"content-type": request.headers.get("content-type", "application/octet-stream")})
    except (httpx.HTTPError, ClientDisconnect):
        return JSONResponse({"error": "测试后端暂时不可用，请稍后重试。"}, status_code=502)
    headers = {key: value for key, value in result.headers.items() if key.lower() in {"content-type", "content-disposition", "cache-control"}}
    headers["Cache-Control"] = "no-store"
    return Response(content=result.content, status_code=result.status_code, headers=headers)


app.mount("/", StaticFiles(directory=ROOT / "frontend" / "dist", html=True), name="frontend")
