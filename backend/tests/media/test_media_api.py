"""C3 路由测试：真实 FastAPI 路由 + 假视觉服务 + 假生图 transport，**不做任何付费调用**。

覆盖：

- `GET  /api/sessions/{id}/prompts`：结构、与 C0 一致、确定性、404。
- `POST /api/sessions/{id}/reference`：multipart 上传、冻结策略、冲突、台账、文件落盘与 `/files` 访问、
  视觉服务未启动时 503 中文、图片不合规时 400 且不调模型。
- `POST /api/sessions/{id}/images`：`IMAGE_PROVIDER=none` 时 `url=None` 只记台账、不发请求；
  multipart 外部上传只核对；`openai_compatible`（假 transport）出图→核对→不符合时重画一次，
  两次图和核对都保留；配置不全/名字不对时 502。
- 线程隔离：视觉/生图在专用线程池里跑，慢请求不阻塞其他接口。
"""

from __future__ import annotations

import asyncio
import base64
import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient

from app.config import settings
from app.media import image_provider, prompts, vision_client
from app.routers import media
from app.routers import sessions as sessions_router
from app.schemas import (
    FIELD_ORDER,
    Mapping,
    Session,
    SpecField,
    SpecVersion,
    Utterance,
)
from app.store import load_session, save_session

PROJECT_ROOT = Path(__file__).resolve().parents[3]
CASES_PATH = PROJECT_ROOT / "data" / "cases.json"

PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
) + b"\x00" * 64  # 补足最小长度，PNG 头不变
UTTERANCE_TEXT = "脖子短，领子不能太高，想做一件交领外套"
SESSION_ID = "s1234567890ab"


# ------------------------------------------------------------ 数据


def load_case(case_id: str) -> dict:
    cases = json.loads(CASES_PATH.read_text(encoding="utf-8"))["cases"]
    return next(case for case in cases if case["id"] == case_id)


def spec_version(case_id: str = "c6", version: int = 1) -> SpecVersion:
    case = load_case(case_id)
    return SpecVersion(
        version=version,
        created_at="2026-11-18T09:00:00+08:00",
        reason="导入",
        fields={
            field: SpecField(
                value=prompts.field_text(case["spec"].get(field, "")),
                origin="已有方案",
            )
            for field in FIELD_ORDER
        },
    )


def make_session(versions: int = 1) -> Session:
    return Session(
        id=SESSION_ID,
        title="C3 路由测试",
        mode="审计已有方案",
        source="mock",
        created_at="2026-11-18T10:00:00+00:00",
        input={"text": UTTERANCE_TEXT, "existing_spec": None, "sample_id": "c6"},
        utterances=[
            Utterance(
                id="u1",
                text=UTTERANCE_TEXT,
                start=0,
                end=len(UTTERANCE_TEXT),
                type="身形",
                strength="硬约束",
                status="未落实",
                mappings=[Mapping(field="领型", status="未落实")],
            )
        ],
        spec_versions=[spec_version("c6", index + 1) for index in range(versions)],
    )


def c6_like_fields() -> dict:
    """案例六成衣图的机器结果形态：交领、无扣、暗花。"""
    return {
        "collar_family": {"value": "交领", "confidence": "确定"},
        "front_closure": {"value": "无扣开襟", "confidence": "疑似"},
        "buttons_visible": {"value": False, "confidence": "确定"},
        "length": {"value": "臀部附近", "confidence": "确定"},
        "silhouette": {"value": "H 型", "confidence": "确定"},
        "pattern": {"value": "暗花细密，低调", "confidence": "疑似"},
        "colors": {"value": ["藏青"], "confidence": "确定"},
    }


def c1_like_fields() -> dict:
    """案例一成衣图的机器结果形态：立领、无花纹。"""
    return {
        "collar_family": {"value": "立领", "confidence": "确定"},
        "front_closure": {"value": "交叠闭合", "confidence": "疑似"},
        "buttons_visible": {"value": False, "confidence": "疑似"},
        "length": {"value": "臀部附近", "confidence": "确定"},
        "silhouette": {"value": "H 型", "confidence": "确定"},
        "pattern": {"value": "无明显花纹", "confidence": "疑似"},
        "colors": {"value": ["黑"], "confidence": "确定"},
    }


