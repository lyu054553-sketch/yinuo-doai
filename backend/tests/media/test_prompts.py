"""C0 单元测试：提示词确定性、核对清单、映射数据表。不调用任何模型。"""

import json
from pathlib import Path

import pytest

from app.media import prompts as p

PROJECT_ROOT = Path(__file__).resolve().parents[3]
CASES_PATH = PROJECT_ROOT / "data" / "cases.json"
MAPPINGS = p.load_mappings()


def load_case(case_id: str) -> dict:
    cases = json.loads(CASES_PATH.read_text(encoding="utf-8"))["cases"]
    return next(case for case in cases if case["id"] == case_id)


def spec_version(case_id: str, version: int = 1) -> dict:
    """按约定 SpecVersion 结构包一层，模拟后端核心存下来的版本。"""
    case = load_case(case_id)
    return {
        "version": version,
        "created_at": "2026-11-18T09:00:00+08:00",
        "reason": "导入",
        "fields": {
            field: {
                "value": case["spec"].get(field, ""),
                "resource_ids": [],
                "utterance_ids": [],
                "origin": "已有方案",
                "confirmed": False,
            }
            for field in p.FIELD_ORDER
        },
    }


def check_by_target(specs, target: str) -> dict:
    return next(item for item in specs if item["target"] == target)


# ---------- 映射数据表 ----------

def test_field_order_matches_mapping():
    assert tuple(MAPPINGS["field_order"]) == p.FIELD_ORDER
    assert len(p.FIELD_ORDER) == 8


def test_vision_keys_fields_are_in_eight_fields():
    for key, meta in MAPPINGS["vision_keys"].items():
        assert meta["field"] in p.FIELD_ORDER, key
        for source in meta["source_fields"]:
            assert source in p.FIELD_ORDER, (key, source)
    # 纽扣和闭合方式都归“版型结构”
    assert MAPPINGS["vision_keys"]["buttons_visible"]["field"] == "版型结构"
    assert MAPPINGS["vision_keys"]["front_closure"]["field"] == "版型结构"


def test_check_order_matches_vision_keys():
    assert list(MAPPINGS["check_order"]) == list(MAPPINGS["vision_keys"].keys())


def test_rule_vocabulary_consistent():
    vocab = MAPPINGS["vision_vocabulary"]

    def assert_vocab(key, value, allow_alternatives=True):
        assert value in vocab[key], (key, value)
        return value

    for rule in MAPPINGS["rules"]["collar_rules"]:
        assert_vocab("collar_family", rule["vision"])
        assert rule["keywords"]
    for rule in MAPPINGS["rules"]["closure_rules"]:
        assert_vocab("front_closure", rule["vision"])
        for alt in rule.get("alternatives", []):
            assert_vocab("front_closure", alt)
    for rule in MAPPINGS["rules"]["length_rules"]:
        assert_vocab("length", rule["vision"])
    for rule in MAPPINGS["rules"]["silhouette_rules"]:
        assert_vocab("silhouette", rule["vision"])


def test_frozen_policy_manual_flags():
    """冻结策略：衣长/廓形/颜色不采用模型结果，一律需人工确认。"""
    for key in ("length", "silhouette", "colors"):
        meta = MAPPINGS["vision_keys"][key]
        assert meta["used"] is False
        assert meta["manual_confirm"] is True
        assert meta["confidence_cap"] is None
    for key in ("collar_family", "front_closure", "buttons_visible", "pattern"):
        meta = MAPPINGS["vision_keys"][key]
        assert meta["used"] is True
        assert meta["confidence_cap"] == "疑似"


# ---------- 提示词 ----------

def test_response_shape():
    result = p.build_prompts(spec_version("c6", version=3))
    assert set(result) == {"version", "items", "checks"}
    assert result["version"] == 3
    assert [item["kind"] for item in result["items"]] == list(p.KINDS)
    for item in result["items"]:
        assert isinstance(item["text"], str) and item["text"].startswith(p.HEADERS[item["kind"]])
        assert item["text"].endswith(p.FOOTERS[item["kind"]])
    assert result["checks"], "核对清单不能为空"
    for check in result["checks"]:
        assert set(check) == {"field", "expect"}
        assert check["field"] in p.FIELD_ORDER
        assert isinstance(check["expect"], str) and check["expect"]


def test_prompts_are_deterministic():
    first = p.build_prompts(spec_version("c2"))
    second = p.build_prompts(spec_version("c2"))
    assert first == second
    assert json.dumps(first, ensure_ascii=False) == json.dumps(second, ensure_ascii=False)


def test_prompt_text_ignores_version_number():
    """同方案不同版本号，提示词逐字一致（版本号只出现在响应顶层）。"""
    v1 = p.build_prompts(spec_version("c6", version=1))
    v2 = p.build_prompts(spec_version("c6", version=7))
    assert v1["items"] == v2["items"]
    assert v1["checks"] == v2["checks"]


def test_prompt_body_contains_fields_and_skips_unspecified():
    result = p.build_prompts(spec_version("c2"))
    text = result["items"][0]["text"]
    for field in ("品类", "版型结构", "领型", "面料", "装饰纹样", "长度比例", "松弛余量"):
        assert f"【{field}】" in text
    # 案例二色彩是“无特殊需求”，不进提示词
    assert "【色彩】" not in text
    assert "无特殊需求" not in text


def test_three_prompts_share_body_but_differ_by_kind():
    items = p.build_prompt_items(spec_version("c6"))
    texts = [item["text"] for item in items]
    assert len(set(texts)) == 3
    bodies = [line for line in texts[0].splitlines() if line.startswith("【领型】")]
    for text in texts[1:]:
        assert bodies == [line for line in text.splitlines() if line.startswith("【领型】")]


