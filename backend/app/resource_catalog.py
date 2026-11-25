"""Traceable presentation metadata for the static resource catalog.

The source files in ``data/`` remain read-only. This module derives style and
scene labels from the development cases that explicitly mention them, adds a
type-specific usage boundary, and keeps any unsupported field empty.
"""

from __future__ import annotations

from . import knowledge

GROUP_LABELS = {
    "silhouettes": "版型",
    "shoulders": "肩型",
    "collars": "领型",
    "fabrics": "面料",
    "crafts": "工艺",
    "patterns": "纹样",
    "colors": "色彩",
    "lengths": "长度",
    "body_rules": "身形规则",
}

# Only explicit customer expressions from development cases are used. The
# labels describe the source case, not an unproven intrinsic property.
CASE_PROFILES = {
    "c1": {
        "style_tags": ["中式", "端庄", "气质"],
        "scene_tags": ["正式场合"],
        "basis": "案例一口述：中式、端庄、正式场合、气质",
    },
    "c2": {
        "style_tags": ["中式", "优雅"],
        "scene_tags": ["正式场合"],
        "basis": "案例二口述：中式、正式场合、优雅",
    },
    "c3": {
        "style_tags": ["优雅", "明快", "有活力", "正式", "松弛"],
        "scene_tags": ["户外舞台", "演出"],
        "basis": "案例三口述：户外舞台演出、优雅、明快、正式与松弛",
    },
    "c4": {
        "style_tags": ["中式", "内敛", "简约", "低调"],
        "scene_tags": [],
        "basis": "案例四口述：中式、内敛、简约、低调；未明确场景",
    },
    "c6": {
        "style_tags": ["简约大气", "新中式", "正式", "有质感"],
        "scene_tags": ["正式场合"],
        "basis": "案例六口述：简约大气的新中式、正式、有质感",
    },
}

GROUP_GUIDANCE = {
    "silhouettes": "仅作案例版型方向参考；人体适配、具体松量和纸样由版师量体后确认。",
    "shoulders": "仅作案例肩型方向参考；肩线位置和袖笼活动量需试穿确认。",
    "collars": "仅作案例领型方向参考；领高、领宽和颈部舒适度需设计师及试穿确认。",
    "fabrics": "仅作案例面料方向参考；成分、克重、色差、库存和可采购性均未记录，需找样确认。",
    "crafts": "仅作案例结构或工艺方向参考；成本、可制造性和参数由制版师或样衣工确认。",
    "patterns": "仅作案例纹样方向参考；花型循环、对位和色牢度需以实际面料样确认。",
    "colors": "仅作案例配色方向参考；具体色号、色差和面料适配需以实物确认。",
    "lengths": "仅作案例比例方向参考；最终衣长按身高、用途和量体结果确认。",
    "body_rules": "规则只提示需要留意的身形条件；处理方式和舒适度必须由设计师、版师和试穿共同确认。",
}


def _unique(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return result


def _development_sources(item: dict) -> list[str]:
    dev = knowledge.dev_case_ids()
    return [
        source
        for source in item.get("sources", [])
        if isinstance(source, str) and source in dev
    ]


def _case_classification(sources: list[str]) -> tuple[list[str], list[str], list[str]]:
    styles: list[str] = []
    scenes: list[str] = []
    evidence: list[str] = []
    for source in sources:
        profile = CASE_PROFILES.get(source)
        if not profile:
            continue
        styles.extend(profile["style_tags"])
        scenes.extend(profile["scene_tags"])
        evidence.append(profile["basis"])
    return _unique(styles), _unique(scenes), evidence


def _resource_guidance(group: str, item: dict) -> list[str]:
    guidance = [GROUP_GUIDANCE[group]]
    caution = item.get("caution")
    note = item.get("note")
    if isinstance(caution, str) and caution.strip():
        guidance.append(f"案例提示：{caution.strip()}")
    if isinstance(note, str) and note.strip():
        guidance.append(f"资料说明：{note.strip()}")
    if group == "body_rules":
        condition = item.get("condition")
        treatment = item.get("treatment")
        if isinstance(condition, str) and condition.strip():
            guidance.append(f"触发信息：{condition.strip()}")
        if isinstance(treatment, str) and treatment.strip():
            guidance.append(f"案例处理：{treatment.strip()}")
    return guidance


SIM_GUIDANCE = "模拟库条目：演示用的常见做法，不是上服真实做法库；落地时替换为上服数据。"


def _item_profile(group: str, item: dict) -> dict:
    sources = _development_sources(item)
    styles, scenes, evidence = _case_classification(sources)
    simulated = knowledge.SIM_SOURCE in (item.get("sources") or [])
    if simulated:
        styles = _unique(list(item.get("style_tags") or []))
        scenes = _unique(list(item.get("scene_tags") or []))
        evidence = ["模拟资源库（演示数据）"]
    missing: list[str] = []
    if not styles:
        missing.append("风格标签")
    if not scenes:
        missing.append("场景标签")
    if not sources and not simulated:
        missing.append("开发案例依据")
    public = dict(item)
    if isinstance(item.get("sources"), list):
        public["sources"] = knowledge.visible_sources(item["sources"])
    return {
        **public,
        "group": group,
        "group_label": GROUP_LABELS[group],
        "style_tags": styles,
        "scene_tags": scenes,
        "audience_tags": [],
        "usage_guidance": ([SIM_GUIDANCE] if simulated else []) + _resource_guidance(group, item),
        "simulated": simulated,
        "evidence": evidence,
        "basis_cases": sources,
        "recommendation_eligible": knowledge.is_recommendable(item),
        "missing_metadata": missing,
    }


def build_catalog() -> dict:
    """Return a flat, traceable view without changing the original resource API."""
    document = knowledge.get_resources()
    items: list[dict] = []
    for group, label in GROUP_LABELS.items():
        records = document.get(group, [])
        if not isinstance(records, list):
            continue
        for item in records:
            if not isinstance(item, dict) or not item.get("id"):
                continue
            profile = _item_profile(group, item)
            if not profile.get("name"):
                profile["name"] = profile.get("condition") or label
            items.append(profile)
    return {
        "meta": {
            "source": "根据 data/cases.json 与 data/resources.json 派生的只读分类目录",
            "evidence_policy": "风格和场景只继承客户原话中明确出现的表达；未记录字段保持空白，不猜测。",
            "availability": "案例资源仅为历史案例参考；标“模拟库”的为演示数据。都不代表库存、货号、报价或当前可采购性。",
        },
        "items": items,
        "counts": {
            "total": len(items),
            "recommendable": sum(1 for item in items if item["recommendation_eligible"]),
        },
    }
