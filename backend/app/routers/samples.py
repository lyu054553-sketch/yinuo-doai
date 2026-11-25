"""示例案例接口：只给出 data/cases.json 里的非留出案例。"""

from __future__ import annotations

from fastapi import APIRouter

from .. import knowledge

router = APIRouter(prefix="/api", tags=["samples"])


@router.get("/samples")
def get_samples() -> list[dict]:
    items: list[dict] = []
    for case in knowledge.get_cases():
        if knowledge.is_blocked_sample(case):
            continue
        case_id = case.get("id", "")
        items.append(
            {
                "id": case_id,
                "title": case.get("title", ""),
                "text": case.get("customer_text", ""),
                "existing_spec": knowledge.case_spec(case),
                # 留出案例不会出现在列表里；字段保留是为了前端类型稳定
                "holdout": False,
            }
        )
    return items
