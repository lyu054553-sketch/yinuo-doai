"""会话相关接口：创建、列表、详情、回答客户问题、人工改栏、交接单。"""

from __future__ import annotations

import json
import os
import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import knowledge
from ..config import settings
from ..llm import LLMError, get_client
from ..pipeline.handoff import build_handoff
from ..pipeline.mock import build_mock_session
from ..pipeline.real import build_llm_session
from ..pipeline.revise import apply_answer, apply_manual_patch
from ..pipeline.requirements import Category, RequirementsAnalysis, analyze, validate_categories
from ..schemas import AnswerRequest, CreateSessionRequest, LedgerEntry, Session, SpecPatchRequest
from ..store import list_sessions, load_session, now_iso, save_session, session_lock

router = APIRouter(prefix="/api", tags=["sessions"])

NOT_FOUND = "找不到这个会话，可能已经被删除。"
HOLDOUT_BLOCKED = "这个案例是留出评测集，不开放演示。"

NOTE_TITLES = {"风格调性", "廓形要求", "面料质感", "工艺细节", "色彩体系", "场景适配度", "情绪与审美", "使用场景", "使用人群", "业务台账备注"}

class NoteUpdate(BaseModel):
    title: str
    value: str = Field(max_length=10000)

class RequirementsReviewRequest(BaseModel):
    categories: list[Category]
    note: str = Field(default="", max_length=2000)


def _append_ledger(session: Session, *, summary: str, detail: str) -> None:
    session.ledger.append(
        LedgerEntry(
            version=session.spec_versions[-1].version,
            at=now_iso(),
            action="审计",
            summary=summary,
            detail=detail,
            utterance_ids=[],
            changed_fields=[],
        )
    )

def _notes_path(session_id: str):
    _load(session_id)
    return settings.storage_dir / "notes" / f"{session_id}.json"

def _read_notes(path):
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))

@router.get("/sessions/{session_id}/notes")
def get_notes(session_id: str) -> dict[str, str]:
    with session_lock(session_id):
        return _read_notes(_notes_path(session_id))

@router.patch("/sessions/{session_id}/notes")
def update_note(session_id: str, request: NoteUpdate) -> dict[str, str]:
    if request.title not in NOTE_TITLES:
        raise HTTPException(status_code=400, detail="未知的备注项目。")
    with session_lock(session_id):
        path = _notes_path(session_id)
        notes = _read_notes(path)
        previous = notes.get(request.title, "")
        notes[request.title] = request.value
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.stem}.{uuid.uuid4().hex}.tmp")
        try:
            tmp.write_text(json.dumps(notes, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, path)
        finally:
            tmp.unlink(missing_ok=True)
        if previous != request.value:
            session = _load(session_id)
            label = "业务台账备注" if request.title == "业务台账备注" else f"需求报告：{request.title}"
            _append_ledger(
                session,
                summary=f"保存{label}",
                detail="人工补充说明已保存；不会自动改写设计方案。",
            )
            save_session(session)
        return notes

def _analysis_path(session_id: str):
    _load(session_id)
    return settings.storage_dir / "requirements" / f"{session_id}.json"

@router.get("/sessions/{session_id}/requirements-analysis")
def get_requirements_analysis(session_id: str) -> dict | None:
    with session_lock(session_id):
        path = _analysis_path(session_id)
        return _read_notes(path) if path.exists() else None

@router.post("/sessions/{session_id}/requirements-analysis")
def create_requirements_analysis(session_id: str) -> dict:
    session = _load(session_id)
    if settings.mock_mode or session.source != "llm":
        raise HTTPException(status_code=400, detail="语义解构需要真实模型会话；示例模式可使用规则线索。")
    client = get_client()
    try:
        analysis = analyze(session, client)
    except LLMError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    categories = analysis.model_dump(mode="json")["categories"]
    generated_at = now_iso()
    with session_lock(session_id):
        path = _analysis_path(session_id)
        previous = _read_notes(path) if path.exists() else {}
        revisions = list(previous.get("revisions", []))
        if previous.get("categories"):
            revisions.append(
                {
                    "at": generated_at,
                    "action": "重新运行模型",
                    "model": previous.get("model"),
                    "categories": previous["categories"],
                }
            )
        result = {
            "model": client.model,
            "generated_at": generated_at,
            "model_categories": categories,
            "categories": categories,
            "review": {"status": "待人工核对", "reviewed_at": None, "note": ""},
            "revisions": revisions,
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.stem}.{uuid.uuid4().hex}.tmp")
        try:
            tmp.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, path)
        finally:
            tmp.unlink(missing_ok=True)
        session = _load(session_id)
        cited = len({item_id for row in categories for item_id in row["utterance_ids"]})
        _append_ledger(
            session,
            summary="模型完成需求语义归类",
            detail=f"模型 {client.model} 返回九个维度，引用 {cited} 条会话内原话；结果等待人工核对。",
        )
        save_session(session)
    return result


