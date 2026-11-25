"""工作台辅助接口：相似旧单、面料备选、复述确认。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET  | /api/sessions/{id}/similar-cases | 最像的旧单（最多 3 个），逐栏标出能不能用到本单 |
| POST | /api/sessions/{id}/reuse | 引用旧单某一栏的做法 → 方案升版、写台账 |
| GET  | /api/sessions/{id}/fabric-options | 当前面料 + 候选面料，各带三条依据和（模拟）库存 |
| POST | /api/sessions/{id}/fabric | 换成某块候选面料 → 方案升版、写台账 |
| POST | /api/sessions/{id}/restatement | 生成复述（真实模式用大模型改写口语，否则用模板） |
| POST | /api/sessions/{id}/restatement/answer | 客户对某一条点“对 / 不对” |
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..config import settings
from ..llm import get_client
from ..pipeline import assist
from ..schemas import Session
from ..store import load_session, save_session, session_lock

router = APIRouter(prefix="/api/sessions", tags=["assist"])


class ReuseRequest(BaseModel):
    case_id: str
    field: str


class FabricRequest(BaseModel):
    resource_id: str


class RestatementAnswer(BaseModel):
    point_id: str
    answer: str
    note: str = ""


def _load(session_id: str) -> Session:
    try:
        return load_session(session_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="找不到这次洽谈记录")


def _mutate(session_id: str, fn) -> Session:
    with session_lock(session_id):
        session = _load(session_id)
        try:
            updated = fn(session)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        if updated is not session:
            save_session(updated)
    return updated


@router.get("/{session_id}/similar-cases")
def get_similar_cases(session_id: str) -> list[dict]:
    return assist.similar_cases(_load(session_id))


@router.post("/{session_id}/reuse", response_model=Session)
def post_reuse(session_id: str, request: ReuseRequest) -> Session:
    return _mutate(session_id, lambda s: assist.reuse_case_field(s, request.case_id, request.field))


@router.get("/{session_id}/fabric-options")
def get_fabric_options(session_id: str) -> list[dict]:
    return assist.fabric_options(_load(session_id))


@router.post("/{session_id}/fabric", response_model=Session)
def post_fabric(session_id: str, request: FabricRequest) -> Session:
    return _mutate(session_id, lambda s: assist.choose_fabric(s, request.resource_id))


@router.post("/{session_id}/restatement", response_model=Session)
def post_restatement(session_id: str) -> Session:
    session = _load(session_id)
    client = None if settings.mock_mode or session.source != "llm" else get_client()
    restatement = assist.build_restatement(session, client)
    return _mutate(session_id, lambda s: assist.save_restatement(s, restatement))


@router.post("/{session_id}/restatement/answer", response_model=Session)
def post_restatement_answer(session_id: str, request: RestatementAnswer) -> Session:
    return _mutate(
        session_id,
        lambda s: assist.answer_restatement(s, request.point_id, request.answer, request.note),
    )
