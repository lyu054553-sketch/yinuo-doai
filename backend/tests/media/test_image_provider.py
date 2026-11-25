"""C3 单元测试：生图适配器。全程假 transport，**不产生任何真实付费调用**。"""

from __future__ import annotations

import base64
import json

import pytest

from app.config import settings
from app.media import image_provider

PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
) + b"\x00" * 64  # 补足最小长度，PNG 头不变
PROMPT = "成衣挂拍照片：单件上装正面展示。\n【品类】外套。"


@pytest.fixture
def storage(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "storage_dir", tmp_path)
    return tmp_path


@pytest.fixture
def no_network(monkeypatch):
    """把真实 transport 换成“一调就炸”，用来证明某些分支没有联网。"""

    calls: list[tuple] = []

    def boom(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("不应该发起任何网络请求")

    monkeypatch.setattr(image_provider, "_post_json", boom)
    return calls


@pytest.fixture
def fake_transport(monkeypatch):
    """假 transport：返回预设 payload，并记录 URL / headers / body。"""

    class Transport:
        def __init__(self):
            self.calls: list[tuple[str, dict, dict, float]] = []
            self.payload = {"data": [{"b64_json": base64.b64encode(PNG_BYTES).decode()}]}

        def __call__(self, url, headers, body, timeout):
            self.calls.append((url, dict(headers), dict(body), timeout))
            return self.payload

    transport = Transport()
    monkeypatch.setattr(image_provider, "_post_json", transport)
    return transport


def configured(monkeypatch, **overrides):
    values = {
        "image_provider": "openai_compatible",
        "image_base_url": "http://fake-image.local/v1",
        "image_api_key": "test-key-not-real",
        "image_model": "fake-image-model",
    }
    values.update(overrides)
    for key, value in values.items():
        monkeypatch.setattr(settings, key, value)


# ------------------------------------------------------------ none（默认）


def test_default_provider_is_none(monkeypatch):
    monkeypatch.setattr(settings, "image_provider", "none")
    provider = image_provider.get_provider()
    assert isinstance(provider, image_provider.NoneProvider)
    assert provider.name == "none"


def test_none_provider_makes_no_request(no_network):
    asset = image_provider.NoneProvider().generate(PROMPT, "成衣挂拍")
    assert asset.url is None
    assert asset.provider == "none"
    assert asset.prompt == PROMPT
    assert "未配置生图接口" in asset.note
    assert no_network == []


def test_none_provider_rejects_bad_kind():
    with pytest.raises(image_provider.ImageProviderError) as exc:
        image_provider.NoneProvider().generate(PROMPT, "视频")
    assert "图片类型只能是" in str(exc.value)


def test_none_provider_rejects_empty_prompt():
    with pytest.raises(image_provider.ImageProviderError):
        image_provider.NoneProvider().generate("   ", "成衣挂拍")


# ------------------------------------------------------------ 选择实现


def test_unknown_provider_is_rejected(monkeypatch):
    monkeypatch.setattr(settings, "image_provider", "some-cloud")
    with pytest.raises(image_provider.ImageProviderError) as exc:
        image_provider.get_provider()
    assert "IMAGE_PROVIDER 只能是 none 或 openai_compatible" in str(exc.value)


def test_openai_provider_requires_full_config(monkeypatch):
    for missing in ("image_base_url", "image_api_key", "image_model"):
        configured(monkeypatch, **{missing: ""})
        with pytest.raises(image_provider.ImageProviderError) as exc:
            image_provider.get_provider()
        assert "配置不完整" in str(exc.value)


def test_repr_does_not_leak_api_key(monkeypatch):
    configured(monkeypatch)
    provider = image_provider.get_provider()
    assert "test-key-not-real" not in repr(provider)
    assert "fake-image-model" in repr(provider)


# ------------------------------------------------------------ openai_compatible（假 transport）


def test_generate_saves_image_and_returns_url(monkeypatch, storage, fake_transport):
    configured(monkeypatch)
    provider = image_provider.get_provider()
    asset = provider.generate(PROMPT, "上身效果")

    assert asset.provider == "openai_compatible"
    assert asset.url and asset.url.startswith("/files/generated/")
    assert asset.prompt == PROMPT
    path = storage / "files" / "generated" / asset.url.rsplit("/", 1)[1]
    assert path.read_bytes() == PNG_BYTES

    url, headers, body, timeout = fake_transport.calls[0]
    assert url == "http://fake-image.local/v1/images/generations"
    assert headers["Authorization"] == "Bearer test-key-not-real"
    assert body["model"] == "fake-image-model"
    assert body["prompt"] == PROMPT
    assert body["response_format"] == "b64_json"
    assert body["size"] == "1024x1536"  # 上身效果用竖图
    assert timeout > 0


def test_generate_size_follows_kind(monkeypatch, storage, fake_transport):
    configured(monkeypatch)
    provider = image_provider.get_provider()
    provider.generate(PROMPT, "面料细节")
    assert fake_transport.calls[0][2]["size"] == "1024x1024"


def test_generate_does_not_put_key_in_body(monkeypatch, storage, fake_transport):
    configured(monkeypatch)
    provider = image_provider.get_provider()
    provider.generate(PROMPT, "成衣挂拍")
    body_text = json.dumps(fake_transport.calls[0][2], ensure_ascii=False)
    assert "test-key-not-real" not in body_text


def test_generate_rejects_payload_without_b64(monkeypatch, storage, fake_transport):
    configured(monkeypatch)
    fake_transport.payload = {"data": [{"url": "http://example.com/a.png"}]}
    provider = image_provider.get_provider()
    with pytest.raises(image_provider.ImageProviderError) as exc:
        provider.generate(PROMPT, "成衣挂拍")
    assert "b64_json" in str(exc.value)


def test_generate_rejects_empty_data(monkeypatch, storage, fake_transport):
    configured(monkeypatch)
    fake_transport.payload = {"data": []}
    with pytest.raises(image_provider.ImageProviderError):
        image_provider.get_provider().generate(PROMPT, "成衣挂拍")


def test_generate_rejects_invalid_base64(monkeypatch, storage, fake_transport):
    configured(monkeypatch)
    fake_transport.payload = {"data": [{"b64_json": "不是base64***"}]}
    with pytest.raises(image_provider.ImageProviderError) as exc:
        image_provider.get_provider().generate(PROMPT, "成衣挂拍")
    assert "base64" in str(exc.value)


def test_generate_rejects_non_image_payload(monkeypatch, storage, fake_transport):
    configured(monkeypatch)
    fake_transport.payload = {
        "data": [{"b64_json": base64.b64encode("<html>不是图片</html>".encode()).decode()}]
    }
    with pytest.raises(image_provider.ImageProviderError) as exc:
        image_provider.get_provider().generate(PROMPT, "成衣挂拍")
    assert "不可用" in str(exc.value)


# ------------------------------------------------------------ 真实 transport 的错误转中文（不联网）


def test_transport_connection_error_is_chinese(monkeypatch):
    import urllib.error

    def refuse(*args, **kwargs):
        raise urllib.error.URLError(ConnectionRefusedError(61, "Connection refused"))

    monkeypatch.setattr(image_provider.urllib.request, "urlopen", refuse)
    with pytest.raises(image_provider.ImageProviderError) as exc:
        image_provider._post_json("http://127.0.0.1:9/images", {}, {}, 1.0)
    assert "连不上生图接口" in str(exc.value)


def test_transport_http_error_is_chinese(monkeypatch):
    import io
    import urllib.error

    body = io.BytesIO(json.dumps({"error": "余额不足"}, ensure_ascii=False).encode("utf-8"))

    def fail(*args, **kwargs):
        raise urllib.error.HTTPError("http://x", 402, "pay", {}, body)

    monkeypatch.setattr(image_provider.urllib.request, "urlopen", fail)
    with pytest.raises(image_provider.ImageProviderError) as exc:
        image_provider._post_json("http://x/images", {}, {}, 1.0)
    assert "HTTP 402" in str(exc.value) and "余额不足" in str(exc.value)