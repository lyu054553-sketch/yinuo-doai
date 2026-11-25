"""只读访问 data/ 下的案例、资源和流程数据，并提供资源属性与文本匹配。"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from .config import settings
from .schemas import FIELD_ORDER

DATA_DIR = Path(settings.data_dir)


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def get_cases_doc() -> dict:
    return _read_json(DATA_DIR / "cases.json")


def get_cases() -> list[dict]:
    return get_cases_doc().get("cases", [])


def get_case(case_id: str) -> dict | None:
    for case in get_cases():
        if case.get("id") == case_id:
            return case
    return None


def is_holdout(case: dict) -> bool:
    """留出案例只做通用加载，不参与开发与测试。"""
    return case.get("split") == "holdout"


def is_blocked_sample(case: dict) -> bool:
    """留出评测案例不能出现在示例列表里，也不能用来建演示会话。

    以 split 标注为准，同时按约定的案例五 id 兜底，避免数据缺标注时漏拦。
    """
    return is_holdout(case) or case.get("id") == "c5"


@lru_cache(maxsize=1)
def dev_case_ids() -> frozenset[str]:
    """开发集案例 id。留出案例不在其中，评估环境里也一样。"""
    return frozenset(
        case["id"]
        for case in get_cases()
        if case.get("id") and not is_holdout(case)
    )


# 模拟资源库：补齐案例资源覆盖不到的常见做法，并附一份模拟仓库。来源统一记为 "sim"，
# 界面显示“模拟库”。落地时整份换成上服真实数据（格式不变），或设 YINUO_SIM_LIBRARY=0 关掉。
SIM_SOURCE = "sim"
SIM_PATH = DATA_DIR / "resources_sim.json"


def sim_enabled() -> bool:
    import os

    flag = os.environ.get("YINUO_SIM_LIBRARY", "1").strip().lower()
    return flag not in ("0", "off", "false", "no") and SIM_PATH.exists()


@lru_cache(maxsize=1)
def get_sim_doc() -> dict:
    return _read_json(SIM_PATH) if sim_enabled() else {}


@lru_cache(maxsize=1)
def get_resources() -> dict:
    """案例资源（data/resources.json）+ 模拟资源库。模拟条目排在同类案例资源之后。"""
    import copy

    document = _read_json(DATA_DIR / "resources.json")
    sim = get_sim_doc()
    if not sim:
        return document
    document = copy.deepcopy(document)
    for key, items in (sim.get("resources") or {}).items():
        if isinstance(items, list):
            document.setdefault(key, [])
            document[key] = list(document[key]) + copy.deepcopy(items)
    document["inventory"] = copy.deepcopy(sim.get("inventory") or [])
    meta = dict(document.get("meta") or {})
    meta["simulated"] = sim.get("meta", {})
    document["meta"] = meta
    return document


def inventory() -> list[dict]:
    return list(get_resources().get("inventory") or [])


def stock_for(resource_ids: list[str]) -> list[dict]:
    """方案里用到的面料 / 纹样 / 色彩编号，在（模拟）仓库里对得上的货。

    必须对上面料编号；纹样、色彩对上的越多越靠前。没有面料编号时不硬配。
    """
    ids = set(resource_ids)
    plan_has = {
        "pattern": any(rid.startswith(("P", "MP")) for rid in ids),
        "color": any(rid.startswith(("CO", "MCO")) for rid in ids),
    }
    ranked: list[tuple[int, dict]] = []
    for item in inventory():
        if item.get("fabric") not in ids:
            continue
        # 方案已经定了纹样 / 颜色时，花型或颜色对不上的货不推荐
        if any(plan_has[key] and item.get(key) and item.get(key) not in ids for key in plan_has):
            continue
        score = sum(1 for key in ("pattern", "color") if item.get(key) and item.get(key) in ids)
        mismatch = sum(1 for key in ("pattern", "color") if item.get(key) and item.get(key) not in ids)
        ranked.append((score * 10 - mismatch + (1 if item.get("status") == "有货" else 0), item))
    ranked.sort(key=lambda pair: (-pair[0], str(pair[1].get("sku", ""))))
    return [item for _, item in ranked]


def is_recommendable(resource: dict) -> bool:
    """资源能不能进推荐候选。

    判定只看 sources 元数据：没有 sources 的通用参考保留；至少有一条来源是
    开发集案例的保留；sources 全是留出案例（或数据里查不到的案例）的一律排除，
    否则等于把留出案例的答案提前放进资源库。这里不按案例内容做任何硬编码。
    """
    sources = resource.get("sources")
    if not isinstance(sources, list) or not sources:
        return True
    dev = dev_case_ids()
    return any(src in dev or src == SIM_SOURCE for src in sources)


@lru_cache(maxsize=1)
def get_workflow() -> dict:
    return _read_json(DATA_DIR / "workflow.json")


@lru_cache(maxsize=1)
def get_attrs() -> dict:
    path = Path(__file__).resolve().parent / "knowledge_attrs.json"
    base = _read_json(path) if path.exists() else {"match_keywords": {}, "attrs": {}}
    sim = get_sim_doc()
    if not sim:
        return base
    merged = dict(base)
    for key in ("attrs", "match_keywords"):
        merged[key] = {**(base.get(key) or {}), **(sim.get(key) or {})}
    return merged


# 设计字段 -> resources.json 里的资源集合
FIELD_RESOURCE_KEYS: dict[str, tuple[str, ...]] = {
    "版型结构": ("silhouettes", "shoulders"),
    "领型": ("collars",),
    "面料": ("fabrics",),
    "色彩": ("colors",),
    "装饰纹样": ("patterns",),
    "长度比例": ("lengths",),
}


def resources_for_field(field: str) -> list[dict]:
    """某一设计字段可用的推荐资源，已排除只有留出来源的条目。"""
    doc = get_resources()
    items: list[dict] = []
    for key in FIELD_RESOURCE_KEYS.get(field, ()):
        for item in doc.get(key, []):
            if not isinstance(item, dict):
                continue
            if not is_recommendable(item):
                continue
            items.append(item)
    return items


@lru_cache(maxsize=1)
def _resource_index() -> dict[str, dict]:
    index: dict[str, dict] = {}
    doc = get_resources()
    for key, value in doc.items():
        if not isinstance(value, list):
            continue
        for item in value:
            if isinstance(item, dict) and item.get("id"):
                index[item["id"]] = item
    return index


def resource_by_id(resource_id: str) -> dict | None:
    return _resource_index().get(resource_id)


def resource_label(resource_id: str) -> str:
    item = resource_by_id(resource_id)
    if not item:
        return resource_id
    return f"{resource_id} {item.get('name', '')}".strip()


def attr(resource_id: str) -> dict:
    return get_attrs().get("attrs", {}).get(resource_id, {})


def _keywords(resource_id: str) -> list[str]:
    return get_attrs().get("match_keywords", {}).get(resource_id, [])


def _norm(text: str) -> str:
    return "".join(str(text).split())


def find_resource_ids(field: str, text: str) -> list[str]:
    """把方案里的一栏文字对应到资源 id。对应不上就返回空列表，不硬凑。"""
    if not text:
        return []
    plain = _norm(text)
    ids: list[str] = []
    for item in resources_for_field(field):
        rid = item["id"]
        name = _norm(item.get("name", ""))
        if name and name in plain:
            ids.append(rid)
            continue
        for keyword in _keywords(rid):
            if _norm(keyword) in plain:
                ids.append(rid)
                break
    return ids


CASE_LABELS = {
    "c1": "案例一",
    "c2": "案例二",
    "c3": "案例三",
    "c4": "案例四",
    "c5": "案例五",
    "c6": "案例六",
    "sim": "模拟库",
}


def visible_sources(sources) -> list[str]:
    """对外展示用的来源：只留开发集案例。留出案例（案例五）不出现在任何界面和导出里，
    免得评测前就把它的存在和内容透露出去。"""
    if not isinstance(sources, list):
        return []
    dev = dev_case_ids()
    return [
        item for item in sources if isinstance(item, str) and (item in dev or item == SIM_SOURCE)
    ]


def public_resources() -> dict:
    """资源库的对外副本：每条资源的 sources 只保留开发集案例，其余内容原样。"""
    import copy

    document = copy.deepcopy(get_resources())
    for key, items in document.items():
        if key == "meta" or not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, dict) and isinstance(item.get("sources"), list):
                item["sources"] = visible_sources(item["sources"])
    return document


def sources_label(sources: list[str] | None) -> str:
    labels = [CASE_LABELS.get(item, item) for item in visible_sources(sources)]
    return "、".join(labels) if labels else "未注明来源"


def resource_source_text(resource_id: str) -> str:
    item = resource_by_id(resource_id)
    if not item:
        return "未注明来源"
    return sources_label(item.get("sources"))


def normalize_spec_value(value) -> str:
    """案例里的多件套字段是对象，这里按“外套：……；连衣裙：……”拼成字符串。"""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        parts = []
        for garment in ("外套", "连衣裙"):
            if garment in value:
                parts.append(f"{garment}：{value[garment]}")
        for garment, text in value.items():
            if garment not in ("外套", "连衣裙"):
                parts.append(f"{garment}：{text}")
        return "；".join(parts)
    return str(value)


def case_spec(case: dict) -> dict[str, str]:
    spec = case.get("spec") or {}
    return {field: normalize_spec_value(spec.get(field, "")) for field in FIELD_ORDER}


def garment_names(text: str) -> list[str]:
    names = []
    for name in ("外套", "连衣裙", "内搭", "上衣", "裙"):
        if name in text:
            names.append(name)
    return names


def garment_allowed(resource: dict, spec_values: dict[str, str]) -> bool:
    """连衣裙专用资源不能用在只有外套的方案上，反之亦然。"""
    garment = resource.get("garment")
    if not garment:
        return True
    category = spec_values.get("品类", "")
    return garment in category


def workflow_checks(step_no: int = 5) -> list[str]:
    for step in get_workflow().get("steps", []):
        if step.get("no") == step_no:
            return list(step.get("checks", []))
    return []
