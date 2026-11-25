#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""D2 正式评测的数据层：审批门禁、开发集/注入集标准答案、请求体构造与泄漏防护。

只读 `data/`、`eval/datasets/injected.json` 与 `orchestration/approvals/d1.json`，
不联网、不写 runs/、不修改任何已批准数据。供 run_formal_eval.py 与离线单元测试复用。

重要约定：
- 注入组送给被测系统的只有 `customer_text` 与 `existing_spec`，绝不带标题、类别、
  expected_issues、test_injected、resources 等答案信息（见 assert_payload_clean）。
- 留出集 c5 在加载阶段即拒绝；本模块不会读取其内容。
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from typing import Any, Dict, List

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import run_eval  # noqa: E402  D0 脚手架的纯函数，不导入后端
import validate_injected  # noqa: E402  D1 的答案提示词清单

FIELDS = list(run_eval.FIELDS)
DEV_CASE_IDS = list(run_eval.DEV_CASE_IDS)
HOLDOUT_CASE_ID = run_eval.HOLDOUT_CASE_ID
PROBLEM_STATUSES = set(run_eval.PROBLEM_STATUSES)
EXPECTED_GOLD_PROBLEMS = dict(run_eval.EXPECTED_GOLD_PROBLEMS)

# 请求体只允许出现输入字段
ALLOWED_PAYLOAD_KEYS = {"text", "existing_spec", "sample_id", "title"}
# 任何情况下都不许出现在请求体里的键
FORBIDDEN_PAYLOAD_KEYS = {
    "utterances",
    "expected_issues",
    "test_injected",
    "issue_count",
    "clean",
    "category",
    "resources",
    "normal_control",
    "reason",
    "expected_status",
    "mappings",
    "status",
    "issues",
    "expected",
    "answer",
    "gold",
}
# 答案提示词：只允许留在元数据里，绝不允许跟着请求体发出去
LEAK_TOKENS = list(
    dict.fromkeys(
        list(getattr(validate_injected, "ANSWER_HINTS", []) or [])
        + [
            "expected_issues",
            "test_injected",
            "normal_control",
            "issue_count",
            "资源库中不存在",
            "标准答案",
        ]
    )
)


class FormalDataError(RuntimeError):
    """数据或门禁不满足 D2 要求；出现即拒绝评测。"""


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------

def repo_root() -> str:
    return os.path.dirname(HERE)


def sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: str) -> Any:
    if not os.path.isfile(path):
        raise FormalDataError(f"找不到文件：{path}")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError) as exc:
        raise FormalDataError(f"读取失败：{path}（{exc}）")


def utterance_id(group_id: str, index1: int) -> str:
    return f"{group_id}-u{index1}"


# ---------------------------------------------------------------------------
# 审批门禁：D1 数据必须与审批记录里的 SHA256 完全一致
# ---------------------------------------------------------------------------

def check_approval(approval_path: str, dataset_path: str) -> Dict[str, Any]:
    record = _read_json(approval_path)
    if not isinstance(record, dict):
        raise FormalDataError(f"审批记录格式不对：{approval_path}")

    decision = record.get("decision")
    if decision != "approved":
        raise FormalDataError(f"拒绝评测：D1 数据尚未获批（decision={decision!r}），请先取得项目负责人批准。")
    scope = record.get("scope")
    if scope != "d1_dataset_only":
        raise FormalDataError(f"拒绝评测：审批范围是 {scope!r}，不是 d1_dataset_only。")

    if not os.path.isfile(dataset_path):
        raise FormalDataError(f"找不到已批准的注入集：{dataset_path}")
    actual = sha256_file(dataset_path)
    expected = str(record.get("dataset_sha256") or "")
    if not expected:
        raise FormalDataError("拒绝评测：审批记录缺少 dataset_sha256。")
    if actual != expected:
        raise FormalDataError(
            "拒绝评测：注入集 SHA256 与审批记录不一致"
            f"（审批 {expected[:12]}…，实际 {actual[:12]}…）。"
            "已批准的数据或标准答案被改动，或不是同一份文件；请项目负责人重新确认后再跑。"
        )

    return {
        "approval_path": approval_path,
        "decision": decision,
        "scope": scope,
        "user_message": record.get("user_message"),
        "recorded_at": record.get("recorded_at"),
        "dataset_path_in_record": record.get("dataset_path"),
        "dataset_path_used": dataset_path,
        "dataset_sha256_expected": expected,
        "dataset_sha256_actual": actual,
        "sha256_match": True,
        "scheme_frozen": bool(record.get("scheme_frozen")),
        "holdout_authorized": bool(record.get("holdout_authorized")),
        "review_sha256": record.get("review_sha256"),
    }


