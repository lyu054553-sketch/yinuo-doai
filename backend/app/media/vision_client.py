"""视觉服务客户端（C1）。

复用现有 `vision/server_local.py`（`--port 8001`），**不训练、不改它的代码**。

服务约定（见 vision/server_local.py 的 `do_POST`）：

- 请求：`POST {VISION_URL}/api/deconstruct`
- 请求体：`{"imageDataUrl": "data:image/jpeg;base64,..."}`
- 成功：200，`{"fields": {...}, "diagnostics": {...}, "local_only": true}`
  验证用的 `fields` 在**外层字段 `fields` 里**，必须解包后使用。
- 失败：400/500/504，`{"error": "<中文说明>"}`

`fields` 的结构见 `vision/schema/deconstruct.schema.json`：七个键
`collar_family / front_closure / buttons_visible / length / silhouette / pattern / colors`，
每个键都是 `{"value": ..., "confidence": "确定|疑似|看不出"}`。

本模块只做“取图 → 调用 → 解包 → 校验”，不裁剪字段（冻结策略在 `reference.py`）。
任何一步不对都抛带中文说明的异常，由路由映射成 503 / 502 / 504，绝不假装成功。
"""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from ..config import settings

CONFIDENCE_VALUES = ("确定", "疑似", "看不出")

# vision/schema/deconstruct.schema.json 的枚举，逐字照抄。
VALUE_ENUMS: dict[str, tuple[str, ...]] = {
    "collar_family": ("立领", "交领", "翻领", "斜襟", "圆领", "看不出"),
    "front_closure": ("外露扣", "无扣开襟", "交叠闭合", "系带", "看不出"),
    "length": ("臀部附近", "过臀", "到腰", "看不出"),
    "silhouette": ("H 型", "收腰", "A 摆", "看不出"),
}

REQUIRED_KEYS = (
    "collar_family",
    "front_closure",
    "buttons_visible",
    "length",
    "silhouette",
    "pattern",
    "colors",
)

MAX_BYTES = 20 * 1024 * 1024  # 服务端上限 20MB
DEFAULT_TIMEOUT = 320.0  # 服务端推理超时 300 秒，这里留一点余量


# ---------------------------------------------------------------- 异常


class VisionError(RuntimeError):
    """视觉相关错误基类，`str(exc)` 是可以直接给业务员看的中文。"""


class VisionServiceUnavailable(VisionError):
    """视觉服务没启动 / 连不上。路由应映射为 503。"""


class VisionTimeout(VisionError):
    """视觉服务推理超时。路由可映射为 504。"""


class VisionOutputError(VisionError):
    """视觉服务返回的内容不符合约定格式。路由可映射为 502。"""


UNAVAILABLE_MESSAGE = (
    "视觉服务未启动。请先运行 python vision/server_local.py --port 8001，"
    "参考图解析和效果图核对需要它；其他功能不受影响。"
)


# ---------------------------------------------------------------- 图片


def encode_image_data_url(image: bytes | bytearray, mime: str = "image/jpeg") -> str:
    """把图片字节转成 data URL。服务只接受 `data:image/...;base64,...`。"""
    if not mime.startswith("image/"):
        raise VisionError("只能上传图片文件（image/*）。")
    data = bytes(image)
    if not data:
        raise VisionError("图片为空，请重新选择。")
    if len(data) > MAX_BYTES:
        raise VisionError("图片超过 20MB，请压缩后再上传。")
    return f"data:{mime};base64," + base64.b64encode(data).decode("ascii")


def mime_from_name(name: str | None) -> str:
    """按文件名猜 MIME；认不出就按 jpeg。"""
    suffix = Path(name or "").suffix.lower()
    return {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
        ".bmp": "image/bmp",
        ".gif": "image/gif",
    }.get(suffix, "image/jpeg")


def encode_image_file(path: Path | str, mime: str | None = None) -> str:
    """读本地图片文件并转 data URL（C1 自测与路由都用它）。"""
    file_path = Path(path)
    if not file_path.is_file():
        raise VisionError(f"找不到图片文件：{file_path}")
    return encode_image_data_url(file_path.read_bytes(), mime or mime_from_name(file_path.name))


