import importlib

from fastapi.testclient import TestClient


def test_gateway_auth_origin_and_quota(monkeypatch):
    monkeypatch.setenv("YINUO_TEST_USER", "tester")
    monkeypatch.setenv("YINUO_TEST_PASSWORD", "test-password-1234567890")
    monkeypatch.setenv("YINUO_TEST_REQUEST_QUOTA", "1")
    import test_gateway
    gateway = importlib.reload(test_gateway)

    class FakeResponse:
        status_code = 200
        content = b'{"ok":true}'
        headers = {"content-type": "application/json"}

    class FakeClient:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def request(self, *args, **kwargs): return FakeResponse()

    monkeypatch.setattr(gateway.httpx, "AsyncClient", FakeClient)
    with TestClient(gateway.app) as client:
        auth = ("tester", "test-password-1234567890")
        assert client.get("/api/health").status_code == 401
        assert client.get("/api/health", auth=auth).json() == {"ok": True}
        assert client.post("/api/sessions", auth=auth, json={}).status_code == 403
        assert client.post("/api/sessions", auth=auth, json={}, headers={"origin": "https://evil.example"}).status_code == 403
        assert client.post("/api/sessions", auth=auth, json={}, headers={"origin": "http://testserver"}).status_code == 200
        assert client.post("/api/sessions", auth=auth, json={}, headers={"origin": "http://testserver"}).status_code == 429
