"""大模型输出的严格结构（只用于校验模型返回，不对外暴露）。

约定第 8 节要求“大模型输出一律做 JSON 校验”。这里用 Pydantic 的 `extra="forbid"`
把字段卡死：多一个字段、状态值不在词表、少了必填字段，都算不合格，
由 `app.llm.LLMClient.request_json` 带着原因重试一次。

注意：`已与客户确认` 是系统在客户回答后追加的状态，不是模型能给的结论，
所以模型输出的状态里不允许出现它。
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field as PField, field_validator

from .schemas import Field, Strength

ExtractedStatus = Literal[
    "已落实", "间接体现", "被改写", "有冲突", "未落实", "需设计师判断"
]

_RULE_ID_RE = re.compile(r"^R[1-8]$")


class ExtractedMapping(BaseModel):
    """一句原话对一个设计字段的要求及落实情况。"""

    model_config = ConfigDict(extra="forbid")

    field: Field
    garment: str | None = None
    status: ExtractedStatus
    note: str = ""
    rule_id: str | None = None

    @field_validator("garment", mode="after")
    @classmethod
    def _clean_garment(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @field_validator("rule_id", mode="after")
    @classmethod
    def _clean_rule_id(cls, value: str | None) -> str | None:
        """只接受 R1–R8。编出来的规则编号算不合格，交给上层重试一次，不悄悄清成 null。"""
        if value is None:
            return None
        text = str(value).strip().upper()
        if not text:
            return None
        if not _RULE_ID_RE.match(text):
            raise ValueError(f"rule_id 只能是 R1–R8 或 null，收到的是「{value}」")
        return text


class ExtractedUtterance(BaseModel):
    """一句原话。text 必须是客户口述的原文子串，由程序定位，模型不给位置。"""

    model_config = ConfigDict(extra="forbid")

    text: str = PField(min_length=1)
    type: str = "描述"
    strength: Strength
    mappings: list[ExtractedMapping] = PField(min_length=1, max_length=8)

    @field_validator("text", mode="after")
    @classmethod
    def _strip_text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("原话不能为空")
        return text

    @field_validator("type", mode="after")
    @classmethod
    def _clean_type(cls, value: str) -> str:
        return (value or "").strip() or "描述"


class ExtractionResult(BaseModel):
    """一次解构的完整输出。"""

    model_config = ConfigDict(extra="forbid")

    utterances: list[ExtractedUtterance] = PField(min_length=1, max_length=40)