# ---------------------------------------------------------------------------
# 注入集：加载、自洽校验、转成与开发集同构的标准答案
# ---------------------------------------------------------------------------

def _check_group_payload(group: Dict[str, Any]) -> None:
    gid = str(group.get("id") or "")
    if not gid:
        raise FormalDataError("注入组缺少 id")
    text = group.get("customer_text")
    if not isinstance(text, str) or not text.strip():
        raise FormalDataError(f"[{gid}] customer_text 缺失或为空")
    spec = group.get("existing_spec")
    if not isinstance(spec, dict):
        raise FormalDataError(f"[{gid}] existing_spec 不是对象")
    missing = [f for f in FIELDS if not isinstance(spec.get(f), str) or not str(spec.get(f)).strip()]
    if missing:
        raise FormalDataError(f"[{gid}] 已有方案字段缺失或为空：{'、'.join(missing)}")
    extra = [k for k in spec.keys() if k not in FIELDS]
    if extra:
        raise FormalDataError(f"[{gid}] 已有方案出现八个字段之外的键：{extra}")


def injected_gold(group: Dict[str, Any]) -> Dict[str, Any]:
    """把注入组标准化成与开发集同构的 gold，供 run_eval.evaluate_session 直接复用。"""
    if not isinstance(group, dict):
        raise FormalDataError("注入组不是对象")
    gid = str(group.get("id") or "")
    _check_group_payload(group)
    text = group["customer_text"]
    spec = group["existing_spec"]

    utterances: List[Dict[str, Any]] = []
    raw_utterances = group.get("utterances")
    if not isinstance(raw_utterances, list) or not raw_utterances:
        raise FormalDataError(f"[{gid}] utterances 缺失或为空")
    for index, item in enumerate(raw_utterances, start=1):
        if not (isinstance(item, (list, tuple)) and len(item) == 4):
            raise FormalDataError(f"[{gid}] 第 {index} 条原话格式应为 [text, type, strength, mappings]")
        utext, utype, ustrength, raw_maps = item
        if not isinstance(utext, str) or not utext or utext not in text:
            raise FormalDataError(f"[{gid}] 第 {index} 条原话不是 customer_text 的子串")
        if not isinstance(raw_maps, list) or not raw_maps:
            raise FormalDataError(f"[{gid}] 第 {index} 条原话没有 mappings")
        mappings: List[Dict[str, Any]] = []
        for raw_map in raw_maps:
            if not (isinstance(raw_map, (list, tuple)) and len(raw_map) == 3):
                raise FormalDataError(f"[{gid}] 第 {index} 条原话的 mapping 格式应为 [field, status, note]")
            field, status, note = raw_map
            if field not in FIELDS:
                raise FormalDataError(f"[{gid}] mapping 字段不在八个字段内：{field}")
            if status not in run_eval.SEVERITY:
                raise FormalDataError(f"[{gid}] mapping 状态不在约定词表内：{status}")
            mappings.append({"field": field, "status": status, "note": note, "garment": None, "rule_id": None})
        utterances.append(
            {
                "id": utterance_id(gid, index),
                "text": utext,
                "type": utype,
                "strength": ustrength,
                "status": run_eval.status_of({"mappings": mappings}),
                "mappings": mappings,
            }
        )

    issues = group.get("expected_issues") or []
    if not isinstance(issues, list):
        raise FormalDataError(f"[{gid}] expected_issues 不是数组")
    if group.get("issue_count") != len(issues):
        raise FormalDataError(f"[{gid}] issue_count 与 expected_issues 条数不一致")
    if bool(group.get("clean")) != (len(issues) == 0):
        raise FormalDataError(f"[{gid}] clean 标记与问题数不一致")

    by_id = {u["id"]: u for u in utterances}
    problem_ids = sorted(u["id"] for u in utterances if u["status"] in PROBLEM_STATUSES)
    issue_ids = sorted(str(it.get("utterance_id")) for it in issues)
    if problem_ids != issue_ids:
        raise FormalDataError(
            f"[{gid}] 标准答案不自洽：问题句 {problem_ids} 与 expected_issues {issue_ids} 不一致"
        )
    for issue in issues:
        uid = str(issue.get("utterance_id"))
        utt = by_id.get(uid)
        if utt is None:
            raise FormalDataError(f"[{gid}] 问题引用了不存在的原话：{uid}")
        if issue.get("text") != utt["text"]:
            raise FormalDataError(f"[{gid}] 问题原文与对应原话不一致：{uid}")
        if not utt["text"] in text:
            raise FormalDataError(f"[{gid}] 问题原文不是 customer_text 的子串")
        if issue.get("field") not in FIELDS:
            raise FormalDataError(f"[{gid}] 问题字段不在八个字段内：{issue.get('field')}")
        if issue.get("expected_status") not in PROBLEM_STATUSES:
            raise FormalDataError(f"[{gid}] 问题标准状态不是四类问题状态：{issue.get('expected_status')}")
        ok = any(
            m["field"] == issue.get("field") and m["status"] == issue.get("expected_status")
            for m in utt["mappings"]
        )
        if not ok:
            raise FormalDataError(f"[{gid}] 问题 {uid} 在 {issue.get('field')} 上没有对应的 {issue.get('expected_status')} 映射")

    return {
        "id": gid,
        "title": group.get("title") or gid,
        "category": group.get("category") or "",
        "text": text,
        "spec": spec,
        "utterances": utterances,
        "expected_issues": issues,
    }


