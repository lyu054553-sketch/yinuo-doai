"""OpenAI 兼容的大模型客户端：只做一件事——发对话请求，拿到一份合格的 JSON。

设计要点（对应开发约定第 8 节）：

* 模型名、地址、密钥一律从环境变量（经 `app.config.settings`）读取，不写死；
* 只走标准 `POST /chat/completions`，任何 OpenAI 兼容服务都能接；
* 输出一律做 Pydantic 严格校验；不合格只重试一次，还不行就抛 `LLMError`，
  由接口层翻译成中文错误，绝不硬凑结果；
* 传输层可以替换（`transport`），测试用假传输，不碰网络、不调付费服务。

`LLMError` 的文案是给业务员看的中文，不包含密钥、不包含原始堆栈。
"""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, TypeVar

from pydantic import BaseModel, ValidationError

from .config import settings

T = TypeVar("T", bound=BaseModel)

# 传输层：收到 (url, headers, payload, timeout)，返回响应正文（字符串）。
Transport = Callable[[str, dict[str, str], dict[str, Any], float], str]


class LLMError(RuntimeError):
    """调用大模型失败，消息可以直接展示给业务员。"""


def _http_transport(
    url: str, headers: dict[str, str], payload: dict[str, Any], timeout: float
) -> str:
    """标准库 HTTP 传输，不引入额外依赖。"""
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def _extract_json_text(content: str) -> str:
    """从模型输出里取出 JSON 文本：允许 ```json 代码块，也允许前后有少量说明文字。"""
    text = (content or "").strip()
    if not text:
        raise ValueError("大模型返回了空内容")
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        json.loads(text)
        return text
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        candidate = text[start : end + 1]
        json.loads(candidate)  # 不合法就在这里抛 JSONDecodeError
        return candidate
    raise ValueError("大模型输出里没有找到 JSON 对象")


def _read_content(raw: str) -> str:
    """解析 OpenAI 兼容的响应外壳，取出正文。"""
    try:
        envelope = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise LLMError("大模型返回的内容不是 OpenAI 兼容格式，无法读取。请检查模型地址配置。") from exc
    if not isinstance(envelope, dict):
        raise LLMError("大模型返回的内容不是 OpenAI 兼容格式，无法读取。请检查模型地址配置。")
    choices = envelope.get("choices")
    if not isinstance(choices, list) or not choices:
        message = envelope.get("error")
        if message:
            raise LLMError(f"大模型返回了错误：{message}")
        raise LLMError("大模型没有返回内容，请稍后重试。")
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, list):  # 少数服务把正文拆成多段
        content = "".join(
            part.get("text", "") if isinstance(part, dict) else str(part) for part in content
        )
    if not isinstance(content, str) or not content.strip():
        raise LLMError("大模型没有返回内容，请稍后重试。")
    return content


def _describe_error(exc: Exception) -> str:
    """把不合格原因压成一句中文，既用于重试提示，也用于最终报错。"""
    if isinstance(exc, ValidationError):
        errors = exc.errors()
        if errors:
            first = errors[0]
            loc = ".".join(str(part) for part in first.get("loc", ()))
            return f"字段 {loc or '（整体）'} 的取值或结构不符合约定"
        return "输出字段或取值不符合约定"
    if isinstance(exc, json.JSONDecodeError):
        return "输出不是合法 JSON"
    return str(exc) or "输出不合格"


@dataclass
class LLMClient:
    """一个最小可用的 OpenAI 兼容客户端。"""

    base_url: str
    api_key: str
    model: str
    timeout: float = 120.0
    transport: Transport = field(default=_http_transport)

    @classmethod
    def from_settings(cls) -> "LLMClient":
        return cls(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key,
            model=settings.llm_model,
            timeout=float(getattr(settings, "llm_timeout", 120.0)),
        )

    @property
    def endpoint(self) -> str:
        base = (self.base_url or "").rstrip("/")
        if base.endswith("/chat/completions"):
            return base
        return base + "/chat/completions"

    # ------------------------------------------------------------ 单次调用

    def complete(self, system: str, user: str) -> str:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0,
            "stream": False,
        }
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {self.api_key}".strip(),
        }
        try:
            raw = self.transport(self.endpoint, headers, payload, float(self.timeout))
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                raise LLMError(
                    f"大模型接口鉴权失败（HTTP {exc.code}）。请检查 LLM_API_KEY 是否正确。"
                ) from exc
            if exc.code == 404:
                raise LLMError(
                    "大模型接口地址不对（HTTP 404）。请检查 LLM_BASE_URL 和模型名。"
                ) from exc
            if exc.code == 429:
                raise LLMError("大模型接口被限流了（HTTP 429），请稍后再试。") from exc
            raise LLMError(f"大模型接口返回错误（HTTP {exc.code}），请稍后再试。") from exc
        except LLMError:
            raise
        except (TimeoutError, socket.timeout) as exc:
            raise LLMError(
                f"大模型响应超时（超过 {int(float(self.timeout))} 秒）。请稍后再试，"
                "或让管理员把 LLM_TIMEOUT 调大一点。"
            ) from exc
        except urllib.error.URLError as exc:
            raise LLMError(
                "连不上大模型服务。请确认模型服务已启动，并检查 LLM_BASE_URL 配置"
                f"（当前是 {self.base_url}）。"
            ) from exc
        except Exception as exc:  # 传输层自定义异常，例如测试里的假传输
            raise LLMError(f"调用大模型失败：{exc}") from exc
        return _read_content(raw)

    # ------------------------------------------------------------ JSON 输出

    def request_json(
        self,
        system: str,
        user: str,
        model_cls: type[T],
        *,
        post_validate: Callable[[T], None] | None = None,
        retries: int = 1,
    ) -> T:
        """要求模型输出 JSON，严格校验；不合格只重试一次。

        `post_validate` 用于校验 JSON 之外的业务约束（例如原话必须是原文子串），
        抛 `ValueError` 视为不合格，会带着原因重试一次。
        """
        prompt = user
        last_reason = ""
        for attempt in range(retries + 1):
            content = self.complete(system, prompt)
            try:
                data = json.loads(_extract_json_text(content))
                result = model_cls.model_validate(data)
                if post_validate is not None:
                    post_validate(result)
                return result
            except (json.JSONDecodeError, ValidationError, ValueError) as exc:
                last_reason = _describe_error(exc)
                if attempt >= retries:
                    break
                prompt = (
                    user
                    + "\n\n【上一次输出被程序判为不合格，原因："
                    + last_reason
                    + "】\n请修正后重新输出完整 JSON：只输出 JSON，不要解释，不要 Markdown 代码块。"
                )
        raise LLMError(
            "大模型返回的结果不合格："
            + last_reason
            + "。已自动重试一次仍不合格，本次没有生成会话，请稍后重试或让管理员检查模型配置。"
        )


# ---------------------------------------------------------------- 进程内单例（可替换）


_override: LLMClient | None = None


def get_client() -> LLMClient:
    """默认按当前环境变量构造；测试或特殊部署可以用 `set_client` 换掉。"""
    if _override is not None:
        return _override
    return LLMClient.from_settings()


def set_client(client: LLMClient | None) -> None:
    global _override
    _override = client


def reset_client() -> None:
    set_client(None)
