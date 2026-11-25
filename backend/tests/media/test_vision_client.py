"""vision_client 的单元测试：不连真实视觉服务，全部替换网络调用。"""

from __future__ import annotations

import io
import json
import urllib.error

import pytest

from app.media import vision_client


def valid_fields() -> dict:
    return {
        "collar_family": {"value": "立领", "confidence": "确定"},
        "front_closure": {"value": "交叠闭合", "confidence": "疑似"},
        "buttons_visible": {"value": False, "confidence": "疑似"},
        "length": {"value": "臀部附近", "confidence": "确定"},
        "silhouette": {"value": "H 型", "confidence": "疑似"},
        "pattern": {"value": "无可见花纹", "confidence": "疑似"},
        "colors": {"value": ["藏青"], "confidence": "疑似"},
    }


class FakePost:
    """按顺序返回预设响应（或抛预设异常），并记录调用次数。"""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = 0

    def __call__(self, url, body, timeout):
        self.calls += 1
        item = self.responses[min(self.calls - 1, len(self.responses) - 1)]
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture
def no_sleep(monkeypatch):
    monkeypatch.setattr(vision_client, "_post_json", lambda *a, **k: None)


# ------------------------------------------------------------ 字段校验


def test_normalize_fields_accepts_schema_shaped_result():
    fields = vision_client.normalize_fields(valid_fields())
    assert fields["collar_family"] == {"value": "立领", "confidence": "确定"}
    assert fields["buttons_visible"]["value"] is False
    assert fields["colors"]["value"] == ["藏青"]


def test_normalize_fields_rejects_missing_key():
    raw = valid_fields()
    del raw["pattern"]
    with pytest.raises(vision_client.VisionOutputError) as exc:
        vision_client.normalize_fields(raw)
    assert "缺少字段" in str(exc.value) and "pattern" in str(exc.value)


def test_normalize_fields_rejects_unknown_key():
    raw = valid_fields()
    raw["seasons"] = {"value": "夏", "confidence": "疑似"}
    with pytest.raises(vision_client.VisionOutputError):
        vision_client.normalize_fields(raw)


def test_normalize_fields_rejects_bad_confidence():
    raw = valid_fields()
    raw["collar_family"]["confidence"] = "很确定"
    with pytest.raises(vision_client.VisionOutputError):
        vision_client.normalize_fields(raw)


def test_normalize_fields_rejects_value_outside_vocabulary():
    raw = valid_fields()
    raw["collar_family"]["value"] = "V 领"
    with pytest.raises(vision_client.VisionOutputError):
        vision_client.normalize_fields(raw)


def test_normalize_fields_rejects_too_many_colors():
    raw = valid_fields()
    raw["colors"]["value"] = ["黑", "白", "灰", "米"]
    with pytest.raises(vision_client.VisionOutputError):
        vision_client.normalize_fields(raw)


def test_normalize_fields_rejects_buttons_text_value():
    raw = valid_fields()
    raw["buttons_visible"]["value"] = "有"
    with pytest.raises(vision_client.VisionOutputError):
        vision_client.normalize_fields(raw)


# ------------------------------------------------------------ 解包与重试


def test_deconstruct_unwraps_fields_and_diagnostics(monkeypatch):
    payload = {
        "fields": valid_fields(),
        "diagnostics": {"model": "qwen2.5vl:7b", "timings": {"total": 12.3}},
        "local_only": True,
    }
    fake = FakePost(payload)
    monkeypatch.setattr(vision_client, "_post_json", fake)

    result = vision_client.deconstruct("data:image/jpeg;base64,AAAA")

    assert fake.calls == 1
    assert result.model == "qwen2.5vl:7b"
    assert result.fields["collar_family"]["value"] == "立领"
    assert result.diagnostics["timings"]["total"] == 12.3


