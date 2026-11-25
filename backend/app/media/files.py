"""媒体文件落盘：图片内容校验、安全随机文件名、`/files/...` 访问路径。

只写 `settings.storage_dir / "files"` 下面三个子目录：

- `reference/`：客户上传的参考图
- `generated/`：生图适配器生成的效果图
- `uploads/`：业务员上传的外部效果图（只做核对）

约定：文件名一律用 `uuid4().hex` + 由魔术数字判定的后缀，绝不使用用户给的文件名，
因此不存在 `../`、重名和诡异字符的问题。`/files` 静态目录由 `app/main.py` 挂载。

任何不合规的图片都抛 `MediaFileError`（`str(exc)` 是给业务员看的中文），不会静默放过。
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import Any

from ..config import settings

REFERENCE_DIR = "reference"
GENERATED_DIR = "generated"
UPLOAD_DIR = "uploads"

SUBDIRS = (REFERENCE_DIR, GENERATED_DIR, UPLOAD_DIR)

# 服务端（vision/server_local.py）上限是 20MB；这里收得更紧，避免拖慢推理和静态访问。
MAX_IMAGE_BYTES = 12 * 1024 * 1024
MIN_IMAGE_BYTES = 64

_NAME_RE = re.compile(r"^[0-9a-f]{32}\.(jpg|png|webp|gif|bmp)$")

# 只按魔术数字判定，不信任文件名后缀。
_SIGNATURES: tuple[tuple[bytes, str, str], ...] = (
    (b"\xff\xd8\xff", "image/jpeg", ".jpg"),
    (b"\x89PNG\r\n\x1a\n", "image/png", ".png"),
    (b"GIF87a", "image/gif", ".gif"),
    (b"GIF89a", "image/gif", ".gif"),
    (b"BM", "image/bmp", ".bmp"),
)


class MediaFileError(ValueError):
    """图片文件不合规，`str(exc)` 是中文说明。"""


def files_root() -> Path:
    """`backend/storage/files`；测试里会改 `settings.storage_dir`，所以每次现取。"""
    return Path(settings.storage_dir) / "files"


def detect_image_type(data: bytes) -> tuple[str, str] | None:
    """按魔术数字判断图片类型，返回 (MIME, 后缀)；认不出返回 None。"""
    for signature, mime, suffix in _SIGNATURES:
        if data.startswith(signature):
            return mime, suffix
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp", ".webp"
    return None


def validate_image_bytes(
    data: bytes,
    *,
    max_bytes: int = MAX_IMAGE_BYTES,
    min_bytes: int = MIN_IMAGE_BYTES,
) -> tuple[str, str]:
    """校验图片内容与大小，返回 (MIME, 后缀)；不合规抛 `MediaFileError`。"""
    if not isinstance(data, (bytes, bytearray)):
        raise MediaFileError("图片数据格式不对，请重新上传。")
    payload = bytes(data)
    if len(payload) < min_bytes:
        raise MediaFileError("图片内容为空或过小，请重新上传一张完整的图片。")
    if len(payload) > max_bytes:
        raise MediaFileError(
            f"图片太大（{len(payload) // 1024 // 1024 + 1}MB 以上），请压缩到 "
            f"{max_bytes // 1024 // 1024}MB 以内再上传。"
        )
    kind = detect_image_type(payload)
    if kind is None:
        raise MediaFileError("这不是能识别的图片（支持 JPG、PNG、WEBP、GIF、BMP），请换一张。")
    return kind


def new_name(suffix: str) -> str:
    """安全随机文件名：32 位十六进制 + 后缀，不含任何用户输入。"""
    if not suffix.startswith(".") or not suffix[1:].isalpha():
        raise MediaFileError(f"内部错误：不合法的文件后缀 {suffix!r}。")
    return f"{uuid.uuid4().hex}{suffix.lower()}"


def url_for(subdir: str, name: str) -> str:
    """`/files/<子目录>/<文件名>`；静态目录由 `app/main.py` 挂载。"""
    if subdir not in SUBDIRS:
        raise MediaFileError(f"内部错误：未知的图片目录 {subdir!r}。")
    if not _NAME_RE.match(name):
        raise MediaFileError(f"内部错误：不合法的文件名 {name!r}。")
    return f"/files/{subdir}/{name}"


def write_bytes(data: bytes, subdir: str, name: str) -> Path:
    """把图片写到 `files/<子目录>/<名字>`，返回落盘路径。"""
    if subdir not in SUBDIRS:
        raise MediaFileError(f"内部错误：未知的图片目录 {subdir!r}。")
    if not _NAME_RE.match(name):
        raise MediaFileError(f"内部错误：不合法的文件名 {name!r}。")
    path = files_root() / subdir / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(data))
    return path


def save_image_bytes(data: bytes, subdir: str) -> tuple[str, str, Path]:
    """校验 + 随机命名 + 落盘，返回 (访问地址, 文件名, 落盘路径)。"""
    _, suffix = validate_image_bytes(data)
    name = new_name(suffix)
    return url_for(subdir, name), name, write_bytes(data, subdir, name)


def resolve_url_path(url: str) -> Path:
    """把 `/files/<子目录>/<名字>` 还原成本地路径（只读，用于核对生成的图）。"""
    prefix = "/files/"
    if not isinstance(url, str) or not url.startswith(prefix):
        raise MediaFileError("图片地址格式不对，只能读取本站 /files 下的图片。")
    parts = url[len(prefix):].split("/")
    if len(parts) != 2:
        raise MediaFileError("图片地址格式不对，只能读取本站 /files 下的图片。")
    subdir, name = parts
    url_for(subdir, name)  # 复用校验
    return files_root() / subdir / name


def read_image_url(url: str) -> tuple[bytes, str, str]:
    """读回一张 `/files` 图片，返回 (字节, MIME, 后缀)，并按内容重新校验。"""
    path = resolve_url_path(url)
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise MediaFileError(f"找不到图片文件：{url}（{exc.__class__.__name__}）。") from exc
    mime, suffix = validate_image_bytes(data)
    return data, mime, suffix


def sniff(data: Any) -> tuple[str, str]:
    """给路由用：不合规时把 `MediaFileError` 的说明原样带出去。"""
    return validate_image_bytes(data)


__all__ = [
    "REFERENCE_DIR", "GENERATED_DIR", "UPLOAD_DIR", "SUBDIRS",
    "MAX_IMAGE_BYTES", "MIN_IMAGE_BYTES", "MediaFileError",
    "files_root", "detect_image_type", "validate_image_bytes", "new_name",
    "url_for", "write_bytes", "save_image_bytes", "resolve_url_path",
    "read_image_url", "sniff",
]