"""资源库接口：返回 data/resources.json，来源字段只保留开发集案例。"""

from __future__ import annotations

from fastapi import APIRouter

from .. import knowledge
from ..resource_catalog import build_catalog

router = APIRouter(prefix="/api", tags=["resources"])


@router.get("/resources")
def get_resources() -> dict:
    return knowledge.public_resources()


@router.get("/resource-catalog")
def get_resource_catalog() -> dict:
    return build_catalog()