# ------------------------------------------------------------ 夹具


@pytest.fixture
def api(tmp_path, monkeypatch):
    """临时存储 + 只挂出图与视觉路由的真实 FastAPI 应用。"""
    monkeypatch.setattr(settings, "storage_dir", tmp_path)
    monkeypatch.setattr(settings, "image_provider", "none")
    monkeypatch.setattr(settings, "image_base_url", "")
    monkeypatch.setattr(settings, "image_api_key", "")
    monkeypatch.setattr(settings, "image_model", "")

    (tmp_path / "files").mkdir(parents=True, exist_ok=True)
    session = make_session()
    save_session(session)

    app = FastAPI()
    app.include_router(media.router)
    # 真实的核心会话路由（只读引用）：用来验证并发下的“客户回答/人工修改”不被覆盖
    app.include_router(sessions_router.router)
    app.mount("/files", StaticFiles(directory=str(tmp_path / "files")), name="files")
    return SimpleNamespace(
        client=TestClient(app), app=app, session=session, tmp_path=tmp_path
    )


@pytest.fixture
def fake_vision(monkeypatch):
    """假视觉服务：按预设字段返回；可设定延迟或异常；记录收到的图片。"""

    class Vision:
        def __init__(self):
            self.calls: list[str] = []
            self.fields = c1_like_fields()
            self.sequence: list[dict] | None = None
            self.model = "test-vision"
            self.delay = 0.0
            self.error: Exception | None = None
            # 并发回归用：paused=True 时在视觉调用里停下，等测试放行
            self.paused = False
            self.entered = threading.Event()
            self.release = threading.Event()

        def __call__(self, image_data_url, **kwargs):
            self.calls.append(image_data_url)
            assert set(kwargs) <= {"vision_url", "timeout", "attempts"}
            if self.paused:
                self.entered.set()
                if not self.release.wait(10.0):
                    raise AssertionError("并发测试的门闩超时：没有等到放行信号")
            if self.delay:
                time.sleep(self.delay)
            if self.error is not None:
                raise self.error
            if self.sequence is not None:
                fields = self.sequence[min(len(self.calls) - 1, len(self.sequence) - 1)]
            else:
                fields = self.fields
            return vision_client.DeconstructResult(
                fields=fields, model=self.model, diagnostics={}
            )

    vision = Vision()
    monkeypatch.setattr(vision_client, "deconstruct", vision)
    return vision


