"""生图适配器（C3）：默认 `IMAGE_PROVIDER=none`，只给提示词、不出图、不发任何请求。

两种实现：

- `none`（默认）：不发请求，返回 `url=None`，由路由记一笔“出图（仅提示词）”台账。
- `openai_compatible`：调 OpenAI 兼容的 `/images/generations`（默认关闭，且需要先得到
  项目负责人同意才会真正联网）。图片按 b64 取回、按魔术数字校验后存到
  `files/generated/`，再通过 `/files/...` 访问。

红线：

- API Key 只从 `settings.image_api_key`（环境变量）读，不写日志、不回显、不进提示词。
- 本模块 import 时**不**发任何网络请求；`generate()` 只在被调用时联网。
- 单元测试通过替换模块内 `_post_json` 走假 transport，**不产生任何真实付费调用**。

异常统一抛 `ImageProviderError`，`str(exc)` 是给业务员看的中文。

    from app.media import image_provider
    provider = image_provider.get_provider()          # 按环境变量选实现
    asset = provider.generate(prompt_text, "成衣挂拍")  # -> GeneratedAsset(url=... | None)
"""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Mapping

from ..config import Settings, settings
from . import files

PROVIDER_NONE = "none"
PROVIDER_OPENAI_COMPATIBLE = "openai_compatible"
UPLOAD_PROVIDER = "upload"  # 业务员上传的外部图片，只做核对

SUPPORTED_PROVIDERS = (PROVIDER_NONE, PROVIDER_OPENAI_COMPATIBLE)

# 各图类的出图尺寸；只影响生图请求，不影响提示词（提示词仍逐字确定）。
IMAGE_SIZE_BY_KIND = {
    "成衣挂拍": "1024x1024",
    "面料细节": "1024x1024",
    "上身效果": "1024x1536",
}

DEFAULT_TIMEOUT = 180.0


class ImageProviderError(RuntimeError):
    """生图相关错误，`str(exc)` 是中文。路由映射为 502。"""


@dataclass(frozen=True)
class GeneratedAsset:
    """一次出图的结果；`url=None` 表示只生成了提示词、没有图。"""

    url: str | None
    provider: str
    prompt: str
    note: str = ""


def _check_kind(kind: str) -> str:
    if kind not in IMAGE_SIZE_BY_KIND:
        raise ImageProviderError(
            "图片类型只能是“成衣挂拍”“面料细节”“上身效果”。"
        )
    return kind


def _check_prompt(prompt: Any) -> str:
    if not isinstance(prompt, str) or not prompt.strip():
        raise ImageProviderError("提示词为空，无法出图；请先确认这个会话有方案版本。")
    return prompt


# ---------------------------------------------------------------- 假 transport 可替换