def test_deconstruct_rejects_payload_without_fields_wrapper(monkeypatch):
    """服务若只回平铺字段（没有外层 fields），必须报错，不能当成功。"""
    fake = FakePost(valid_fields())
    monkeypatch.setattr(vision_client, "_post_json", fake)
    with pytest.raises(vision_client.VisionOutputError) as exc:
        vision_client.deconstruct("data:image/jpeg;base64,AAAA")
    assert "缺少 fields" in str(exc.value)


def test_deconstruct_retries_once_then_succeeds(monkeypatch):
    broken = valid_fields()
    del broken["silhouette"]
    good = {"fields": valid_fields(), "diagnostics": {}}
    fake = FakePost({"fields": broken, "diagnostics": {}}, good)
    monkeypatch.setattr(vision_client, "_post_json", fake)

    result = vision_client.deconstruct("data:image/jpeg;base64,AAAA")
    assert fake.calls == 2
    assert result.fields["silhouette"]["value"] == "H 型"


def test_deconstruct_fails_loudly_after_two_bad_attempts(monkeypatch):
    broken = {"fields": {"collar_family": {"value": "立领", "confidence": "疑似"}}}
    fake = FakePost(broken)
    monkeypatch.setattr(vision_client, "_post_json", fake)
    with pytest.raises(vision_client.VisionOutputError) as exc:
        vision_client.deconstruct("data:image/jpeg;base64,AAAA")
    assert fake.calls == 2
    assert "重试 2 次" in str(exc.value)


def test_deconstruct_requires_data_url():
    with pytest.raises(vision_client.VisionError):
        vision_client.deconstruct("http://example.com/a.jpg")


# ------------------------------------------------------------ 网络错误 → 中文异常


def test_connection_error_becomes_service_unavailable(monkeypatch):
    def boom(*args, **kwargs):
        raise urllib.error.URLError(ConnectionRefusedError(61, "Connection refused"))

    monkeypatch.setattr(vision_client.urllib.request, "urlopen", boom)
    with pytest.raises(vision_client.VisionServiceUnavailable) as exc:
        vision_client.deconstruct("data:image/jpeg;base64,AAAA")
    assert "视觉服务未启动" in str(exc.value)
    assert "8001" in str(exc.value)


def _http_error(code: int, body: dict) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        "http://127.0.0.1:8001/api/deconstruct",
        code,
        "error",
        {},
        io.BytesIO(json.dumps(body, ensure_ascii=False).encode("utf-8")),
    )


def test_http_504_becomes_timeout(monkeypatch):
    monkeypatch.setattr(
        vision_client.urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(_http_error(504, {"error": "本机推理超时"}))
    )
    with pytest.raises(vision_client.VisionTimeout):
        vision_client.deconstruct("data:image/jpeg;base64,AAAA")


def test_http_500_carries_service_message(monkeypatch):
    monkeypatch.setattr(
        vision_client.urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(_http_error(500, {"error": "模型未加载"}))
    )
    with pytest.raises(vision_client.VisionOutputError) as exc:
        vision_client.deconstruct("data:image/jpeg;base64,AAAA")
    assert "模型未加载" in str(exc.value)


# ------------------------------------------------------------ 图片编码


def test_encode_image_data_url_rejects_empty_bytes():
    with pytest.raises(vision_client.VisionError):
        vision_client.encode_image_data_url(b"")


def test_encode_image_data_url_rejects_non_image_mime():
    with pytest.raises(vision_client.VisionError):
        vision_client.encode_image_data_url(b"abc", mime="application/pdf")


def test_encode_image_file_reads_real_case_image():
    from app.config import settings

    image = settings.project_root / "data/images/c1_garment.jpeg"
    if not image.is_file():
        pytest.skip("案例照片不随公开仓库发布")
    url = vision_client.encode_image_file(image)
    assert url.startswith("data:image/jpeg;base64,")
    assert len(url) > 1000


def test_mime_from_name():
    assert vision_client.mime_from_name("a.JPG") == "image/jpeg"
    assert vision_client.mime_from_name("a.png") == "image/png"
    assert vision_client.mime_from_name(None) == "image/jpeg"