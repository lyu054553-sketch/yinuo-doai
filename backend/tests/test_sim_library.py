"""模拟资源库与模拟仓库：可整份替换、可关掉，来源清楚标成“模拟库”，不碰留出案例。"""

from __future__ import annotations

import json

from app import knowledge


def test_sim_entries_are_labelled_and_recommendable() -> None:
    sim = json.loads(knowledge.SIM_PATH.read_text(encoding="utf-8"))
    ids = [item["id"] for items in sim["resources"].values() for item in items]
    assert ids and len(ids) == len(set(ids))
    for rid in ids:
        item = knowledge.resource_by_id(rid)
        assert item is not None and item["sources"] == ["sim"]
        assert rid.startswith("M")
        assert knowledge.is_recommendable(item)
    assert knowledge.sources_label(["sim"]) == "模拟库"


def test_sim_library_never_mentions_holdout_content() -> None:
    text = knowledge.SIM_PATH.read_text(encoding="utf-8")
    for word in ("c5", "案例五", "徽派", "肌理提花针织", "撞色"):
        assert word not in text


def test_sim_names_do_not_change_existing_plan_mapping() -> None:
    """模拟条目不能改变已有方案（案例原文）对到的资源编号，否则会改动核对已有方案的结果。"""
    for case in knowledge.get_cases():
        if knowledge.is_blocked_sample(case):
            continue
        for field, value in knowledge.case_spec(case).items():
            ids = knowledge.find_resource_ids(field, value)
            assert not [rid for rid in ids if rid.startswith("M")], (case["id"], field, ids)


def test_inventory_links_to_known_resources() -> None:
    entries = knowledge.inventory()
    assert entries
    for entry in entries:
        for key in ("fabric", "pattern", "color"):
            if entry.get(key):
                assert knowledge.resource_by_id(entry[key]) is not None, entry
        assert entry["status"] in ("有货", "少量", "需订货")


def test_stock_for_prefers_full_match() -> None:
    best = knowledge.stock_for(["MF1", "MP1", "MCO1"])[0]
    assert best["sku"] == "SIM-0101"
    assert knowledge.stock_for(["S1", "CL1"]) == []


def test_sim_library_can_be_switched_off(no_sim) -> None:
    assert knowledge.resource_by_id("MF1") is None
    assert knowledge.inventory() == []
    assert knowledge.stock_for(["F3"]) == []


def _utt(uid: str, text: str, fields: list[str], strength: str = "软偏好"):
    from app.schemas import Utterance

    return Utterance.model_validate(
        {
            "id": uid, "text": text, "start": 0, "end": len(text), "type": "风格",
            "strength": strength, "status": "已落实", "resolved_by": None,
            "mappings": [
                {"field": f, "garment": None, "status": "已落实", "note": "", "rule_id": None}
                for f in fields
            ],
        }
    )


NEW_CUSTOMER = [
    ("u1", "想做一件新中式的外套", ["品类"]),
    ("u2", "我肩膀有点宽，想显得窄一点", ["版型结构"]),
    ("u3", "不要扣子，想干净一点", ["版型结构"]),
    ("u4", "整体想修身", ["版型结构", "松弛余量"]),
    ("u5", "颜色想要红色，但不要太艳", ["色彩"]),
    ("u6", "面料希望带点光泽", ["面料"]),
    ("u7", "最好有云纹", ["装饰纹样"]),
]


def test_from_zero_plan_uses_sim_library_and_matches_wording() -> None:
    """一段案例里没有的新需求：修身、窄肩、无扣、红色、云纹都能在库里找到做法，并配到现货。"""
    from app.pipeline import audit, match

    utterances = [_utt(*row) for row in NEW_CUSTOMER]
    text = "".join(row[1] for row in NEW_CUSTOMER)
    fields, _ = match.build_initial_spec(utterances, text)
    audit.apply_program_rules(utterances, fields)
    audit.recompute_statuses(utterances)

    assert "修身" in fields["版型结构"].value and "无扣" in fields["版型结构"].value
    assert "红色" in fields["色彩"].value and fields["色彩"].resource_ids == ["MCO1"]
    assert fields["装饰纹样"].resource_ids == ["MP1"]
    assert "云纹" in fields["面料"].value
    assert all(u.status == "已落实" for u in utterances)
    ids = [rid for field in fields.values() for rid in field.resource_ids]
    assert knowledge.stock_for(ids)[0]["sku"] == "SIM-0101"


def test_fitted_request_against_loose_plan_is_conflict() -> None:
    """客户要修身、方案写“无收腰、整体宽松”：方向相反，判有冲突（只升级不降级）。"""
    from app.pipeline import audit
    from app.schemas import SpecField

    utterance = _utt("u1", "整体想修身", ["版型结构", "松弛余量"])
    spec = {
        "版型结构": SpecField(value="H 型直身，无收腰", resource_ids=["S1"], utterance_ids=["u1"], origin="资源库", confirmed=False),
        "松弛余量": SpecField(value="整体围度宽松", resource_ids=["S1"], utterance_ids=["u1"], origin="资源库", confirmed=False),
    }
    audit.apply_program_rules([utterance], spec)
    assert {m.status for m in utterance.mappings} == {"有冲突"}
    # “不要修身”不是修身要求
    negated = _utt("u2", "不要修身，宽松点", ["版型结构"])
    audit.apply_program_rules([negated], spec)
    assert negated.mappings[0].status == "已落实"


def test_cloud_pattern_rewritten_when_library_lacks_it(no_sim) -> None:
    """只用案例资源时，“最好有云纹”被配成别的纹样，审计要判被改写，不能算已落实。"""
    from app.pipeline import audit, match

    utterances = [_utt("u1", "外套面料要带暗纹，最好有云纹", ["装饰纹样"])]
    fields, _ = match.build_initial_spec(utterances, utterances[0].text)
    audit.apply_program_rules(utterances, fields)
    assert utterances[0].mappings[0].status in ("被改写", "未落实")