def load_injected_dataset(path: str) -> Dict[str, Any]:
    raw = _read_json(path)
    if not isinstance(raw, dict):
        raise FormalDataError(f"注入集顶层不是对象：{path}")
    groups = raw.get("groups")
    if not isinstance(groups, list) or not groups:
        raise FormalDataError("注入集没有 groups")
    meta = raw.get("meta") if isinstance(raw.get("meta"), dict) else {}

    golds: Dict[str, Dict[str, Any]] = {}
    total_issues = 0
    clean_ids: List[str] = []
    for group in groups:
        if not isinstance(group, dict):
            raise FormalDataError("注入集 groups 中出现非对象条目")
        gid = str(group.get("id") or "")
        if not gid:
            raise FormalDataError("注入组缺少 id")
        if gid == HOLDOUT_CASE_ID or group.get("split") == "holdout" or group.get("holdout") is True:
            raise FormalDataError(f"拒绝加载：注入集出现留出数据（{gid}），D2 不涉及留出集。")
        if gid in golds:
            raise FormalDataError(f"注入组 id 重复：{gid}")
        gold = injected_gold(group)
        golds[gid] = gold
        total_issues += len(gold["expected_issues"])
        if not gold["expected_issues"]:
            clean_ids.append(gid)

    if "expected_total_issues" in meta and int(meta["expected_total_issues"]) != total_issues:
        raise FormalDataError(
            f"注入集问题总数与元数据不一致：实际 {total_issues}，meta 写 {meta['expected_total_issues']}"
        )
    if meta.get("clean_group_ids"):
        if sorted(meta["clean_group_ids"]) != sorted(clean_ids):
            raise FormalDataError(
                f"无问题组与元数据不一致：实际 {clean_ids}，meta 写 {meta['clean_group_ids']}"
            )

    return {
        "path": path,
        "sha256": sha256_file(path),
        "meta": meta,
        "group_ids": [str(g["id"]) for g in groups],
        "gold_by_id": golds,
        "issue_count": total_issues,
        "clean_group_ids": clean_ids,
        "meta_status": str(meta.get("status") or ""),
    }


# ---------------------------------------------------------------------------
# 开发集：只取 c1/c2/c3/c4/c6，问题句数量必须与约定一致
# ---------------------------------------------------------------------------

def load_dev_gold(data_dir: str) -> Dict[str, Dict[str, Any]]:
    try:
        gold = run_eval.load_gold(data_dir, DEV_CASE_IDS)
    except run_eval.EvalError as exc:
        raise FormalDataError(str(exc))
    counts = {
        cid: sum(1 for u in gold[cid]["utterances"] if u.get("status") in PROBLEM_STATUSES)
        for cid in DEV_CASE_IDS
    }
    if counts != EXPECTED_GOLD_PROBLEMS:
        raise FormalDataError(
            f"开发集问题句数量与约定不符：实际 {counts}，预期 {EXPECTED_GOLD_PROBLEMS}"
        )
    return gold