# ---------------------------------------------------------------- 校验


def _check_field(key: str, raw: Any) -> dict[str, Any]:
    """校验单个字段对象，返回 {"value":..., "confidence":...}。"""
    if not isinstance(raw, Mapping):
        raise VisionOutputError(f"视觉结果里的“{key}”不是对象。")
    unknown = set(raw) - {"value", "confidence"}
    if unknown:
        raise VisionOutputError(f"视觉结果里的“{key}”出现未知键：{'、'.join(sorted(unknown))}。")
    if "value" not in raw or "confidence" not in raw:
        raise VisionOutputError(f"视觉结果里的“{key}”缺少 value 或 confidence。")
    confidence = raw["confidence"]
    if confidence not in CONFIDENCE_VALUES:
        raise VisionOutputError(f"视觉结果里的“{key}”把握程度不合法：{confidence!r}。")
    value = raw["value"]

    if key == "buttons_visible":
        if value is True or value is False:
            return {"value": value, "confidence": confidence}
        if value == "看不出":
            return {"value": "看不出", "confidence": confidence}
        raise VisionOutputError(f"视觉结果里的“纽扣”取值不合法：{value!r}。")

    if key == "colors":
        if not isinstance(value, list) or not 1 <= len(value) <= 3:
            raise VisionOutputError("视觉结果里的“颜色”必须是 1–3 个中文颜色词。")
        if any((not isinstance(c, str)) or not c.strip() or len(c) > 20 for c in value):
            raise VisionOutputError("视觉结果里的“颜色”含有空值或过长的颜色词。")
        return {"value": [c.strip() for c in value], "confidence": confidence}

    if key == "pattern":
        if not isinstance(value, str) or not value.strip() or len(value) > 80:
            raise VisionOutputError("视觉结果里的“纹样”必须是 1–80 字的文字描述。")
        return {"value": value.strip(), "confidence": confidence}

    allowed = VALUE_ENUMS.get(key)
    if allowed is None:
        raise VisionOutputError(f"视觉结果里出现未约定的字段：{key}。")
    if value not in allowed:
        raise VisionOutputError(f"视觉结果里的“{key}”取值不在词表内：{value!r}。")
    return {"value": value, "confidence": confidence}


def normalize_fields(raw: Any) -> dict[str, dict[str, Any]]:
    """校验并规整视觉结果的七个字段；缺字段或未知字段直接报错，不硬凑。"""
    if not isinstance(raw, Mapping):
        raise VisionOutputError("视觉结果不是对象。")
    missing = [key for key in REQUIRED_KEYS if key not in raw]
    if missing:
        raise VisionOutputError(f"视觉结果缺少字段：{'、'.join(missing)}。")
    unknown = [key for key in raw if key not in REQUIRED_KEYS]
    if unknown:
        raise VisionOutputError(f"视觉结果出现未知字段：{'、'.join(unknown)}。")
    return {key: _check_field(key, raw[key]) for key in REQUIRED_KEYS}


# ---------------------------------------------------------------- 调用


@dataclass
class DeconstructResult:
    fields: dict[str, dict[str, Any]]
    model: str
    diagnostics: dict[str, Any] = field(default_factory=dict)


def _unique_sources(diagnostics: Mapping[str, Any]) -> list[str]:
    """从 `diagnostics.fields[*].source` 按出现顺序去重提取模型来源。

    `vision/scripts/predict_local.py` 的调试输出里，每个字段都带 `source`，例如
    “FashionSigLIP zero-shot”“Qwen2.5-VL 7B local”“Qwen2.5-VL 3B local, 3-vote majority”。
    只照抄服务自己回报的字符串，不拼造任何型号。
    """
    fields = diagnostics.get("fields")
    if not isinstance(fields, Mapping):
        return []
    sources: list[str] = []
    for value in fields.values():
        if not isinstance(value, Mapping):
            continue
        source = value.get("source")
        if isinstance(source, str) and source.strip():
            text = source.strip()
            if text not in sources:
                sources.append(text)
    return sources