@pytest.fixture
def no_network(monkeypatch):
    """把生图 transport 换成“一调就炸”，证明该分支没有联网。"""
    calls: list[tuple] = []

    def boom(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("不应该发起任何生图请求")

    monkeypatch.setattr(image_provider, "_post_json", boom)
    return calls


@pytest.fixture
def fake_image_transport(monkeypatch):
    """假生图 transport：返回 b64 图片，记录每次请求。"""

    class Transport:
        def __init__(self):
            self.calls: list[tuple] = []

        def __call__(self, url, headers, body, timeout):
            self.calls.append((url, dict(headers), dict(body), timeout))
            return {"data": [{"b64_json": base64.b64encode(PNG_BYTES).decode()}]}

    transport = Transport()
    monkeypatch.setattr(image_provider, "_post_json", transport)
    return transport


def configure_openai(monkeypatch):
    monkeypatch.setattr(settings, "image_provider", "openai_compatible")
    monkeypatch.setattr(settings, "image_base_url", "http://fake-image.local/v1")
    monkeypatch.setattr(settings, "image_api_key", "test-key-not-real")
    monkeypatch.setattr(settings, "image_model", "fake-image-model")


# ------------------------------------------------------------ 1. 提示词


def test_prompts_match_c0_and_are_deterministic(api):
    url = f"/api/sessions/{api.session.id}/prompts"
    first = api.client.get(url)
    assert first.status_code == 200
    body = first.json()
    assert set(body) == {"version", "items", "checks"}
    assert body["version"] == 1
    assert [item["kind"] for item in body["items"]] == list(prompts.KINDS)
    stored = load_session(api.session.id)
    assert body == prompts.build_prompts(stored.spec_versions[-1])

    second = api.client.get(url)
    assert second.json() == body  # 同一份方案永远同样的提示词


def test_prompts_unknown_session_returns_chinese_error(api):
    response = api.client.get("/api/sessions/s000000000dead/prompts")
    assert response.status_code == 404
    assert "找不到这个会话" in response.json()["error"]


# ------------------------------------------------------------ 2. 参考图


def test_reference_upload_freezes_fields_and_reports_conflict(api, fake_vision):
    response = api.client.post(
        f"/api/sessions/{api.session.id}/reference",
        files={"file": ("ref.png", PNG_BYTES, "image/png")},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    reference = body["reference"]

    # 采用的四项：把握上限“疑似”
    assert reference["fields"]["领型"] == {"value": "立领", "confidence": "疑似", "used": True}
    assert reference["fields"]["纽扣"]["used"] is True
    # 不采用的三项：used=false + 看不出
    for label in ("衣长", "廓形", "颜色"):
        assert reference["fields"][label] == {"value": "看不出", "confidence": "看不出", "used": False}
    assert reference["model"] == "test-vision"

    # 立领 + “脖子短，领子不能太高” → 领型冲突
    assert reference["conflicts"], "应报出一条领型冲突"
    conflict = reference["conflicts"][0]
    assert conflict["field"] == "领型"
    assert "立领" in conflict["message"]
    assert conflict["utterance_ids"] == ["u1"]

    # 台账
    entry = body["ledger"][-1]
    assert entry["action"] == "参考图解析"
    assert entry["version"] == 1
    assert "冲突" in entry["summary"]
    assert entry["changed_fields"] == ["领型"]

    # 文件真实落盘 + 通过 /files 访问
    url = reference["image_url"]
    assert url.startswith("/files/reference/")
    path = api.tmp_path / "files" / "reference" / url.rsplit("/", 1)[1]
    assert path.read_bytes() == PNG_BYTES
    served = api.client.get(url)
    assert served.status_code == 200 and served.content == PNG_BYTES

    # 会话已保存
    assert load_session(api.session.id).reference is not None


def test_reference_returns_503_when_vision_is_down(api, fake_vision):
    fake_vision.error = vision_client.VisionServiceUnavailable(vision_client.UNAVAILABLE_MESSAGE)
    response = api.client.post(
        f"/api/sessions/{api.session.id}/reference",
        files={"file": ("ref.png", PNG_BYTES, "image/png")},
    )
    assert response.status_code == 503
    assert "视觉服务未启动" in response.json()["error"]
    # 没有落盘、没有写会话
    assert not (api.tmp_path / "files" / "reference").exists()
    assert load_session(api.session.id).reference is None


def test_reference_rejects_non_image_without_calling_vision(api, fake_vision):
    response = api.client.post(
        f"/api/sessions/{api.session.id}/reference",
        files={"file": ("fake.png", b"this is definitely not an image" * 4, "image/png")},
    )
    assert response.status_code == 400
    assert "不是能识别的图片" in response.json()["error"]
    assert fake_vision.calls == []
    assert not (api.tmp_path / "files" / "reference").exists()


def test_reference_requires_multipart(api):
    response = api.client.post(f"/api/sessions/{api.session.id}/reference", json={"file": "x"})
    assert response.status_code == 400
    assert "multipart" in response.json()["error"]


# ------------------------------------------------------------ 3. 效果图：none（默认）


def test_images_none_provider_writes_prompt_only_ledger(api, fake_vision, no_network):
    response = api.client.post(
        f"/api/sessions/{api.session.id}/images", json={"kind": "成衣挂拍"}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    image = body["images"][-1]
    assert image["url"] is None
    assert image["provider"] == "none"
    assert image["check"] is None
    assert image["version"] == 1
    expected_prompt = prompts.build_prompt_items(
        load_session(api.session.id).spec_versions[-1]
    )[0]["text"]
    assert image["prompt"] == expected_prompt

    entry = body["ledger"][-1]
    assert entry["action"] == "出图"
    assert "仅提示词" in entry["summary"]
    assert "成衣挂拍" in entry["summary"]
    assert entry["changed_fields"] == []

    # 不发任何请求：既不调视觉、也不调生图
    assert fake_vision.calls == []
    assert no_network == []
    # 不产生图片文件
    assert not (api.tmp_path / "files" / "generated").exists()
    # 已保存
    stored = load_session(api.session.id)
    assert stored.images[-1].url is None and stored.images[-1].provider == "none"


def test_images_rejects_bad_kind(api):
    response = api.client.post(
        f"/api/sessions/{api.session.id}/images", json={"kind": "短视频"}
    )
    assert response.status_code == 400
    assert "图片类型只能是" in response.json()["error"]


def test_images_unknown_session(api):
    response = api.client.post("/api/sessions/s000000000dead/images", json={"kind": "成衣挂拍"})
    assert response.status_code == 404
    assert "找不到这个会话" in response.json()["error"]


def test_images_unknown_provider_returns_502(api, monkeypatch):
    monkeypatch.setattr(settings, "image_provider", "some-cloud")
    response = api.client.post(
        f"/api/sessions/{api.session.id}/images", json={"kind": "成衣挂拍"}
    )
    assert response.status_code == 502
    assert "IMAGE_PROVIDER 只能是 none 或 openai_compatible" in response.json()["error"]


# ------------------------------------------------------------ 3b. 外部图片上传只核对


def test_images_multipart_uploads_and_checks_only(api, fake_vision):
    response = api.client.post(
        f"/api/sessions/{api.session.id}/images",
        files={"file": ("outfit.png", PNG_BYTES, "image/png")},
        data={"kind": "成衣挂拍"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    image = body["images"][-1]
    assert image["provider"] == "upload"
    assert image["url"].startswith("/files/uploads/")
    assert image["prompt"] == ""
    # c1 形态的图（立领）对照 c6 方案（交领）→ 领型不符合
    verdicts = [(item["field"], item["verdict"]) for item in image["check"]["items"]]
    assert ("领型", "不符合") in verdicts
    assert ("长度比例", "看不出") in verdicts

    actions = [entry["action"] for entry in body["ledger"]]
    assert actions[-2:] == ["出图", "核对"]
    assert "外部上传" in body["ledger"][-2]["summary"]
    assert body["ledger"][-1]["changed_fields"] == ["领型", "装饰纹样"]

    served = api.client.get(image["url"])
    assert served.status_code == 200 and served.content == PNG_BYTES
    assert load_session(api.session.id).images[-1].check is not None


def test_images_multipart_requires_kind(api, fake_vision):
    response = api.client.post(
        f"/api/sessions/{api.session.id}/images",
        files={"file": ("outfit.png", PNG_BYTES, "image/png")},
    )
    assert response.status_code == 400
    assert "图片类型只能是" in response.json()["error"]
    assert fake_vision.calls == []


def test_images_multipart_503_when_vision_down(api, fake_vision):
    fake_vision.error = vision_client.VisionServiceUnavailable(vision_client.UNAVAILABLE_MESSAGE)
    response = api.client.post(
        f"/api/sessions/{api.session.id}/images",
        files={"file": ("outfit.png", PNG_BYTES, "image/png")},
        data={"kind": "成衣挂拍"},
    )
    assert response.status_code == 503
    assert "视觉服务未启动" in response.json()["error"]
    assert load_session(api.session.id).images == []


# ------------------------------------------------------------ 3c. openai_compatible（假 transport）


def test_images_generate_check_and_redraw_once(api, monkeypatch, fake_vision, fake_image_transport):
    configure_openai(monkeypatch)
    fake_vision.sequence = [c1_like_fields(), c6_like_fields()]

    response = api.client.post(
        f"/api/sessions/{api.session.id}/images", json={"kind": "成衣挂拍"}
    )
    assert response.status_code == 200, response.text
    body = response.json()

    images = body["images"]
    assert len(images) == 2, "不符合时应重画一次，两次的图都保留"
    assert len(fake_image_transport.calls) == 2
    assert len(fake_vision.calls) == 2

    first, second = images
    assert first["provider"] == "openai_compatible"
    assert first["url"] and first["url"].startswith("/files/generated/")
    assert second["url"] and second["url"].startswith("/files/generated/")
    assert first["url"] != second["url"]
    # 提示词逐字相同：参数锁定
    assert first["prompt"] == second["prompt"]
    assert fake_image_transport.calls[0][2]["prompt"] == fake_image_transport.calls[1][2]["prompt"]

    # 两次核对都留着
    assert first["check"] is not None and second["check"] is not None
    assert ("领型", "不符合") in [(i["field"], i["verdict"]) for i in first["check"]["items"]]
    assert ("领型", "符合") in [(i["field"], i["verdict"]) for i in second["check"]["items"]]

    summaries = [entry["summary"] for entry in body["ledger"]]
    assert any("已生成效果图" in s for s in summaries)
    assert any("自动重画 1 次" in s for s in summaries)
    assert [entry["action"] for entry in body["ledger"]].count("核对") == 2

    for image in images:
        path = api.tmp_path / "files" / "generated" / image["url"].rsplit("/", 1)[1]
        assert path.read_bytes() == PNG_BYTES
        assert api.client.get(image["url"]).status_code == 200

    stored = load_session(api.session.id)
    assert len(stored.images) == 2
    assert all(image.check is not None for image in stored.images)


def test_images_generate_no_redraw_when_all_match(api, monkeypatch, fake_vision, fake_image_transport):
    configure_openai(monkeypatch)
    fake_vision.sequence = [c6_like_fields()]

    response = api.client.post(
        f"/api/sessions/{api.session.id}/images", json={"kind": "面料细节"}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["images"]) == 1
    assert len(fake_image_transport.calls) == 1
    assert fake_image_transport.calls[0][2]["size"] == "1024x1024"
    assert not any("重画" in entry["summary"] for entry in body["ledger"])
    assert body["images"][0]["check"]["items"][0]["verdict"] == "符合"


def test_images_generate_incomplete_config_returns_502(api, monkeypatch, fake_vision):
    monkeypatch.setattr(settings, "image_provider", "openai_compatible")
    monkeypatch.setattr(settings, "image_base_url", "")
    response = api.client.post(
        f"/api/sessions/{api.session.id}/images", json={"kind": "成衣挂拍"}
    )
    assert response.status_code == 502
    assert "配置不完整" in response.json()["error"]
    assert fake_vision.calls == []
    assert load_session(api.session.id).images == []


def test_images_generate_provider_failure_returns_502(api, monkeypatch, fake_vision):
    configure_openai(monkeypatch)

    def refuse(*args, **kwargs):
        raise image_provider.ImageProviderError("生图接口返回错误（HTTP 402）：余额不足")

    monkeypatch.setattr(image_provider, "_post_json", refuse)
    response = api.client.post(
        f"/api/sessions/{api.session.id}/images", json={"kind": "成衣挂拍"}
    )
    assert response.status_code == 502
    assert "余额不足" in response.json()["error"]
    assert load_session(api.session.id).images == []


# ------------------------------------------------------------ 4. 线程隔离


def test_vision_and_provider_run_in_dedicated_executors():
    seen: dict[str, str] = {}

    def vision_work():
        seen["vision"] = threading.current_thread().name
        return "vision-ok"

    def image_work():
        seen["image"] = threading.current_thread().name
        return "image-ok"

    async def scenario():
        return (
            await media.run_vision(vision_work),
            await media.run_image_provider(image_work),
        )

    assert asyncio.run(scenario()) == ("vision-ok", "image-ok")
    assert seen["vision"].startswith("media-vision")
    assert seen["image"].startswith("media-image")
    assert seen["vision"] != threading.current_thread().name


def test_slow_vision_does_not_block_other_endpoints(api, fake_vision):
    """一张图跑 0.6 秒时，提示词接口应在 0.25 秒内返回。"""
    fake_vision.delay = 0.6

    async def scenario():
        transport = httpx.ASGITransport(app=api.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            slow = asyncio.create_task(
                client.post(
                    f"/api/sessions/{api.session.id}/reference",
                    files={"file": ("ref.png", PNG_BYTES, "image/png")},
                )
            )
            await asyncio.sleep(0.1)  # 等慢请求真的进了视觉线程池
            started = time.perf_counter()
            fast = await client.get(f"/api/sessions/{api.session.id}/prompts")
            elapsed = time.perf_counter() - started
            slow_response = await slow
        return fast, elapsed, slow_response

    fast, elapsed, slow_response = asyncio.run(scenario())
    assert fast.status_code == 200
    assert elapsed < 0.25, f"提示词接口被视觉调用拖慢了 {elapsed:.2f}s"
    assert slow_response.status_code == 200


# ------------------------------------------------------------ 5. 并发：慢调用不持锁、提交只追加


def test_slow_reference_keeps_concurrent_manual_patch(api, fake_vision):
    """视觉调用被挂起期间改方案：人工修改的新版本/台账必须保留，参考图指向当时原话。

    这条回归覆盖“慢调用结束后 save 整份旧 Session 冲掉别处修改”的问题：
    - 慢调用（视觉）全程不持会话锁，期间 PATCH 能立刻成功；
    - 提交时重读最新会话，只追加 reference + 一条台账；
    - 参考图不绑定方案版本，台账记在提交时的新版本上。
    """
    fake_vision.paused = True

    async def scenario():
        transport = httpx.ASGITransport(app=api.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            slow = asyncio.create_task(
                client.post(
                    f"/api/sessions/{api.session.id}/reference",
                    files={"file": ("ref.png", PNG_BYTES, "image/png")},
                )
            )
            entered = await asyncio.to_thread(fake_vision.entered.wait, 10.0)
            assert entered, "视觉调用没有进来，测试门闩没生效"
            # 视觉线程仍挂着：此时 PATCH 必须立刻成功（说明锁没被慢调用占住）
            patched = await client.patch(
                f"/api/sessions/{api.session.id}/spec",
                json={"field": "面料", "value": "并发修改-真丝提花", "reason": "并发回归"},
            )
            fake_vision.release.set()
            slow_response = await slow
        return patched, slow_response

    patched, slow_response = asyncio.run(scenario())
    assert patched.status_code == 200, patched.text
    assert patched.json()["spec_versions"][-1]["version"] == 2

    assert slow_response.status_code == 200, slow_response.text
    body = slow_response.json()

    # 新版方案与人工修改台账都在
    assert body["spec_versions"][-1]["version"] == 2
    assert body["spec_versions"][-1]["fields"]["面料"]["value"] == "并发修改-真丝提花"
    actions = [entry["action"] for entry in body["ledger"]]
    assert "人工修改" in actions and "参考图解析" in actions
    assert actions.index("人工修改") < actions.index("参考图解析")

    # 参考图结果与台账都在，台账按提交时的当前版本（2）记
    assert body["reference"] is not None
    assert body["reference"]["fields"]["领型"]["value"] == "立领"
    assert body["reference"]["conflicts"]
    reference_entry = body["ledger"][-1]
    assert reference_entry["action"] == "参考图解析"
    assert reference_entry["version"] == 2

    # 落盘的最新会话同样保留“人工修改 + 参考图”
    stored = load_session(api.session.id)
    assert stored.spec_versions[-1].version == 2
    assert stored.reference is not None
    assert [entry.action for entry in stored.ledger].count("人工修改") == 1


def test_slow_generation_keeps_concurrent_patch_and_image_version(
    api, monkeypatch, fake_vision, fake_image_transport
):
    """生图 + 核对挂起期间改方案：新版方案、人工修改台账都留，图片指向出图依据的旧版。"""
    configure_openai(monkeypatch)
    fake_vision.sequence = [c6_like_fields()]
    fake_vision.paused = True

    async def scenario():
        transport = httpx.ASGITransport(app=api.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            slow = asyncio.create_task(
                client.post(
                    f"/api/sessions/{api.session.id}/images", json={"kind": "成衣挂拍"}
                )
            )
            entered = await asyncio.to_thread(fake_vision.entered.wait, 10.0)
            assert entered, "核对调用没有进来，测试门闩没生效"
            patched = await client.patch(
                f"/api/sessions/{api.session.id}/spec",
                json={"field": "面料", "value": "并发修改-亚麻", "reason": "并发回归"},
            )
            fake_vision.release.set()
            slow_response = await slow
        return patched, slow_response

    patched, slow_response = asyncio.run(scenario())
    assert patched.status_code == 200, patched.text
    assert slow_response.status_code == 200, slow_response.text

    body = slow_response.json()
    assert body["spec_versions"][-1]["version"] == 2
    assert body["spec_versions"][-1]["fields"]["面料"]["value"] == "并发修改-亚麻"
    assert len(body["images"]) == 1
    image = body["images"][0]
    assert image["version"] == 1, "图片版本必须是出图所依据的旧版，不能跟着新版本走"
    assert image["check"] is not None

    actions = [entry["action"] for entry in body["ledger"]]
    assert actions.index("人工修改") < actions.index("出图")

    stored = load_session(api.session.id)
    assert stored.spec_versions[-1].version == 2
    assert len(stored.images) == 1 and stored.images[0].version == 1


def test_none_images_concurrent_requests_keep_both(api, no_network):
    """IMAGE_PROVIDER=none 时并发两次出图：编号唯一，两张图都写进会话，不互相覆盖。"""
    url = f"/api/sessions/{api.session.id}/images"

    async def scenario():
        transport = httpx.ASGITransport(app=api.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await asyncio.gather(
                client.post(url, json={"kind": "成衣挂拍"}),
                client.post(url, json={"kind": "面料细节"}),
            )

    responses = asyncio.run(scenario())
    for response in responses:
        assert response.status_code == 200, response.text

    stored = load_session(api.session.id)
    assert len(stored.images) == 2, "并发两次出图不能互相丢失"
    ids = [image.id for image in stored.images]
    assert len(set(ids)) == 2, f"图片编号必须唯一，实际是 {ids}"
    assert all(image.url is None and image.provider == "none" for image in stored.images)
    kinds = sorted(image.kind for image in stored.images)
    assert kinds == ["成衣挂拍", "面料细节"]
    assert sum(1 for entry in stored.ledger if entry.action == "出图") == 2
    assert no_network == [], "none 分支不允许任何生图请求"

def test_images_generate_keeps_image_when_vision_is_down(api, monkeypatch, fake_vision, fake_image_transport):
    """图已经付费生成：看图服务没开时保留这张图，不自动重画，台账写明请人工核对。"""
    configure_openai(monkeypatch)
    fake_vision.error = vision_client.VisionServiceUnavailable(vision_client.UNAVAILABLE_MESSAGE)
    response = api.client.post(f"/api/sessions/{api.session.id}/images", json={"kind": "成衣挂拍"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["images"]) == 1
    assert body["images"][0]["url"] and body["images"][0]["check"] is None
    assert len(fake_image_transport.calls) == 1
    assert "自动核对没做成" in body["ledger"][-1]["summary"]
