#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""D2 正式评测的 HTTP 层：超时、失败分类、密钥只从环境读取。

失败分类（决定 resume 行为）：
- success：拿到 2xx 且响应体是 JSON。
- definite_failure：服务端明确回了 HTTP 错误（4xx/5xx）或返回体不是合法 JSON。
  说明这次调用已经结束，resume 时允许重跑。
- unknown：超时、连接断开、DNS 失败等——请求可能已在服务端执行，
  **不自动重试**，结果原样保留在分母里，交由项目负责人决定。

任何记录、打印、报告里都不出现 API Key；调用记录只写 key_present 与 key_env。
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime
from typing import Any, Dict, List, Optional

OUTCOME_SUCCESS = "success"
OUTCOME_DEFINITE = "definite_failure"
OUTCOME_UNKNOWN = "unknown"

RAW_KEEP = 20000  # raw 响应最多保留的字符数，避免把大响应塞进 checkpoint


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def resolve_api_key(env_names: List[str]) -> Dict[str, Any]:
    """只在环境变量里找 key；返回里不含 key 明文，供报告使用。"""
    for name in env_names:
        if not name:
            continue
        value = os.environ.get(name)
        if value and value.strip():
            return {"key": value.strip(), "env": name, "present": True}
    return {"key": None, "env": None, "present": False, "checked": [n for n in env_names if n]}


def key_status(key_info: Dict[str, Any]) -> Dict[str, Any]:
    """给报告用的密钥状态：只说明有无与环境变量名，绝不含密钥明文。"""
    return {
        "present": bool(key_info.get("present")),
        "env": key_info.get("env"),
        "checked": key_info.get("checked"),
    }


def _summarize_error(body: str) -> str:
    return (body or "").strip().replace("\n", " ")[:800]


def request_json(
    method: str,
    url: str,
    payload: Optional[Dict[str, Any]] = None,
    timeout: float = 120.0,
    api_key: Optional[str] = None,
    user_agent: str = "yinuo-eval-d2/1.0",
) -> Dict[str, Any]:
    """发一次请求，返回可直接写进 checkpoint 的记录。不做任何重试。"""
    headers = {"Accept": "application/json", "User-Agent": user_agent}
    data = None
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    record: Dict[str, Any] = {
        "method": method,
        "url": url,
        "at": now_iso(),
        "outcome": OUTCOME_UNKNOWN,
        "http_status": None,
        "elapsed_ms": None,
        "error": None,
        "raw": "",
        "raw_truncated": False,
        "parsed": None,
    }
    started = time.perf_counter()
    try:
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = response.status
            body = response.read().decode("utf-8", "replace")
        record["http_status"] = status
        record["raw"] = body[:RAW_KEEP]
        record["raw_truncated"] = len(body) > RAW_KEEP
        try:
            parsed = json.loads(body)
        except ValueError:
            record["outcome"] = OUTCOME_DEFINITE
            record["error"] = f"返回内容不是 JSON：{_summarize_error(body)[:300]}"
            return record
        record["parsed"] = parsed
        record["outcome"] = OUTCOME_SUCCESS
        return record
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        record["http_status"] = exc.code
        record["raw"] = body[:RAW_KEEP]
        record["raw_truncated"] = len(body) > RAW_KEEP
        record["outcome"] = OUTCOME_DEFINITE
        record["error"] = f"HTTP {exc.code}：{_summarize_error(body)[:600]}"
        try:
            record["parsed"] = json.loads(body)
        except ValueError:
            pass
        return record
    except urllib.error.URLError as exc:
        record["outcome"] = OUTCOME_UNKNOWN
        record["error"] = f"无法访问（请求状态未知，不自动重试）：{exc}"
        return record
    except (TimeoutError, OSError) as exc:
        record["outcome"] = OUTCOME_UNKNOWN
        record["error"] = f"网络异常或超时（请求状态未知，不自动重试）：{exc}"
        return record
    finally:
        record["elapsed_ms"] = round((time.perf_counter() - started) * 1000.0, 1)


def get_json(url: str, timeout: float, user_agent: str = "yinuo-eval-d2/1.0") -> Dict[str, Any]:
    return request_json("GET", url, payload=None, timeout=timeout, user_agent=user_agent)


def chat_endpoint(base_url: str) -> str:
    base = (base_url or "").strip().rstrip("/")
    if not base:
        raise ValueError("baseline 地址为空")
    if base.endswith("/chat/completions"):
        return base
    return base + "/chat/completions"