def _model_name(payload: Mapping[str, Any], vision_url: str) -> str:
    """模型标识：优先按 `diagnostics.fields[*].source` 去重列出实际来源；
    没有来源信息时才看 `model` / `model_name` / `models`；再没有就用服务地址，
    绝不编造模型型号。"""
    diagnostics = payload.get("diagnostics")
    if isinstance(diagnostics, Mapping):
        sources = _unique_sources(diagnostics)
        if sources:
            return "+".join(sources)
        for key in ("model", "model_name", "models"):
            value = diagnostics.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
            if isinstance(value, list) and value and all(isinstance(v, str) for v in value):
                return "+".join(value)
    return f"vision@{vision_url}"


def _post_json(url: str, body: dict[str, Any], timeout: float) -> Any:
    """发 POST；网络错误转成中文异常。"""
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json; charset=utf-8"}, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = json.loads(exc.read().decode("utf-8")).get("error", "")
        except Exception:  # 响应不是 JSON 也要能给出话
            detail = ""
        if exc.code == 504:
            raise VisionTimeout(f"视觉服务推理超时：{detail or '请稍后重试'}") from exc
        if exc.code in (502, 503):
            raise VisionServiceUnavailable(f"视觉服务暂不可用（HTTP {exc.code}）：{detail}") from exc
        raise VisionOutputError(
            f"视觉服务处理图片失败（HTTP {exc.code}）：{detail or '没有返回原因'}"
        ) from exc
    except TimeoutError as exc:
        raise VisionTimeout("视觉服务推理超时，请稍后重试或换一张更清晰的图。") from exc
    except (urllib.error.URLError, ConnectionError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        raise VisionServiceUnavailable(UNAVAILABLE_MESSAGE + f"（连接 {url} 失败：{reason}）") from exc
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VisionOutputError("视觉服务返回的不是合法 JSON。") from exc


def deconstruct(
    image_data_url: str,
    *,
    vision_url: str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    attempts: int = 2,
) -> DeconstructResult:
    """调用视觉服务看图解构。

    `attempts=2`：结果校验不通过时重试一次（接口约定第 8 节），仍不通过就抛错。
    返回 `DeconstructResult(fields=已校验的七字段, model=模型标识, diagnostics=原始诊断信息)`。
    """
    if not isinstance(image_data_url, str) or not image_data_url.startswith("data:image/"):
        raise VisionError("图片数据格式不对，需要 data:image/...;base64,...。")
    base = (vision_url or settings.vision_url).rstrip("/")
    url = f"{base}/api/deconstruct"

    last_error: VisionOutputError | None = None
    total = max(1, attempts)
    for attempt in range(total):
        payload = _post_json(url, {"imageDataUrl": image_data_url}, timeout)
        if not isinstance(payload, Mapping):
            last_error = VisionOutputError("视觉服务返回的不是对象。")
        else:
            # 服务把验证结果放在外层 `fields`，这里必须解包；
            # 如果外层没有 fields，视作格式错误，不把整个 payload 当字段硬凑。
            if "fields" not in payload:
                raise VisionOutputError(
                    "视觉服务返回的内容缺少 fields，无法解读；请确认服务是 vision/server_local.py。"
                )
            try:
                fields = normalize_fields(payload["fields"])
            except VisionOutputError as exc:
                last_error = exc
            else:
                diagnostics = payload.get("diagnostics")
                return DeconstructResult(
                    fields=fields,
                    model=_model_name(payload, base),
                    diagnostics=dict(diagnostics) if isinstance(diagnostics, Mapping) else {},
                )
        if attempt + 1 < total:
            continue
    assert last_error is not None
    raise VisionOutputError(
        f"视觉服务返回的结果不符合约定格式（已重试 {total} 次）：{last_error}"
    )


__all__ = [
    "CONFIDENCE_VALUES", "VALUE_ENUMS", "REQUIRED_KEYS", "UNAVAILABLE_MESSAGE",
    "VisionError", "VisionServiceUnavailable", "VisionTimeout", "VisionOutputError",
    "DeconstructResult", "encode_image_data_url", "encode_image_file", "mime_from_name",
    "normalize_fields", "deconstruct",
]