"""M3 稳健性自检：大模型严格校验、错误口径统一、并发改会话不丢数据。

不联网、不调付费服务、不跑视觉和生图，只用开发集案例。
"""

from __future__ import annotations

import json
import logging
import threading
import time

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app import llm
from app.config import settings
from app.llm import LLMClient
from app.llm_models import ExtractionResult
from app.main import app
from app.pipeline.extract import build_user_prompt
from app.store import load_session, session_lock

FIELDS = ("品类", "版型结构", "领型", "面料", "色彩", "装饰纹样", "长度比例", "松弛余量")
STATUS_ORDER = ("有冲突", "被改写", "未落实", "需设计师判断", "间接体现", "已落实")


# ---------------------------------------------------------------- 假传输


class FakeTransport:
    """假传输：按顺序返回响应内容，不碰网络。"""

    def __init__(self, responses: list[object]) -> None:
        self.responses = list(responses)
        self.payloads: list[dict] = []

    def __call__(self, url, headers, payload, timeout) -> str:
        self.payloads.append(payload)
        if not self.responses:
            raise AssertionError("模型被调用的次数超出了预期")
        item = self.responses.pop(0)
        content = item if isinstance(item, str) else json.dumps(item, ensure_ascii=False)
        return json.dumps(
            {"choices": [{"message": {"role": "assistant", "content": content}}]},
            ensure_ascii=False,
        )


def install(responses: list[object]) -> FakeTransport:
    transport = FakeTransport(responses)
    llm.set_client(
        LLMClient(
            base_url="http://model.invalid/v1",
            api_key="test-key",
            model="fake-model",
            timeout=5.0,
            transport=transport,
        )
    )
    return transport


@pytest.fixture()
def real_mode(monkeypatch):
    monkeypatch.setattr(settings, "mock_mode", False)
    yield
    llm.reset_client()


# ---------------------------------------------------------------- 大模型严格性


def _audit_output(rule_id: str | None = None) -> dict:
    return {
        "utterances": [
            {
                "text": "想要定制一件外套",
                "type": "描述",
                "strength": "描述",
                "mappings": [
                    {
                        "field": "品类",
                        "status": "已落实",
                        "note": "方案品类是外套",
                        "rule_id": rule_id,
                    }
                ],
            }
        ]
    }


def test_extraction_result_rejects_invented_rule_id() -> None:
    """编造的规则编号算不合格，不能被悄悄清成 null。"""
    with pytest.raises(ValidationError) as info:
        ExtractionResult.model_validate(_audit_output("R9"))
    assert "rule_id" in str(info.value)


def test_invented_rule_id_retries_once_then_succeeds(client, real_mode) -> None:
    transport = install([_audit_output("R9"), _audit_output("R2")])
    response = client.post("/api/sessions", json={"sample_id": "c2"})
    assert response.status_code == 200, response.text
    assert len(transport.payloads) == 2
    retry_prompt = transport.payloads[1]["messages"][-1]["content"]
    assert "不合格" in retry_prompt
    assert "rule_id" in retry_prompt
    session = response.json()
    assert session["utterances"][0]["mappings"][0]["rule_id"] == "R2"


def test_invented_rule_id_twice_returns_chinese_error(client, real_mode) -> None:
    transport = install([_audit_output("R9"), _audit_output("R9")])
    response = client.post("/api/sessions", json={"sample_id": "c2"})
    assert response.status_code == 502
    message = response.json()["error"]
    assert "大模型" in message and "重试" in message
    assert "rule_id" in message
    # 不把异常类名、堆栈、密钥丢给业务员
    for leak in ("ValidationError", "Traceback", "test-key", "pydantic"):
        assert leak not in message
    assert len(transport.payloads) == 2


# ---------------------------------------------------------------- 提示词口径


def test_extract_prompt_status_order_matches_vocabulary() -> None:
    """拆句提示词里的状态顺序必须是严重程度从高到低，不能颠倒。"""
    prompt = build_user_prompt("想要一件外套", {field: "" for field in FIELDS})
    assert "严重程度从高到低" in prompt

    positions = [prompt.index(name) for name in STATUS_ORDER]
    assert positions == sorted(positions), "状态顺序与词表的严重程度顺序不一致"
    assert prompt.index("严重程度从高到低") < positions[0]
    # 已与客户确认是系统追加的状态，不能让模型输出
    assert "已与客户确认" not in prompt


# ---------------------------------------------------------------- 未处理异常


def test_unhandled_exception_returns_chinese_json_500(monkeypatch, case2: dict) -> None:
    import app.routers.sessions as sessions_router

    def boom(_session):
        raise RuntimeError("api_key=sk-secret-123 内部错误 堆栈")

    monkeypatch.setattr(sessions_router, "build_handoff", boom)
    broken = TestClient(app, raise_server_exceptions=False)
    response = broken.get(f"/api/sessions/{case2['id']}/handoff")

    assert response.status_code == 500
    body = response.json()
    assert set(body) == {"error"}
    message = body["error"]
    assert "服务器" in message
    for leak in ("sk-secret-123", "api_key", "Traceback", "RuntimeError", "内部错误"):
        assert leak not in message