def test_accepts_flat_dict_and_specfield_dict():
    flat = {field: load_case("c6")["spec"][field] for field in p.FIELD_ORDER}
    flat["色彩"] = "无特殊需求"
    wrapped = {field: {"value": flat[field]} for field in p.FIELD_ORDER}
    assert p.build_prompts(flat)["items"] == p.build_prompts(wrapped)["items"]
    assert p.build_prompts(flat)["version"] == 1


def test_multi_garment_field_dict():
    case = load_case("c3")
    spec = {"version": 1, "fields": case["spec"]}
    text = p.build_prompts(spec)["items"][0]["text"]
    assert "外套：" in text and "连衣裙：" in text


def test_multi_garment_prompts_show_every_piece():
    case = load_case("c3")
    items = p.build_prompt_items({"version": 1, "fields": case["spec"]})
    for item in items:
        assert "成套服装" in item["text"]
        assert "单件上装" not in item["text"]


def test_rejects_non_mapping():
    with pytest.raises(TypeError):
        p.build_prompts("不是方案")


# ---------- 与后端核心 Pydantic 对象的集成（模块本身不 import app.schemas） ----------

def test_accepts_pydantic_spec_version_object():
    """真实的 Pydantic SpecVersion 对象：结果必须与同内容的 dict 逐字一致。"""
    from app.schemas import SpecField, SpecVersion  # 仅测试里引用，模块不依赖

    case = load_case("c6")
    fields = {
        field: SpecField(
            value=case["spec"][field],
            resource_ids=[],
            utterance_ids=[],
            origin="已有方案",
            confirmed=False,
        )
        for field in p.FIELD_ORDER
    }
    obj = SpecVersion(
        version=3,
        created_at="2026-11-18T09:00:00+08:00",
        reason="导入",
        fields=fields,
    )
    assert not isinstance(obj, dict), "这条测的是对象，不是 dict"

    from_obj = p.build_prompts(obj)
    from_dict = p.build_prompts(spec_version("c6", version=3))
    assert from_obj == from_dict
    assert from_obj["version"] == 3
    # 核对清单同样接受对象
    assert p.build_check_specs(obj) == p.build_check_specs(spec_version("c6", version=3))


def test_rejects_object_without_model_dump():
    """没有 model_dump()、也不是 Mapping 的对象仍然报 TypeError，不硬凑结果。"""

    class Dummy:
        version = 1
        fields = {field: {"value": "某处随便写的"} for field in p.FIELD_ORDER}

    with pytest.raises(TypeError):
        p.build_prompts(Dummy())


# ---------- 核对清单 ----------

def test_c6_checks_expected():
    specs = {item["target"]: item for item in p.build_check_specs(spec_version("c6"))}
    assert specs["collar_family"]["expected_value"] == "交领"
    assert specs["buttons_visible"]["expected_value"] is False
    assert specs["buttons_visible"]["expect"] == "不应看到纽扣"
    assert specs["front_closure"]["expected_value"] == "交叠闭合"
    assert specs["front_closure"]["alternatives"] == ["无扣开襟"]
    assert specs["pattern"]["expected_value"] == "可见纹样"
    assert specs["length"]["expected_value"] == "臀部附近"
    assert specs["silhouette"]["expected_value"] == "H 型"


def test_c2_checks_expected():
    specs = {item["target"]: item for item in p.build_check_specs(spec_version("c2"))}
    assert specs["collar_family"]["expected_value"] == "翻领"
    assert specs["pattern"]["expected_value"] == "可见纹样"
    assert specs["length"]["expected_value"] == "臀部附近"
    # 案例二方案未写前襟闭合方式和纽扣
    assert specs["front_closure"]["expected_value"] == "看不出"
    assert specs["buttons_visible"]["expected_value"] == "看不出"
    assert specs["colors"]["expected_value"] == "看不出"


def test_explicit_no_independent_pattern_does_not_expect_visible_pattern():
    case = load_case("c2")
    fields = {field: {"value": value} for field, value in case["spec"].items()}
    fields["装饰纹样"] = {
        "value": "无独立图案；如面料自身带提花肌理，以实物样板确认"
    }
    specs = {item["target"]: item for item in p.build_check_specs({"fields": fields})}
    assert specs["pattern"]["expected_value"] == "无可见纹样"
    assert specs["pattern"]["expect"] == "应无明显纹样"


def test_manual_confirm_items_carry_label():
    specs = {item["target"]: item for item in p.build_check_specs(spec_version("c6"))}
    for target in ("length", "silhouette", "colors"):
        assert specs[target]["manual_confirm"] is True
        assert "需人工确认" in specs[target]["expect"]
    for target in ("collar_family", "front_closure", "buttons_visible", "pattern"):
        assert specs[target]["manual_confirm"] is False


def test_negation_guard():
    """“无立领”“无收腰”不能被当成需要立领/收腰。"""
    c6 = load_case("c6")
    specs = {item["target"]: item for item in p.build_check_specs({"fields": c6["spec"]})}
    assert specs["collar_family"]["expected_value"] == "交领"
    c1 = load_case("c1")
    specs = {item["target"]: item for item in p.build_check_specs({"fields": c1["spec"]})}
    assert specs["silhouette"]["expected_value"] == "H 型"
    # 低立领保留“领高需人工确认”
    assert specs["collar_family"]["expected_value"] == "立领"
    assert "领高" in specs["collar_family"]["expect"]


def test_check_fields_only_eight_fields_and_buttons_in_structure():
    specs = p.build_check_specs(spec_version("c2"))
    fields = [item["field"] for item in specs]
    assert set(fields) <= set(p.FIELD_ORDER)
    assert "版型结构" in fields
