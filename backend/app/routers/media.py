"""出图与视觉接口（C3）：提示词、参考图解析、效果图生成与核对。

覆盖约定第 6 节的三条接口，统一返回 `Session`（`/prompts` 返回约定的提示词结构）：

| 方法与路径 | 说明 |
|---|---|
| `GET  /api/sessions/{id}/prompts` | 当前方案版本的三条提示词 + 核对清单，纯程序拼接，确定性 |
| `POST /api/sessions/{id}/reference` | multipart（字段名 `file`）上传参考图，解析 + 冲突检测，写台账 |
| `POST /api/sessions/{id}/images` | JSON `{kind}` 按当前方案出图；或 multipart（`file` + `kind`）只核对 |

要点：

- 只复用核心模块：`schemas` / `store.load_session, save_session, now_iso, session_lock` /
  `config.settings`，**不改动后端核心文件**。
- 耗时的视觉推理与生图都放到专用线程池，不占用事件循环和 FastAPI 默认线程池，
  这样一张图跑几十秒时其他业务接口照常响应。
- **不再用旧的整份 Session 覆盖**：慢调用开始时只取一份只读快照（方案版本、原话），
  慢调用全程不持锁；提交时 `with session_lock(...)` 重读最新会话，只追加本次的
  `reference` / `images` / `ledger` 变化。同时发生的客户回答或人工修改不会丢。
  效果图的 `version` 保留“出图所依据的那一版”，即使期间方案又升了版。
- 视觉服务没起：`503` + 中文“视觉服务未启动…”；超时 `504`；结果不合格 `502`。
- `IMAGE_PROVIDER=none`（默认）：`url=None`，只记一笔“出图（仅提示词）”台账，**不发任何请求**。
- 上传外部图片：单独存到 `files/uploads/`，只做核对，并写“出图（上传）”+“核对”两条台账。
- 配置了生图接口时，核对出现“不符合”会自动重画**最多一次**，两次的图与核对结果都保留。
"""

from __future__ import annotations

import asyncio
import functools
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ..media import check, files, image_provider, prompts, reference, vision_client
from ..schemas import GeneratedImage, LedgerEntry, ReferenceAnalysis, Session
from ..store import load_session, now_iso, save_session, session_lock

router = APIRouter(prefix="/api/sessions", tags=["media"])

SESSION_NOT_FOUND = "找不到这个会话，可能已经被删除。"
NO_SPEC_MESSAGE = "这个会话还没有方案版本，无法生成提示词或效果图。"
UPLOAD_HINT = "请用 multipart/form-data 上传图片，字段名 file（效果图还要带 kind）。"
KIND_MESSAGE = "图片类型只能是“成衣挂拍”“面料细节”“上身效果”。"

# 专用线程池：视觉推理一次可能几十秒，不能霸占事件循环或默认线程池。
VISION_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="media-vision")
IMAGE_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="media-image")