def test_unhandled_exception_log_leaks_no_secret(monkeypatch, caplog, case2: dict) -> None:
    """兜底 500 的服务端日志也不能带异常原文和堆栈，否则 api_key 会随日志外泄。"""
    import app.routers.sessions as sessions_router

    secret = "sk-synthetic-secret-987"

    def boom(_session):
        raise RuntimeError(f"下游连接失败 api_key={secret}")

    monkeypatch.setattr(sessions_router, "build_handoff", boom)
    broken = TestClient(app, raise_server_exceptions=False)
    with caplog.at_level(logging.ERROR, logger="yinuo"):
        response = broken.get(f"/api/sessions/{case2['id']}/handoff")

    # 客户端拿到的仍是中文 JSON 500，且不含任何敏感内容
    assert response.status_code == 500
    assert set(response.json()) == {"error"}
    assert secret not in response.text
    assert "api_key" not in response.text

    # 服务端日志：不应出现密钥原文、异常原文、堆栈
    assert caplog.records, "应当留下一条可用于排查的服务端日志"
    text = caplog.text + "\n".join(record.getMessage() for record in caplog.records)
    assert secret not in text
    assert "api_key" not in text
    assert "Traceback" not in text
    assert "下游连接失败" not in text
    for record in caplog.records:
        assert record.exc_info is None, "日志不能带 exc_info，否则堆栈和异常原文会进日志"

    # 但仍要能诊断：方法、路由、异常类型都在
    assert "GET" in text
    assert f"/api/sessions/{case2['id']}/handoff" in text
    assert "RuntimeError" in text


def test_invalid_json_body_returns_chinese_error(client: TestClient, case2: dict) -> None:
    response = client.post(
        f"/api/sessions/{case2['id']}/answers",
        content="{不是 JSON",
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 422
    assert "error" in response.json()


# ---------------------------------------------------------------- 会话锁


def test_session_lock_is_reentrant(case2: dict) -> None:
    """同一线程可以重复进入同一会话的锁（接口里加锁，内部再取一次不会死锁）。"""
    with session_lock(case2["id"]):
        with session_lock(case2["id"]):
            session = load_session(case2["id"])
    assert session.id == case2["id"]


def test_session_lock_blocks_other_thread(case2: dict) -> None:
    sid = case2["id"]
    holder_has_lock = threading.Event()
    release = threading.Event()
    order: list[str] = []

    def holder() -> None:
        with session_lock(sid):
            holder_has_lock.set()
            release.wait(5)
            order.append("holder")

    def contender() -> None:
        with session_lock(sid):
            order.append("contender")

    first = threading.Thread(target=holder)
    first.start()
    assert holder_has_lock.wait(5)
    second = threading.Thread(target=contender)
    second.start()
    time.sleep(0.1)
    assert order == [], "持锁期间其他线程不能进入"
    release.set()
    first.join(5)
    second.join(5)
    assert order == ["holder", "contender"]


def _changing_option(session: dict, question: dict) -> dict:
    current = session["spec_versions"][-1]["fields"]
    return next(
        option
        for option in question["options"]
        if any(option["patch"].get(f) != current[f]["value"] for f in option["patch"])
    )


def _post_answer(session_id: str, question_id: str, option_id: str) -> int:
    worker = TestClient(app)
    response = worker.post(
        f"/api/sessions/{session_id}/answers",
        json={"question_id": question_id, "option_id": option_id},
    )
    return response.status_code


def test_concurrent_same_answer_versions_once(client: TestClient, case2: dict) -> None:
    """同一问题被并发重复提交：只升一个版本、只记一条台账。"""
    question = case2["questions"][0]
    option = _changing_option(case2, question)
    start_version = case2["spec_versions"][-1]["version"]
    start_ledger = len(case2["ledger"])

    barrier = threading.Barrier(6)
    codes: list[int] = []
    codes_lock = threading.Lock()

    def worker() -> None:
        barrier.wait()
        code = _post_answer(case2["id"], question["id"], option["id"])
        with codes_lock:
            codes.append(code)

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)

    assert codes and set(codes) == {200}
    final = client.get(f"/api/sessions/{case2['id']}").json()
    assert final["spec_versions"][-1]["version"] == start_version + 1
    assert len(final["ledger"]) == start_ledger + 1
    assert final["questions"][0]["answer"] == option["id"]


def test_concurrent_different_answers_lose_nothing(client: TestClient, case2: dict) -> None:
    """两个不同问题同时回答：两个改动都在，版本和台账各加一次。"""
    questions = case2["questions"][:2]
    picks = [(question, _changing_option(case2, question)) for question in questions]
    start_version = case2["spec_versions"][-1]["version"]
    start_ledger = len(case2["ledger"])

    barrier = threading.Barrier(4)
    codes: list[int] = []
    codes_lock = threading.Lock()

    def worker(question: dict, option: dict) -> None:
        barrier.wait()
        code = _post_answer(case2["id"], question["id"], option["id"])
        with codes_lock:
            codes.append(code)

    threads = [
        threading.Thread(target=worker, args=pick)
        for pick in picks
        for _ in range(2)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)

    assert codes and set(codes) == {200}
    final = client.get(f"/api/sessions/{case2['id']}").json()
    assert final["spec_versions"][-1]["version"] == start_version + 2
    assert len(final["ledger"]) == start_ledger + 2

    answered = {q["id"]: q["answer"] for q in final["questions"]}
    for question, option in picks:
        field = question["field"]
        assert answered[question["id"]] == option["id"]
        assert final["spec_versions"][-1]["fields"][field]["value"] == option["patch"][field]