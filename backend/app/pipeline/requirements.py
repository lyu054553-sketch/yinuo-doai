"""Classify existing verbatim utterances into competition dimensions."""
from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ..llm import LLMClient
from ..schemas import Session

TITLES = ("风格调性", "廓形要求", "面料质感", "工艺细节", "色彩体系", "场景适配度", "情绪与审美", "使用场景", "使用人群")
Title = Literal["风格调性", "廓形要求", "面料质感", "工艺细节", "色彩体系", "场景适配度", "情绪与审美", "使用场景", "使用人群"]


class Category(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: Title
    utterance_ids: list[str] = Field(max_length=40)


class RequirementsAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")
    categories: list[Category] = Field(min_length=len(TITLES), max_length=len(TITLES))


def validate_categories(session: Session, result: RequirementsAnalysis) -> None:
    valid_ids = {item.id for item in session.utterances}
    titles = [category.title for category in result.categories]
    if len(set(titles)) != len(TITLES) or set(titles) != set(TITLES):
        raise ValueError("九个维度必须各出现一次")
    for category in result.categories:
        if len(set(category.utterance_ids)) != len(category.utterance_ids):
            raise ValueError(f"{category.title}含重复原话编号")
        if not set(category.utterance_ids).issubset(valid_ids):
            raise ValueError(f"{category.title}引用不存在的原话编号")


def analyze(session: Session, client: LLMClient) -> RequirementsAnalysis:
    items = [{"id": item.id, "text": item.text} for item in session.utterances]

    prompt = (
        "请将客户原话编号归入下列九个维度，每个维度必须出现一次。"
        "只依据给出的原话判断，未明确时返回空数组，不要根据方案、常识或人群刻板印象猜测。"
        "同一句可以属于多个维度；不要输出任何原话之外的内容。\n"
        "风格调性是客户明确说的风格、形象或气质；廓形要求包括版型、松量和长度；"
        "面料质感指面料或触感；工艺细节指具体制作、装饰或结构工艺；色彩体系指颜色；"
        "场景适配度和使用场景指客户明确的穿着场合；情绪与审美指审美和感受；"
        "使用人群只有客户明确指定穿着人群时填写，不能根据案例推断。\n"
        "原话：" + json.dumps(items, ensure_ascii=False) + "\n维度：" + "、".join(TITLES)
        + '\n只返回 JSON，例如 {"categories":[{"title":"风格调性","utterance_ids":["u1"]}, ...]}。'
    )
    return client.request_json(
        "你是服饰定制需求分析助手。仅分类给定原话编号，无法确定则留空，输出严格 JSON。",
        prompt,
        RequirementsAnalysis,
        post_validate=lambda result: validate_categories(session, result),
        retries=1,
    )
