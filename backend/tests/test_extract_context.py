"""提取提示的“语义单位”自检（不联网、不调付费服务）。

总控发现：模型会把同一条设计要求里的条件、否定、允许替代项拆成互不关联的短句。
这里只检查**提示行为和假传输实际送出的内容**：

* 审计与从零两种提示都要求：以“可独立审计的一项要求”为单位，不机械按逗号拆；
  紧密相连的原因/条件与要求/允许替代项留在同一段连续原文；不同参数、不同单品要分开；
* 否定不能被反向当成正向要求；只因未知材料名不能断言现实不存在；
* 程序仍严格校验原话是原文子串，且不会把相邻句子拼接；
* 从零模式仍只抽取、不判断落实。

全部用全新合成的句子，不涉及留出案例五；只证明提示携带了这些约束，
不声称真实模型效果已经提高（真实效果由总控独立自检）。
"""

from __future__ import annotations

import json

import pytest

from app.llm import LLMClient, LLMError
from app.pipeline import extract


class FakeTransport:
    """假传输：按顺序返回响应内容，记录每次送出的 payload，不碰网络。"""

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


def make_client(transport: FakeTransport) -> LLMClient:
    return LLMClient(
        base_url="http://model.invalid/v1",
        api_key="test-key",
        model="fake-model",
        timeout=5.0,
        transport=transport,
    )


def user_content(payload: dict) -> str:
    return payload["messages"][1]["content"]


def system_content(payload: dict) -> str:
    return payload["messages"][0]["content"]


# ---------------------------------------------------------------- 提示文本本身


def test_both_prompts_share_semantic_unit_rules() -> None:
    audit_prompt = extract.build_user_prompt("合成测试口述。", {})
    zero_prompt = extract.build_from_zero_prompt("合成测试口述。")

    for prompt in (audit_prompt, zero_prompt):
        assert "可独立审计的一项要求" in prompt
        assert "不要机械按逗号拆" in prompt
        assert "不要把整段口述合成一句" in prompt
        assert "二选一" in prompt
        assert "拼色" in prompt
        assert "否定就是否定" in prompt
        assert "现实中不存在" in prompt
        # 同一套语义单位口径要同时出现在两种模式里
        assert "审计与从零两种模式同一口径" in prompt


def test_prompt_distinguishes_alternatives_from_combinations() -> None:
    rules = extract._semantic_unit_lines()
    assert "允许任选其一" in rules
    assert "同时满足的组合" in rules
    # “或/任意/二选一”归为允许替代，“和/拼色/搭配”归为组合，不能混为一谈
    assert "或／任意／二选一" in rules
    assert "和／拼色／搭配" in rules


def test_prompt_separates_parameters_and_garments() -> None:
    rules = extract._semantic_unit_lines()
    # 领高与领宽即使同属领型字段也是两项要求
    assert "领高" in rules and "领宽" in rules
    assert "两项要求" in rules
    # 多件套要按单品分开
    assert "garment" in rules


def test_system_prompts_state_units_and_from_zero_boundary() -> None:
    audit_system = extract.build_system_prompt(False)
    zero_system = extract.build_system_prompt(True)
    for system in (audit_system, zero_system):
        assert "可独立审计" in system
        assert "不要把整段口述合成一句" in system
        assert "否定" in system
    assert "不要判断“落实得怎么样”" in zero_system


def test_from_zero_prompt_asks_extraction_only() -> None:
    prompt = extract.build_from_zero_prompt("合成测试口述。")
    assert "不要猜“落实”或“未落实”" in prompt
    assert "可独立审计的一项要求" in prompt


def test_unknown_material_rule_in_audit_judgement() -> None:
    audit_prompt = extract.build_user_prompt("合成测试口述。", {})
    # 只凭材料名陌生，不能断言现实里不存在 / 做不出来
    assert "现实中不存在" in audit_prompt
    assert "现实可行性留给设计师判断" in audit_prompt
    # 这是审计判断口径，从零模式不判断落实，所以没有这句审计结论要求
    assert "现实可行性留给设计师判断" not in extract.build_from_zero_prompt("合成测试口述。")


# ---------------------------------------------------------------- 假传输实际送出的内容


