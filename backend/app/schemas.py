"""《开发总体说明与接口约定》第 5 节的数据结构。

这里所有类的名字都与约定一致，JSON 键用英文，值里的字段名和状态用中文词表。
响应模型 `extra="forbid"` 保证返回的字段不多也不少；请求模型允许忽略未知字段。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

# ---------------------------------------------------------------- 固定词表

FIELD_ORDER: tuple[str, ...] = (
    "品类",
    "版型结构",
    "领型",
    "面料",
    "色彩",
    "装饰纹样",
    "长度比例",
    "松弛余量",
)

Field = Literal[
    "品类", "版型结构", "领型", "面料", "色彩", "装饰纹样", "长度比例", "松弛余量"
]

Status = Literal[
    "有冲突", "被改写", "未落实", "需设计师判断", "间接体现", "已落实", "已与客户确认"
]

# 严重程度从高到低。已与客户确认是系统追加的状态，视为最低严重程度。
STATUS_SEVERITY: dict[str, int] = {
    "有冲突": 6,
    "被改写": 5,
    "未落实": 4,
    "需设计师判断": 3,
    "间接体现": 2,
    "已落实": 1,
    "已与客户确认": 0,
}

Strength = Literal["硬约束", "软偏好", "目标", "描述"]
Confidence = Literal["确定", "疑似", "看不出"]
Origin = Literal["资源库", "已有方案", "客户确认", "人工修改", "无出处"]
Mode = Literal["审计已有方案", "从零生成方案"]
Source = Literal["mock", "llm"]
LedgerAction = Literal[
    "导入", "审计", "客户确认", "人工修改", "参考图解析", "出图", "核对",
    "引用旧案", "换面料", "复述确认",
]


def is_field(value: str) -> bool:
    return value in FIELD_ORDER


def most_severe(statuses: list[str]) -> str:
    """取最严重的状态；空列表时按“已落实”处理。"""
    if not statuses:
        return "已落实"
    return max(statuses, key=lambda s: STATUS_SEVERITY.get(s, 0))


# ---------------------------------------------------------------- 结构


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _Request(BaseModel):
    model_config = ConfigDict(extra="ignore")


class Mapping(_Model):
    field: Field
    garment: str | None = None
    status: Status
    note: str = ""
    rule_id: str | None = None


class Utterance(_Model):
    id: str
    text: str
    start: int
    end: int
    type: str
    strength: Strength
    status: Status
    mappings: list[Mapping]
    resolved_by: str | None = None


class SpecField(_Model):
    value: str
    resource_ids: list[str] = []
    utterance_ids: list[str] = []
    origin: Origin
    confirmed: bool = False


class SpecVersion(_Model):
    version: int
    created_at: str
    reason: str
    # 键固定为八个设计字段，由 FIELD_ORDER 保证；用 str 键避免运行时对
    # Literal 键的限制，读取时再按 FIELD_ORDER 校验。
    fields: dict[str, SpecField]


class Option(_Model):
    id: str
    label: str
    detail: str = ""
    resource_ids: list[str] = []
    patch: dict[str, str] = {}
    note: str = ""
    todo: str | None = None


class Question(_Model):
    id: str
    utterance_ids: list[str]
    field: Field
    title: str
    context: str = ""
    why: str = ""
    options: list[Option]
    answer: str | None = None


class DesignerItem(_Model):
    id: str
    utterance_ids: list[str]
    field: Field
    text: str


class SkipItem(_Model):
    field: str
    reason: str


class LedgerEntry(_Model):
    version: int
    at: str
    action: LedgerAction
    summary: str
    detail: str = ""
    utterance_ids: list[str] = []
    changed_fields: list[Field] = []


class RefField(_Model):
    value: str | bool
    confidence: Confidence
    used: bool


class RefConflict(_Model):
    field: Field
    message: str
    utterance_ids: list[str] = []


class ReferenceAnalysis(_Model):
    image_url: str
    fields: dict[str, RefField]
    conflicts: list[RefConflict] = []
    model: str


class CheckItem(_Model):
    field: Field
    expect: str
    observed: str
    verdict: Literal["符合", "不符合", "看不出"]
    confidence: Confidence


class CheckResult(_Model):
    items: list[CheckItem]
    model: str


class GeneratedImage(_Model):
    id: str
    version: int
    kind: Literal["成衣挂拍", "面料细节", "上身效果"]
    prompt: str
    url: str | None = None
    provider: str
    check: CheckResult | None = None


class SessionInput(_Model):
    text: str
    existing_spec: dict[str, str] | None = None
    sample_id: str | None = None


class RestatementPoint(_Model):
    id: str
    field: str
    utterance_ids: list[str] = []
    question: str
    answer: Literal["对", "不对"] | None = None
    note: str = ""


class Restatement(_Model):
    """复述确认：把方案翻成客户听得懂的话，再列出“可能听错”的几处，请客户逐条点对 / 不对。"""

    version: int
    created_at: str
    text: str
    points: list[RestatementPoint] = []
    source: Literal["llm", "template"] = "template"


class Session(_Model):
    id: str
    title: str
    mode: Mode
    source: Source
    created_at: str
    input: SessionInput
    utterances: list[Utterance] = []
    spec_versions: list[SpecVersion]
    questions: list[Question] = []
    designer_items: list[DesignerItem] = []
    skipped: list[SkipItem] = []
    ledger: list[LedgerEntry] = []
    reference: ReferenceAnalysis | None = None
    images: list[GeneratedImage] = []
    restatement: Restatement | None = None


# ---------------------------------------------------------------- 请求体


class CreateSessionRequest(_Request):
    text: str = ""
    existing_spec: dict[str, str] | None = None
    sample_id: str | None = None
    title: str | None = None


class AnswerRequest(_Request):
    question_id: str
    option_id: str


class SpecPatchRequest(_Request):
    field: str
    value: str
    reason: str = ""