class _Problem(Exception):
    """接口内部的中文错误：状态码 + 给业务员看的一句话。"""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def _error(status: int, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": message})


# ---------------------------------------------------------------- 线程池


async def run_vision(fn: Any, /, *args: Any, **kwargs: Any) -> Any:
    """在视觉线程池里跑同步的视觉调用（`deconstruct` / 核对）。"""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(VISION_EXECUTOR, functools.partial(fn, *args, **kwargs))


async def run_image_provider(fn: Any, /, *args: Any, **kwargs: Any) -> Any:
    """在生图线程池里跑同步的 `provider.generate()`。"""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(IMAGE_EXECUTOR, functools.partial(fn, *args, **kwargs))


# ---------------------------------------------------------------- 事务提交


def _commit(session_id: str, apply: Callable[[Session], None]) -> Session:
    """加会话锁 → 重读最新会话 → 只应用本次改动 → 保存。

    慢调用（视觉、生图）**不在这里**，调用前就把数据算好；本函数只做很短的
    读改写，因此不会在慢调用期间占锁，也不会把别处刚写进来的版本/台账冲掉。
    """
    with session_lock(session_id):
        try:
            latest = load_session(session_id)
        except FileNotFoundError:
            raise _Problem(404, SESSION_NOT_FOUND)
        apply(latest)
        save_session(latest)
        return latest


async def commit(session_id: str, apply: Callable[[Session], None]) -> Session:
    """把短暂的读改写放到工作线程里执行，避免持锁时卡住事件循环。"""
    return await asyncio.to_thread(_commit, session_id, apply)


# ---------------------------------------------------------------- 小工具


def _load(session_id: str) -> Session:
    """只读快照：慢调用开始时取一次，之后的版本变化由提交阶段合并。"""
    try:
        return load_session(session_id)
    except FileNotFoundError:
        raise _Problem(404, SESSION_NOT_FOUND)


def _current_version(session: Session) -> Any:
    versions = list(getattr(session, "spec_versions", None) or [])
    if not versions:
        raise _Problem(400, NO_SPEC_MESSAGE)
    return versions[-1]


def _latest_version_number(session: Session) -> int:
    """会话当前（提交时）的版本号；台账按“动作发生时的当前版本”记。"""
    versions = list(getattr(session, "spec_versions", None) or [])
    return versions[-1].version if versions else 1


def _vision_status(exc: vision_client.VisionError) -> tuple[int, str]:
    """视觉异常 → (HTTP 状态码, 中文说明)。"""
    if isinstance(exc, vision_client.VisionServiceUnavailable):
        return 503, str(exc)
    if isinstance(exc, vision_client.VisionTimeout):
        return 504, str(exc)
    return 502, str(exc)


def _next_image_id(session: Session) -> str:
    """在会话锁内调用，保证并发两次出图也各自拿到唯一编号。"""
    used = {image.id for image in session.images}
    index = len(session.images) + 1
    while f"img{index}" in used:
        index += 1
    return f"img{index}"


def _kind_of(value: Any) -> str:
    if isinstance(value, str) and value.strip() in prompts.KINDS:
        return value.strip()
    raise _Problem(400, KIND_MESSAGE)


def _prompt_text(spec_version: Any, kind: str) -> str:
    for item in prompts.build_prompt_items(spec_version):
        if item["kind"] == kind:
            return item["text"]
    raise _Problem(400, KIND_MESSAGE)


def _image_ledger(
    *,
    version: int,
    summary: str,
    detail: str,
    utterance_ids: list[str] | None = None,
    changed_fields: list[str] | None = None,
) -> LedgerEntry:
    """一条“出图”台账（约定 `LedgerEntry`）。"""
    return LedgerEntry(
        version=version,
        at=now_iso(),
        action="出图",
        summary=summary,
        detail=detail,
        utterance_ids=list(utterance_ids or []),
        changed_fields=list(changed_fields or []),
    )


def _record(apply_session: Session, image: GeneratedImage, payload: dict, *, note: str) -> None:
    """写入核对结果与“核对”台账；内部错误转成中文 400。"""
    try:
        check.record_check(apply_session, image, payload, note=note)
    except check.CheckError as exc:
        raise _Problem(400, str(exc))


async def _read_upload(request: Request) -> tuple[Any, Any]:
    """取 multipart 的 `file`（和整张表单）。不是 multipart 或没带文件都报中文 400。"""
    content_type = (request.headers.get("content-type") or "").lower()
    if not content_type.startswith("multipart/form-data"):
        raise _Problem(400, UPLOAD_HINT)
    try:
        form = await request.form()
    except Exception:  # 表单解析失败同样是“上传格式不对”
        raise _Problem(400, UPLOAD_HINT)
    upload = form.get("file")
    if upload is None or isinstance(upload, str):
        raise _Problem(400, "没有收到图片文件，请用字段名 file 上传一张图片。")
    return upload, form


async def _read_json_body(request: Request) -> dict:
    content_type = (request.headers.get("content-type") or "").lower()
    if "json" not in content_type:
        raise _Problem(
            400,
            "请求体要么是 JSON {\"kind\": \"成衣挂拍\"}，要么用 multipart 上传图片（file + kind）。",
        )
    try:
        body = await request.json()
    except Exception:
        raise _Problem(400, "请求体不是合法的 JSON。")
    if not isinstance(body, dict):
        raise _Problem(400, "请求体应该是一个 JSON 对象，例如 {\"kind\": \"成衣挂拍\"}。")
    return body


def _data_url(data: bytes, mime: str) -> str:
    try:
        return vision_client.encode_image_data_url(data, mime)
    except vision_client.VisionError as exc:
        raise _Problem(400, str(exc))


async def _checked_payload(snapshot: Session, data: bytes, mime: str, version: int) -> dict:
    """只把图片交给视觉服务，由程序比对（核对口径见 `media/check.py`）。"""
    data_url = _data_url(data, mime)
    try:
        return await run_vision(check.check_generated_image, snapshot, data_url, version=version)
    except vision_client.VisionError as exc:
        status, message = _vision_status(exc)
        raise _Problem(status, message)
    except check.CheckError as exc:
        raise _Problem(400, str(exc))


async def _checked_asset(snapshot: Session, asset: Any, version: int) -> dict:
    """核对一张由生图接口产出的图（本机 `/files` 读回，不信任任何外部地址）。"""
    try:
        data, mime, _ = files.read_image_url(asset.url or "")
    except files.MediaFileError as exc:
        raise _Problem(400, str(exc))
    return await _checked_payload(snapshot, data, mime, version)


# ---------------------------------------------------------------- 1. 提示词


@router.get("/{session_id}/prompts")
def get_prompts(session_id: str) -> Any:
    """当前方案版本的三条提示词 + 核对清单。同一份方案永远得到同样的文字。"""
    try:
        session = _load(session_id)
        spec_version = _current_version(session)
        return prompts.build_prompts(spec_version)
    except _Problem as exc:
        return _error(exc.status, exc.message)


# ---------------------------------------------------------------- 2. 参考图


@router.post("/{session_id}/reference", response_model=Session)
async def upload_reference(session_id: str, request: Request) -> Any:
    """上传参考图：视觉解构 → 冻结策略 → 与原话比对冲突 → 写 `reference` 和台账。"""
    try:
        snapshot = _load(session_id)
        upload, _ = await _read_upload(request)
        data = await upload.read()
        try:
            mime, suffix = files.validate_image_bytes(data)
        except files.MediaFileError as exc:
            raise _Problem(400, str(exc))

        name = files.new_name(suffix)
        image_url = files.url_for(files.REFERENCE_DIR, name)
        data_url = _data_url(data, mime)

        # ---- 慢调用（不持锁）：只看图，冲突比对是纯程序规则
        try:
            result = await run_vision(vision_client.deconstruct, data_url)
        except vision_client.VisionError as exc:
            status, message = _vision_status(exc)
            raise _Problem(status, message)
        analysis = reference.build_reference_analysis(
            image_url=image_url,
            result=result,
            utterances=list(snapshot.utterances),
        )

        files.write_bytes(data, files.REFERENCE_DIR, name)

        # ---- 提交：只追加本次的 reference + 一条台账
        def apply(latest: Session) -> None:
            latest.reference = ReferenceAnalysis.model_validate(analysis)
            latest.ledger.append(
                LedgerEntry.model_validate(
                    reference.build_ledger_entry(
                        analysis, version=_latest_version_number(latest), at=now_iso()
                    )
                )
            )

        return await commit(session_id, apply)
    except _Problem as exc:
        return _error(exc.status, exc.message)


# ---------------------------------------------------------------- 3. 效果图


@router.post("/{session_id}/images", response_model=Session)
async def create_image(session_id: str, request: Request) -> Any:
    """JSON `{kind}` 出图并核对；或多部分上传一张外部图片，只核对。"""
    try:
        snapshot = _load(session_id)
        content_type = (request.headers.get("content-type") or "").lower()
        if content_type.startswith("multipart/form-data"):
            upload, form = await _read_upload(request)
            kind = _kind_of(form.get("kind"))
            return await _upload_external(session_id, snapshot, kind, upload)
        body = await _read_json_body(request)
        kind = _kind_of(body.get("kind"))
        return await _generate(session_id, snapshot, kind)
    except _Problem as exc:
        return _error(exc.status, exc.message)


async def _upload_external(session_id: str, snapshot: Session, kind: str, upload: Any) -> Session:
    """外部图片：存到 `files/uploads/`，只做核对，不改方案版本。"""
    spec_version = _current_version(snapshot)
    data = await upload.read()
    try:
        mime, _ = files.validate_image_bytes(data)
    except files.MediaFileError as exc:
        raise _Problem(400, str(exc))

    payload = await _checked_payload(snapshot, data, mime, spec_version.version)

    try:
        url, _, _ = files.save_image_bytes(data, files.UPLOAD_DIR)
    except files.MediaFileError as exc:
        raise _Problem(400, str(exc))

    def apply(latest: Session) -> None:
        version = _latest_version_number(latest)
        image = GeneratedImage(
            id=_next_image_id(latest),
            version=spec_version.version,
            kind=kind,
            prompt="",
            url=url,
            provider=image_provider.UPLOAD_PROVIDER,
            check=None,
        )
        latest.images.append(image)
        latest.ledger.append(
            _image_ledger(
                version=version,
                summary=f"【出图（外部上传）】{kind}：业务员上传的外部图片，未调用生图接口，只做核对",
                detail=(
                    "这张图不是由本系统生成的，提示词没有用于出图；"
                    f"图片地址：{url}；核对依据第 {spec_version.version} 版方案。"
                ),
            )
        )
        _record(latest, image, payload, note="外部上传图片，只做核对")

    return await commit(session_id, apply)


async def _generate(session_id: str, snapshot: Session, kind: str) -> Session:
    """按当前方案出图；`none` 时只给提示词，配置了生图接口时再核对、必要时重画一次。"""
    spec_version = _current_version(snapshot)
    prompt_text = _prompt_text(spec_version, kind)

    try:
        provider = image_provider.get_provider()
    except image_provider.ImageProviderError as exc:
        raise _Problem(502, str(exc))

    if provider.name == image_provider.PROVIDER_NONE:
        # 纯本地，不发任何请求；整段很短，直接在会话锁里完成。
        asset = provider.generate(prompt_text, kind)
        if asset.url is not None:  # none 实现永远不带图，防御性检查
            raise _Problem(502, "生图接口配置与实际行为不一致，请检查 IMAGE_PROVIDER。")

        def apply_none(latest: Session) -> None:
            image = GeneratedImage(
                id=_next_image_id(latest),
                version=spec_version.version,
                kind=kind,
                prompt=prompt_text,
                url=None,
                provider=asset.provider,
                check=None,
            )
            latest.images.append(image)
            latest.ledger.append(
                _image_ledger(
                    version=_latest_version_number(latest),
                    summary=f"【出图（仅提示词）】{kind}：没有配置生图接口，只生成提示词，没有出图",
                    detail=f"{asset.note}\n提示词：\n{prompt_text}",
                )
            )

        return await commit(session_id, apply_none)

    # ---- 真正的生图接口：生成与核对都在锁外完成
    try:
        asset = await run_image_provider(provider.generate, prompt_text, kind)
    except image_provider.ImageProviderError as exc:
        raise _Problem(502, str(exc))

    # 图已经付费生成：看图服务没开或超时时保留这张图，只是不做自动核对，交给人看。
    check_problem: str | None = None
    try:
        payload = await _checked_asset(snapshot, asset, spec_version.version)
    except _Problem as exc:
        if exc.status not in (502, 503, 504):
            raise
        payload = None
        check_problem = exc.message

    redraw: dict[str, Any] = {"requested": payload is not None and check.needs_redraw(payload)}
    if redraw["requested"]:
        redraw.update(await _redraw_once(snapshot, provider, prompt_text, kind, spec_version.version))

    def apply(latest: Session) -> None:
        version = _latest_version_number(latest)
        first = GeneratedImage(
            id=_next_image_id(latest),
            version=spec_version.version,
            kind=kind,
            prompt=prompt_text,
            url=asset.url,
            provider=asset.provider,
            check=None,
        )
        latest.images.append(first)
        latest.ledger.append(
            _image_ledger(
                version=version,
                summary=f"【出图】{kind}：已生成效果图（{asset.provider}）",
                detail=f"{asset.note}\n图片地址：{asset.url}\n提示词：\n{prompt_text}",
            )
        )
        if payload is None:
            latest.ledger.append(
                _image_ledger(
                    version=version,
                    summary=f"【效果图核对】{kind}：自动核对没做成，请人工核对",
                    detail=f"图已保留，看图服务这次没有完成核对：{check_problem}",
                )
            )
            return
        _record(latest, first, payload, note=f"{kind} 第 1 次出图")

        if not redraw["requested"]:
            return
        second_asset = redraw.get("asset")
        if second_asset is None:
            latest.ledger.append(
                _image_ledger(
                    version=version,
                    summary=f"【出图】{kind}：核对有不符合项，自动重画 1 次失败",
                    detail=f"{redraw.get('error', '')}\n提示词与第一次相同，未改动方案。",
                )
            )
            return

        second = GeneratedImage(
            id=_next_image_id(latest),
            version=spec_version.version,
            kind=kind,
            prompt=prompt_text,
            url=second_asset.url,
            provider=second_asset.provider,
            check=None,
        )
        latest.images.append(second)
        latest.ledger.append(
            _image_ledger(
                version=version,
                summary=f"【出图】{kind}：核对有不符合项，自动重画 1 次（{second_asset.provider}）",
                detail=(
                    f"{second_asset.note}\n图片地址：{second_asset.url}\n"
                    "提示词与第一次逐字相同（参数锁定），只重画一次；两次的图和核对都保留。"
                ),
            )
        )
        second_payload = redraw.get("payload")
        if second_payload is None:
            latest.ledger.append(
                _image_ledger(
                    version=version,
                    summary=f"【出图】{kind}：重画后的核对失败",
                    detail=f"{redraw.get('check_error', '')}\n第一张图与它的核对结果照常保留。",
                )
            )
            return
        _record(latest, second, second_payload, note=f"{kind} 自动重画 1 次后的核对")

    return await commit(session_id, apply)


async def _redraw_once(
    snapshot: Session,
    provider: Any,
    prompt_text: str,
    kind: str,
    version: int,
) -> dict[str, Any]:
    """核对有“不符合”时再画一次（最多一次）。

    提示词逐字不变：参数锁定优先，靠重画换一次随机种子，不改方案文字。
    重画本身失败、或重画后的核对失败，都不推翻已经拿到的第一张图和它的核对结果。
    """
    try:
        asset = await run_image_provider(provider.generate, prompt_text, kind)
    except image_provider.ImageProviderError as exc:
        return {"error": str(exc)}

    try:
        payload = await _checked_asset(snapshot, asset, version)
    except _Problem as exc:
        return {"asset": asset, "check_error": exc.message}
    return {"asset": asset, "payload": payload}


__all__ = [
    "router", "VISION_EXECUTOR", "IMAGE_EXECUTOR",
    "run_vision", "run_image_provider", "commit",
    "get_prompts", "upload_reference", "create_image",
]