@router.patch("/sessions/{session_id}/requirements-analysis")
def review_requirements_analysis(session_id: str, request: RequirementsReviewRequest) -> dict:
    session = _load(session_id)
    try:
        analysis = RequirementsAnalysis(categories=request.categories)
        validate_categories(session, analysis)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    with session_lock(session_id):
        path = _analysis_path(session_id)
        existing = _read_notes(path) if path.exists() else None
        if not existing:
            raise HTTPException(status_code=400, detail="请先完成模型语义归类，再进行人工核对。")
        reviewed_at = now_iso()
        categories = analysis.model_dump(mode="json")["categories"]
        revisions = list(existing.get("revisions", []))
        revisions.append(
            {
                "at": reviewed_at,
                "action": "人工核对或修正",
                "before": existing.get("categories", []),
                "after": categories,
                "note": request.note,
            }
        )
        result = {
            **existing,
            "categories": categories,
            "review": {
                "status": "已人工核对",
                "reviewed_at": reviewed_at,
                "note": request.note,
            },
            "revisions": revisions,
        }
        tmp = path.with_name(f"{path.stem}.{uuid.uuid4().hex}.tmp")
        try:
            tmp.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, path)
        finally:
            tmp.unlink(missing_ok=True)
        _append_ledger(
            session,
            summary="人工核对需求语义归类",
            detail=request.note or "业务人员已核对九个维度的原话引用；系统不会自动改写设计方案。",
        )
        save_session(session)
    return result


def _load(session_id: str) -> Session:
    try:
        return load_session(session_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=NOT_FOUND)


@router.post("/sessions", response_model=Session)
def create_session(request: CreateSessionRequest) -> Session:
    # 留出案例在建会话前就拦住，示例和真实两条分支都不能碰它。
    if request.sample_id:
        case = knowledge.get_case(request.sample_id)
        if case is not None and knowledge.is_blocked_sample(case):
            raise HTTPException(status_code=400, detail=HOLDOUT_BLOCKED)
    if settings.mock_mode:
        if not request.sample_id:
            raise HTTPException(status_code=400, detail="示例模式只支持示例案例")
        try:
            session = build_mock_session(request)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
    else:
        try:
            session = build_llm_session(request)
        except LLMError as exc:
            # 大模型连不上、超时、输出不合格：502，文案已经是中文说明
            raise HTTPException(status_code=502, detail=str(exc))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
    save_session(session)
    return session


@router.get("/sessions")
def get_sessions() -> list[dict]:
    return list_sessions()


@router.get("/sessions/{session_id}", response_model=Session)
def get_session(session_id: str) -> Session:
    return _load(session_id)


@router.post("/sessions/{session_id}/answers", response_model=Session)
def answer_question(session_id: str, request: AnswerRequest) -> Session:
    # 读—改—写全程锁住：同一问题并发提交不会重复升版，两个不同修改也不会互相覆盖。
    with session_lock(session_id):
        session = _load(session_id)
        try:
            updated = apply_answer(session, request)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        if updated is not session:
            save_session(updated)
    return updated


@router.patch("/sessions/{session_id}/spec", response_model=Session)
def patch_spec(session_id: str, request: SpecPatchRequest) -> Session:
    with session_lock(session_id):
        session = _load(session_id)
        try:
            updated = apply_manual_patch(session, request.field, request.value, request.reason)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        if updated is not session:
            save_session(updated)
    return updated


@router.get("/sessions/{session_id}/handoff")
def get_handoff(session_id: str) -> dict:
    session = _load(session_id)
    return {"markdown": build_handoff(session)}
