"""留出案例在服务端被拦住：列表不返回，建会话直接 400，且不调大模型。

全部用合成案例，不读取也不打印真实留出案例的内容。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import knowledge
from app.config import settings
from app.pipeline import real

BLOCKED_MESSAGE = "这个案例是留出评测集，不开放演示。"
NORMAL_TEXT = "想要定制一件外套"
HOLDOUT_TEXT = "这段虚构口述只属于留出案例，不应出现在任何接口返回里。"

SYNTHETIC_CASES = [
    {
        "id": "hx-dev",
        "title": "合成普通案例",
        "split": "dev",
        "customer_text": NORMAL_TEXT,
        "spec": {"品类": "外套"},
        "utterances": [
            {
                "text": NORMAL_TEXT,
                "type": "品类",
                "strength": "描述",
                "mappings": [
                    {"field": "品类", "status": "已落实", "note": "方案品类是外套"},
                ],
            }
        ],
    },
    {
        "id": "hx",
        "title": "合成留出案例",
        "split": "holdout",
        "customer_text": HOLDOUT_TEXT,
        "spec": {"品类": "外套"},
        "utterances": [],
    },
]


@pytest.fixture()
def synthetic_cases(monkeypatch):
    monkeypatch.setattr(knowledge, "get_cases", lambda: SYNTHETIC_CASES)
    # 示例列表与建会话都走 get_cases；清掉按案例 id 派生的缓存，避免串到真实数据。
    knowledge.dev_case_ids.cache_clear()
    yield
    knowledge.dev_case_ids.cache_clear()


def test_samples_hide_holdout_case(client: TestClient, synthetic_cases) -> None:
    response = client.get("/api/samples")
    assert response.status_code == 200
    samples = response.json()

    assert [item["id"] for item in samples] == ["hx-dev"]
    assert "hx" not in {item["id"] for item in samples}
    assert HOLDOUT_TEXT not in response.text
    assert all(item["holdout"] is False for item in samples)


def test_holdout_session_is_400_in_mock_mode(client: TestClient, synthetic_cases) -> None:
    response = client.post("/api/sessions", json={"sample_id": "hx"})
    assert response.status_code == 400
    assert response.json()["error"] == BLOCKED_MESSAGE


def test_holdout_session_is_400_and_never_calls_llm(
    client: TestClient, synthetic_cases, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "mock_mode", False)
    calls = {"get_client": 0, "extract_utterances": 0}

    def fake_get_client():
        calls["get_client"] += 1
        raise AssertionError("留出案例不应创建大模型客户端")

    def fake_extract_utterances(*args, **kwargs):
        calls["extract_utterances"] += 1
        raise AssertionError("留出案例不应调用大模型拆句")

    monkeypatch.setattr(real, "get_client", fake_get_client)
    monkeypatch.setattr(real, "extract_utterances", fake_extract_utterances)

    response = client.post("/api/sessions", json={"sample_id": "hx"})
    assert response.status_code == 400
    assert response.json()["error"] == BLOCKED_MESSAGE
    assert calls == {"get_client": 0, "extract_utterances": 0}


def test_normal_case_still_creates_session(client: TestClient, synthetic_cases) -> None:
    response = client.post("/api/sessions", json={"sample_id": "hx-dev"})
    assert response.status_code == 200, response.text
    session = response.json()
    assert session["input"]["sample_id"] == "hx-dev"
    assert session["input"]["text"] == NORMAL_TEXT
