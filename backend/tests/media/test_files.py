"""C3 单元测试：图片内容校验、安全随机文件名、`/files` 路径（不联网）。"""

from __future__ import annotations

import base64

import pytest

from app.config import settings
from app.media import files

# 1×1 真 PNG 头；尾部补零只为保证长度超过 64 字节的最小长度检查
PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
) + b"\x00" * 64
JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 200
GIF_BYTES = b"GIF89a" + b"\x00" * 200
BMP_BYTES = b"BM" + b"\x00" * 200
WEBP_BYTES = b"RIFF" + b"\x00" * 4 + b"WEBP" + b"\x00" * 200


@pytest.fixture
def storage(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "storage_dir", tmp_path)
    return tmp_path


# ------------------------------------------------------------ 内容判定


def test_detect_image_type_by_magic_number():
    assert files.detect_image_type(PNG_BYTES) == ("image/png", ".png")
    assert files.detect_image_type(JPEG_BYTES) == ("image/jpeg", ".jpg")
    assert files.detect_image_type(GIF_BYTES) == ("image/gif", ".gif")
    assert files.detect_image_type(BMP_BYTES) == ("image/bmp", ".bmp")
    assert files.detect_image_type(WEBP_BYTES) == ("image/webp", ".webp")


def test_detect_image_type_rejects_text():
    assert files.detect_image_type(b"hello world, not an image") is None


def test_validate_accepts_real_image():
    assert files.validate_image_bytes(PNG_BYTES) == ("image/png", ".png")


def test_validate_rejects_text_even_with_image_extension():
    """不看文件名，只看内容：改后缀的文本文件必须被拦住。

    输入故意超过 64 字节的最小长度，确保走到“文件类型”分支，而不是先被
    “内容过小”拦住（真实校验逻辑不为测试让步）。
    """
    payload = b"<html>not image</html>" + b" " * 64
    assert len(payload) >= files.MIN_IMAGE_BYTES
    assert files.detect_image_type(payload) is None
    with pytest.raises(files.MediaFileError) as exc:
        files.validate_image_bytes(payload)
    assert "不是能识别的图片" in str(exc.value)


def test_validate_rejects_empty_and_tiny():
    with pytest.raises(files.MediaFileError) as exc:
        files.validate_image_bytes(b"")
    assert "过小" in str(exc.value)
    with pytest.raises(files.MediaFileError):
        files.validate_image_bytes(b"ab")


def test_validate_rejects_oversized(monkeypatch):
    with pytest.raises(files.MediaFileError) as exc:
        files.validate_image_bytes(PNG_BYTES, max_bytes=32)
    assert "太大" in str(exc.value)


# ------------------------------------------------------------ 文件名


def test_new_name_is_random_and_hex_only():
    names = {files.new_name(".png") for _ in range(20)}
    assert len(names) == 20
    for name in names:
        assert len(name) == 36 and name.endswith(".png")
        assert name[:32].isalnum() and name[:32].lower() == name[:32]
        assert "/" not in name and ".." not in name


def test_new_name_rejects_bad_suffix():
    with pytest.raises(files.MediaFileError):
        files.new_name("png")
    with pytest.raises(files.MediaFileError):
        files.new_name("../png")


def test_url_for_rejects_unknown_dir_and_bad_name():
    name = files.new_name(".png")
    with pytest.raises(files.MediaFileError):
        files.url_for("images", name)
    with pytest.raises(files.MediaFileError):
        files.url_for(files.GENERATED_DIR, "../../etc/passwd")


# ------------------------------------------------------------ 落盘与读回


def test_save_image_bytes_writes_under_storage_files(storage):
    url, name, path = files.save_image_bytes(PNG_BYTES, files.GENERATED_DIR)
    assert url == f"/files/generated/{name}"
    assert path == storage / "files" / "generated" / name
    assert path.read_bytes() == PNG_BYTES


def test_save_image_bytes_creates_dir_on_demand(storage):
    assert not (storage / "files" / "uploads").exists()
    files.save_image_bytes(PNG_BYTES, files.UPLOAD_DIR)
    assert (storage / "files" / "uploads").is_dir()


def test_read_image_url_roundtrip(storage):
    url, _, _ = files.save_image_bytes(PNG_BYTES, files.REFERENCE_DIR)
    data, mime, suffix = files.read_image_url(url)
    assert data == PNG_BYTES and mime == "image/png" and suffix == ".png"


def test_read_image_url_rejects_other_paths():
    with pytest.raises(files.MediaFileError):
        files.read_image_url("http://example.com/a.png")
    with pytest.raises(files.MediaFileError):
        files.read_image_url("/files/images/c1_garment.jpeg")
    with pytest.raises(files.MediaFileError):
        files.read_image_url("/files/generated/../reference/x.png")


def test_read_image_url_missing_file(storage):
    name = files.new_name(".png")
    with pytest.raises(files.MediaFileError) as exc:
        files.read_image_url(files.url_for(files.GENERATED_DIR, name))
    assert "找不到图片文件" in str(exc.value)


def test_write_bytes_rejects_bad_names(storage):
    with pytest.raises(files.MediaFileError):
        files.write_bytes(PNG_BYTES, files.GENERATED_DIR, "a.png")
    with pytest.raises(files.MediaFileError):
        files.write_bytes(PNG_BYTES, "sessions", files.new_name(".png"))