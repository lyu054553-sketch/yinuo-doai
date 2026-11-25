"""测试环境：MOCK_MODE=1，会话存到临时目录；不接触案例五，不调外部模型。"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

_TEST_STORAGE = tempfile.mkdtemp(prefix="yinuo-storage-")
os.environ["MOCK_MODE"] = "1"
os.environ["STORAGE_DIR"] = _TEST_STORAGE
os.environ["VISION_URL"] = "http://127.0.0.1:9"  # 确保健康检查不等待

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


@pytest.fixture(scope="session")
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture()
def case2(client: TestClient) -> dict:
    response = client.post("/api/sessions", json={"sample_id": "c2"})
    assert response.status_code == 200, response.text
    return response.json()

def _clear_resource_caches() -> None:
    from app import knowledge

    for fn in (knowledge.get_sim_doc, knowledge.get_resources, knowledge.get_attrs, knowledge._resource_index):
        fn.cache_clear()


@pytest.fixture()
def no_sim(monkeypatch):
    """只用上服案例资源（关掉模拟资源库），用来测“资源库里没有”时的兜底行为。"""
    monkeypatch.setenv("YINUO_SIM_LIBRARY", "0")
    _clear_resource_caches()
    yield
    monkeypatch.delenv("YINUO_SIM_LIBRARY", raising=False)
    _clear_resource_caches()