def _post_json(url: str, headers: Mapping[str, str], body: Mapping[str, Any], timeout: float) -> Any:
    """发 POST 并解析 JSON。网络与 HTTP 错误都转成中文的 `ImageProviderError`。

    单元测试替换本函数即可完全离线；本函数只在 `generate()` 里被调用。
    """
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json; charset=utf-8", **dict(headers)},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = json.loads(exc.read().decode("utf-8")).get("error", "")
            if isinstance(detail, Mapping):
                detail = detail.get("message", "") or json.dumps(detail, ensure_ascii=False)
        except Exception:  # 响应不是 JSON 也要能给出话
            detail = ""
        raise ImageProviderError(
            f"生图接口返回错误（HTTP {exc.code}）：{detail or '没有返回原因'}"
        ) from exc
    except TimeoutError as exc:
        raise ImageProviderError("生图接口超时，请稍后重试；也可以先用外部图片上传只做核对。") from exc
    except (urllib.error.URLError, ConnectionError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        raise ImageProviderError(
            f"连不上生图接口（{url}）：{reason}；可以先用外部图片上传只做核对。"
        ) from exc
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ImageProviderError("生图接口返回的不是合法 JSON。") from exc


# ---------------------------------------------------------------- 两种实现


class NoneProvider:
    """默认实现：只把提示词交出去，不发请求，不出图。"""

    name = PROVIDER_NONE

    def generate(self, prompt: str, kind: str) -> GeneratedAsset:
        _check_kind(kind)
        return GeneratedAsset(
            url=None,
            provider=self.name,
            prompt=_check_prompt(prompt),
            note="未配置生图接口（IMAGE_PROVIDER=none），只生成提示词，没有出图，也没有任何外部请求。",
        )


class OpenAICompatibleProvider:
    """OpenAI 兼容生图接口。只有配置齐全、且被明确同意后才会真正联网。"""

    name = PROVIDER_OPENAI_COMPATIBLE

    def __init__(self, base_url: str, api_key: str, model: str, timeout: float = DEFAULT_TIMEOUT):
        base = (base_url or "").strip().rstrip("/")
        if not base:
            raise ImageProviderError("生图接口配置不完整：缺少 IMAGE_BASE_URL。")
        if not (api_key or "").strip():
            raise ImageProviderError("生图接口配置不完整：缺少 IMAGE_API_KEY。")
        if not (model or "").strip():
            raise ImageProviderError("生图接口配置不完整：缺少 IMAGE_MODEL。")
        self.url = f"{base}/images/generations"
        self._api_key = api_key.strip()
        self.model = model.strip()
        self.timeout = float(timeout)

    def __repr__(self) -> str:  # 绝不把 Key 打进日志
        return f"OpenAICompatibleProvider(url={self.url!r}, model={self.model!r})"

    def _extract_b64(self, payload: Any) -> str:
        if not isinstance(payload, Mapping):
            raise ImageProviderError("生图接口返回的不是对象。")
        data = payload.get("data")
        if not isinstance(data, list) or not data:
            raise ImageProviderError("生图接口没有返回图片数据（data 为空）。")
        first = data[0]
        if not isinstance(first, Mapping):
            raise ImageProviderError("生图接口返回的 data 结构不符合约定。")
        encoded = first.get("b64_json")
        if not isinstance(encoded, str) or not encoded.strip():
            if first.get("url"):
                raise ImageProviderError(
                    "生图接口只返回了图片地址，没有返回 b64_json；本适配器只接受 b64_json。"
                )
            raise ImageProviderError("生图接口没有返回 b64_json 图片数据。")
        return encoded.strip()

    def generate(self, prompt: str, kind: str) -> GeneratedAsset:
        _check_kind(kind)
        text = _check_prompt(prompt)
        body = {
            "model": self.model,
            "prompt": text,
            "n": 1,
            "size": IMAGE_SIZE_BY_KIND[kind],
            "response_format": "b64_json",
        }
        payload = _post_json(
            self.url,
            {"Authorization": f"Bearer {self._api_key}"},
            body,
            self.timeout,
        )
        encoded = self._extract_b64(payload)
        try:
            data = base64.b64decode(encoded, validate=True)
        except Exception as exc:
            raise ImageProviderError("生图接口返回的图片数据不是合法 base64。") from exc
        try:
            url, _, _ = files.save_image_bytes(data, files.GENERATED_DIR)
        except files.MediaFileError as exc:
            raise ImageProviderError(f"生图接口返回的图片不可用：{exc}") from exc
        return GeneratedAsset(
            url=url,
            provider=self.name,
            prompt=text,
            note=f"由 {self.model} 生成（{IMAGE_SIZE_BY_KIND[kind]}）。",
        )


# ---------------------------------------------------------------- 选择实现


def get_provider(name: str | None = None, config: Settings | None = None) -> Any:
    """按 `IMAGE_PROVIDER` 选实现；配置不对就抛中文的 `ImageProviderError`。"""
    config = config or settings
    raw = (name if name is not None else config.image_provider) or PROVIDER_NONE
    provider_name = raw.strip().lower()
    if provider_name == PROVIDER_NONE:
        return NoneProvider()
    if provider_name == PROVIDER_OPENAI_COMPATIBLE:
        return OpenAICompatibleProvider(
            config.image_base_url, config.image_api_key, config.image_model
        )
    raise ImageProviderError(
        f"生图接口配置不正确：IMAGE_PROVIDER 只能是 none 或 openai_compatible（当前是 {raw!r}）。"
    )


__all__ = [
    "PROVIDER_NONE", "PROVIDER_OPENAI_COMPATIBLE", "UPLOAD_PROVIDER",
    "SUPPORTED_PROVIDERS", "IMAGE_SIZE_BY_KIND", "DEFAULT_TIMEOUT",
    "ImageProviderError", "GeneratedAsset", "NoneProvider",
    "OpenAICompatibleProvider", "get_provider",
]