# ---------------------------------------------------------------------------
# 请求体：注入组只发口述与已有方案
# ---------------------------------------------------------------------------

def dev_payload(gold: Dict[str, Any]) -> Dict[str, Any]:
    cid = gold["id"]
    if cid == HOLDOUT_CASE_ID or cid not in DEV_CASE_IDS:
        raise FormalDataError(f"拒绝构造非开发案例的请求体：{cid}")
    return {
        "text": gold["text"],
        "existing_spec": run_eval.normalize_existing_spec(gold["spec"]),
        "sample_id": cid,
        "title": gold.get("title") or "",
    }


def injected_payload(gold: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "text": gold["text"],
        "existing_spec": run_eval.normalize_existing_spec(gold["spec"]),
    }


def _legitimate_input_blob(gold: Dict[str, Any]) -> str:
    """客户口述与已有方案本身：答案文字若已在这里，就不算泄漏（它本来就要发给系统）。"""
    parts = [gold.get("text") or ""]
    spec = run_eval.normalize_existing_spec(gold.get("spec")) or {}
    parts.extend(str(v) for v in spec.values())
    return "\n".join(parts)


def answer_strings(gold: Dict[str, Any]) -> List[str]:
    """标准答案里的说明性文字，绝不允许出现在请求体里。

    会先排除“本来就属于输入”的文字（例如映射说明恰好复述了客户原话“整体宽松”），
    否则会把正常输入误判成泄漏。
    """
    out: List[str] = []
    for utt in gold.get("utterances") or []:
        for mapping in utt.get("mappings") or []:
            note = mapping.get("note")
            if isinstance(note, str) and len(note.strip()) >= 4:
                out.append(note.strip())
    for issue in gold.get("expected_issues") or []:
        if not isinstance(issue, dict):
            continue
        for key in ("reason", "normal_control", "category"):
            value = issue.get(key)
            if isinstance(value, str) and len(value.strip()) >= 4:
                out.append(value.strip())
    # 注入集的标题与类别本身就是答案标签（开发集标题只描述案例，不进这个清单）
    if gold.get("category"):
        for key in ("title", "category"):
            value = gold.get(key)
            if isinstance(value, str) and len(value.strip()) >= 4:
                out.append(value.strip())
    legit = _legitimate_input_blob(gold)
    return [text for text in dict.fromkeys(out) if text not in legit]


def assert_payload_clean(lane: str, subset: str, gold: Dict[str, Any], payload: Dict[str, Any]) -> None:
    """发请求前的硬门禁：只要疑似泄漏答案就抛错，宁可不跑。"""
    if not isinstance(payload, dict):
        raise FormalDataError("请求体不是对象")
    keys = set(payload.keys())
    extra = keys - ALLOWED_PAYLOAD_KEYS
    if extra:
        raise FormalDataError(f"请求体出现非输入字段：{sorted(extra)}（可能泄漏答案）")
    banned = keys & FORBIDDEN_PAYLOAD_KEYS
    if banned:
        raise FormalDataError(f"请求体出现禁止的答案字段：{sorted(banned)}")
    if subset == "injected":
        if payload.get("sample_id") not in (None, ""):
            raise FormalDataError("注入组请求体不得包含 sample_id")
        if "title" in keys:
            raise FormalDataError("注入组请求体不得包含标题（标题含类别答案）")
    if lane == "yinuo":
        if not isinstance(payload.get("text"), str) or not payload["text"].strip():
            raise FormalDataError("请求体缺少客户口述")
        if not isinstance(payload.get("existing_spec"), dict):
            raise FormalDataError("请求体缺少已有方案")

    serialized = json.dumps(payload, ensure_ascii=False)
    for token in LEAK_TOKENS:
        if token and token in serialized:
            raise FormalDataError(f"请求体出现答案提示词「{token}」，拒绝发送")
    for text in answer_strings(gold):
        if text in serialized:
            raise FormalDataError(f"请求体出现标准答案文字「{text[:30]}」，拒绝发送")