def test_audit_request_carries_rules_and_keeps_units_separate() -> None:
    text = "因为脖子短，领口开低一点；领宽收窄一点，领子不要太高。"
    output = {
        "utterances": [
            {
                "text": "因为脖子短，领口开低一点",
                "type": "领型",
                "strength": "硬约束",
                "mappings": [{"field": "领型", "status": "已落实", "note": "条件与要求连在一起"}],
            },
            {
                "text": "领宽收窄一点",
                "type": "领型",
                "strength": "硬约束",
                "mappings": [{"field": "领型", "status": "已落实", "note": "领宽是另一项要求"}],
            },
            {
                "text": "领子不要太高",
                "type": "领型",
                "strength": "硬约束",
                "mappings": [{"field": "领型", "status": "未落实", "note": "否定保留原样"}],
            },
        ]
    }
    transport = FakeTransport([output])
    utterances = extract.extract_utterances(text, {}, client=make_client(transport))

    # 规则真的随请求送给了模型
    sent_user = user_content(transport.payloads[0])
    sent_system = system_content(transport.payloads[0])
    assert "可独立审计的一项要求" in sent_user
    assert "不要机械按逗号拆" in sent_user
    assert "二选一" in sent_user
    assert "可独立审计" in sent_system

    # 程序不拼接：三句原话各自保留，且都是原文的连续片段
    assert len(utterances) == 3
    for utterance in utterances:
        assert text[utterance.start : utterance.end] == utterance.text
    assert all(utterance.text != text for utterance in utterances)
    # 原因/条件与要求没有被拆散
    assert utterances[0].text.startswith("因为脖子短")
    # 否定词原样保留，没有被反向改写
    assert "不要" in utterances[2].text


def test_from_zero_request_carries_rules_and_does_not_judge() -> None:
    text = "面料要梅花或竹叶暗纹，二选一。"
    output = {
        "utterances": [
            {
                "text": "面料要梅花或竹叶暗纹，二选一",
                "type": "纹样",
                "strength": "硬约束",
                # 模型即使给了审计结论，从零模式也要重置为“已落实”，不判断落实
                "mappings": [{"field": "装饰纹样", "status": "未落实", "note": "允许替代"}],
            }
        ]
    }
    transport = FakeTransport([output])
    utterances = extract.extract_utterances(
        text, {}, client=make_client(transport), from_zero=True
    )

    sent_user = user_content(transport.payloads[0])
    sent_system = system_content(transport.payloads[0])
    assert "可独立审计的一项要求" in sent_user
    assert "不要把整段口述合成一句" in sent_user
    assert "不要判断“落实得怎么样”" in sent_system

    assert len(utterances) == 1
    assert text[utterances[0].start : utterances[0].end] == utterances[0].text
    assert utterances[0].mappings[0].status == "已落实"


def test_span_validation_still_rejects_paraphrase_after_prompt_change() -> None:
    text = "外套要藏蓝色，最好有竹叶暗纹。"
    bad = {
        "utterances": [
            {
                "text": "外套要深蓝色",
                "type": "色彩",
                "strength": "硬约束",
                "mappings": [{"field": "色彩", "status": "已落实", "note": "改写了原文"}],
            }
        ]
    }
    transport = FakeTransport([bad, bad])
    with pytest.raises(LLMError) as excinfo:
        extract.extract_utterances(text, {}, client=make_client(transport))

    assert "原文" in str(excinfo.value)
    assert len(transport.payloads) == 2
    # 重试提示里带上了“不是原文片段”的原因
    assert "不是客户口述的原文片段" in user_content(transport.payloads[1])


def _extraction(texts: list[str]) -> extract.ExtractionResult:
    return extract.ExtractionResult.model_validate(
        {
            "utterances": [
                {
                    "text": text,
                    "type": "描述",
                    "strength": "描述",
                    "mappings": [{"field": "品类", "status": "已落实", "note": "测试"}],
                }
                for text in texts
            ]
        }
    )


@pytest.mark.parametrize(
    ("text", "pieces"),
    [
        ("外套要宽松，领子要低。", ["外套要宽松", "外套要宽松"]),
        ("外套要宽松，领子要低。", ["领子要低", "外套要宽松"]),
        ("外套要宽松，领子要低。", ["外套要宽松，领子要低", "领子要低"]),
    ],
)
def test_span_validation_rejects_duplicate_reverse_and_overlap(
    text: str, pieces: list[str]
) -> None:
    with pytest.raises(ValueError, match="重叠或顺序颠倒"):
        extract.locate_utterances(text, _extraction(pieces))


def test_span_validation_allows_two_real_occurrences_in_order() -> None:
    text = "外套要宽松，连衣裙也要宽松。"
    result = _extraction(["宽松", "宽松"])
    spans = extract.locate_utterances(text, result)
    assert [text[start:end] for start, end in spans] == ["宽松", "宽松"]
    assert spans == [(3, 5), (11, 13)]
