"""C3：视觉结果里的模型标识必须来自服务自己回报的来源，不编造型号。

`vision/scripts/predict_local.py` 的调试输出里每个字段都有 `source`，
例如 “FashionSigLIP zero-shot”“Qwen2.5-VL 7B local”“Qwen2.5-VL 3B local, 3-vote majority”。
约定要求核对与参考图结果的 `model` 照实记录，取不到就用服务地址兜底。
"""

from __future__ import annotations

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


def run(monkeypatch, diagnostics, *, vision_url="http://127.0.0.1:8001"):
    monkeypatch.setattr(
        vision_client,
        "_post_json",
        lambda url, body, timeout: {"fields": valid_fields(), "diagnostics": diagnostics},
    )
    return vision_client.deconstruct("data:image/jpeg;base64,AAAA", vision_url=vision_url)


def test_model_comes_from_field_sources_deduped(monkeypatch):
    diagnostics = {
        "meta": {"mode": "local_hybrid_zero_shot"},
        "fields": {
            "collar_family": {"value": "立领", "confidence": "疑似", "source": "FashionSigLIP zero-shot"},
            "front_closure": {"value": "交叠闭合", "confidence": "疑似", "source": "FashionSigLIP zero-shot"},
            "buttons_visible": {"value": False, "confidence": "疑似", "source": "Qwen2.5-VL 7B local"},
            "silhouette": {"source": "Qwen2.5-VL 3B local, 3-vote majority"},
        },
    }
    result = run(monkeypatch, diagnostics)
    assert result.model == (
        "FashionSigLIP zero-shot+Qwen2.5-VL 7B local+Qwen2.5-VL 3B local, 3-vote majority"
    )


def test_field_sources_take_priority_over_flat_model_key(monkeypatch):
    diagnostics = {
        "model": "某个笼统的名字",
        "fields": {"pattern": {"source": "Qwen2.5-VL 7B local"}},
    }
    assert run(monkeypatch, diagnostics).model == "Qwen2.5-VL 7B local"


def test_flat_model_key_is_used_when_no_sources(monkeypatch):
    assert run(monkeypatch, {"model": "qwen2.5vl:7b"}).model == "qwen2.5vl:7b"
    assert run(monkeypatch, {"models": ["a", "b"]}).model == "a+b"


def test_falls_back_to_service_address_without_any_model_info(monkeypatch):
    result = run(monkeypatch, {"meta": {"mode": "local"}}, vision_url="http://127.0.0.1:8001")
    assert result.model == "vision@http://127.0.0.1:8001"
    # 不编造任何型号
    assert "qwen" not in result.model.lower()


def test_no_diagnostics_still_falls_back(monkeypatch):
    monkeypatch.setattr(
        vision_client,
        "_post_json",
        lambda url, body, timeout: {"fields": valid_fields(), "local_only": True},
    )
    result = vision_client.deconstruct(
        "data:image/jpeg;base64,AAAA", vision_url="http://127.0.0.1:8123"
    )
    assert result.model == "vision@http://127.0.0.1:8123"


def test_malformed_diagnostics_is_ignored(monkeypatch):
    assert run(monkeypatch, {"fields": ["not", "a", "mapping"]}).model.startswith("vision@")
    assert run(monkeypatch, {"fields": {"x": {"source": ""}}}).model.startswith("vision@")


def test_sources_are_stripped_and_deduplicated(monkeypatch):
    diagnostics = {
        "fields": {
            "a": {"source": "  Qwen2.5-VL 7B local  "},
            "b": {"source": "Qwen2.5-VL 7B local"},
        }
    }
    assert run(monkeypatch, diagnostics).model == "Qwen2.5-VL 7B local"