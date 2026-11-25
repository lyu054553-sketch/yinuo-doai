#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""衣诺评测指标单元测试（不联网、不启动后端、不写 runs/ 或 reports/）。

覆盖点：
  1. 重叠比例用的是**最长公共子序列**（甲乙丙 vs 甲丁乙戊丙 = 3/5），名字与实现一致。
  2. 拆句配对是“一对一”贪心：同一条原话被重复预测，只能配上一个标准原话。
  3. 留出集 c5 无论以何种参数形式出现都必须被 validate_cases 拒绝。
  4. 不编造检查能抓出虚构原话（非原文子串）与不存在的资源 id，且对干净数据不误报。

用法（由总控执行）：
    python eval/test_metrics.py          # 纯标准库，无需 pytest
    python -m pytest eval/test_metrics.py -q
"""

from __future__ import annotations

import os
import sys
from typing import Any, Dict, List

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import run_eval  # noqa: E402  评测脚本自身，非后端代码

FIELDS = run_eval.FIELDS


# ---------------------------------------------------------------------------
# 1. 重叠比例 = 最长公共子序列 / 较长者长度
# ---------------------------------------------------------------------------

def test_lcs_len_is_subsequence_length():
    # 子串会得到 2（甲乙 或 乙丙），子序列才是 3
    assert run_eval.lcs_len("甲乙丙", "甲丁乙戊丙") == 3
    # 子序列允许跳跃，顺序相关：“丙乙甲”的最长公共子序列只有 1
    assert run_eval.lcs_len("丙乙甲", "甲乙丙") == 1


def test_overlap_ratio_uses_subsequence():
    ratio = run_eval.overlap_ratio("甲乙丙", "甲丁乙戊丙")
    assert abs(ratio - 3.0 / 5.0) < 1e-9, f"期望 3/5，实际 {ratio}"
    # 分母取较长者
    assert abs(run_eval.overlap_ratio("甲丁乙戊丙", "甲乙丙") - 3.0 / 5.0) < 1e-9
    assert run_eval.overlap_ratio("", "甲乙") == 0.0
    assert run_eval.overlap_ratio("甲乙", "") == 0.0


def test_overlap_ratio_threshold_allows_paired():
    preds = [{"id": "p1", "text": "甲乙丙"}]
    golds = [{"id": "g1", "text": "甲丁乙戊丙"}]
    pairs = run_eval.pair_utterances(preds, golds)
    assert len(pairs) == 1
    assert abs(pairs[0]["ratio"] - 0.6) < 1e-9


# ---------------------------------------------------------------------------
# 2. 配对一对一：重复预测同一原话只算一次
# ---------------------------------------------------------------------------

def test_duplicate_predictions_match_one_to_one():
    same = "我想要一件藏青色外套"
    preds = [
        {"id": "p1", "text": same},
        {"id": "p2", "text": same},
    ]
    golds = [{"id": "g1", "text": same}]
    pairs = run_eval.pair_utterances(preds, golds)
    assert len(pairs) == 1, f"重复预测只能配一次，实际配了 {len(pairs)} 次"
    assert pairs[0]["gold_index"] == 0
    assert pairs[0]["pred_index"] in (0, 1)
    # 另一条预测没有被重复计入，因此拆句精确率的分子只有 1
    assert 1 < len(preds)


def test_two_golds_two_preds_matched_once_each():
    preds = [
        {"id": "p1", "text": "领子要立领"},
        {"id": "p2", "text": "面料要真丝"},
    ]
    golds = [
        {"id": "g1", "text": "领子要立领"},
        {"id": "g2", "text": "面料要真丝"},
    ]
    pairs = run_eval.pair_utterances(preds, golds)
    assert len(pairs) == 2
    assert sorted(p["gold_index"] for p in pairs) == [0, 1]
    assert len({p["pred_index"] for p in pairs}) == 2


def test_below_threshold_not_paired():
    preds = [{"id": "p1", "text": "领子"}]
    golds = [{"id": "g1", "text": "我想要一个很宽的立领外套"}]
    assert run_eval.pair_utterances(preds, golds) == []


# ---------------------------------------------------------------------------
# 3. 留出集 c5 必须被拒绝
# ---------------------------------------------------------------------------

def _raises_holdout(raw: str) -> bool:
    try:
        run_eval.validate_cases(raw)
    except run_eval.EvalError as exc:
        return "c5" in str(exc) and "留出集" in str(exc)
    return False


def test_holdout_c5_rejected():
    assert _raises_holdout("c5")
    assert _raises_holdout("c1,c5")
    assert _raises_holdout("c5,c1")
    assert _raises_holdout(" c4 , c5 ")


def test_holdout_not_in_dev_whitelist():
    assert run_eval.HOLDOUT_CASE_ID == "c5"
    assert "c5" not in run_eval.DEV_CASE_IDS


def test_dev_cases_still_accepted():
    assert run_eval.validate_cases("c1,c2") == ["c1", "c2"]
    assert run_eval.validate_cases("c2,c2,c1") == ["c2", "c1"]


# ---------------------------------------------------------------------------
# 4. 不编造：虚构原话与不存在的资源 id 必须检出
# ---------------------------------------------------------------------------

def _blank_fields() -> Dict[str, Any]:
    return {field: {"value": "", "resource_ids": [], "utterance_ids": [], "origin": "已有方案", "confirmed": False} for field in FIELDS}


def _session(text: str, utterances: List[Dict[str, Any]], fields: Dict[str, Any], questions: List[Dict[str, Any]] = None) -> Dict[str, Any]:
    return {
        "id": "s-test",
        "input": {"text": text, "existing_spec": None, "sample_id": None},
        "utterances": utterances,
        "spec_versions": [{"version": 1, "created_at": "2025-01-01T00:00:00", "reason": "测试", "fields": fields}],
        "questions": questions or [],
    }


def test_fabrication_violations_detected():
    text = "我想要一件藏青色外套"
    assert len(text) == 10

    fields = _blank_fields()
    fields["面料"] = {
        "value": "真丝提花",
        "resource_ids": ["ZZ999"],
        "utterance_ids": ["u1"],
        "origin": "无出处",
        "confirmed": False,
    }
    questions = [
        {
            "id": "q1",
            "utterance_ids": ["u1"],
            "field": "面料",
            "title": "面料选哪种？",
            "context": "",
            "why": "",
            "options": [{"id": "o1", "label": "真丝", "detail": "", "resource_ids": ["QQ001"], "patch": {}, "note": "", "todo": None}],
            "answer": None,
        }
    ]
    session = _session(
        text,
        [
            {"id": "u1", "text": "藏青色外套", "start": 5, "end": 10, "type": "色彩", "strength": "软偏好", "status": "已落实", "mappings": [], "resolved_by": None},
            {"id": "u2", "text": "客户说要有暗纹", "start": 0, "end": 7, "type": "纹样", "strength": "描述", "status": "已落实", "mappings": [], "resolved_by": None},
        ],
        fields,
        questions,
    )

    result = run_eval.check_fabrication(session, valid_ids={"S1"})
    types = {v.get("type") for v in result["violations"]}
    assert "原话不是原文子串" in types, f"虚构原话未检出：{types}"
    assert "资源 id 不在 resources.json 中" in types, f"不存在资源 id 未检出：{types}"
    assert "选项引用了不存在的资源 id" in types, f"选项里的假资源 id 未检出：{types}"
    # 无出处栏数
    assert result["ungrounded_fields"] == ["面料"]
    assert result["latest_version"] == 1

    bad_utt = [v for v in result["violations"] if v.get("type") == "原话不是原文子串"]
    assert bad_utt and bad_utt[0]["utterance_id"] == "u2"

    bad_res = [v for v in result["violations"] if v.get("type") == "资源 id 不在 resources.json 中"]
    assert bad_res and bad_res[0]["field"] == "面料" and bad_res[0]["resource_id"] == "ZZ999"


def test_position_mismatch_detected():
    text = "我想要一件藏青色外套"
    session = _session(
        text,
        [{"id": "u1", "text": "藏青色外套", "start": 0, "end": 5, "type": "色彩", "strength": "软偏好", "status": "已落实", "mappings": [], "resolved_by": None}],
        _blank_fields(),
    )
    types = {v.get("type") for v in run_eval.check_fabrication(session, valid_ids=set())["violations"]}
    assert "原话位置与原文不符" in types


def test_clean_session_no_violation():
    text = "我想要一件藏青色外套"
    fields = _blank_fields()
    fields["色彩"] = {
        "value": "藏青色",
        "resource_ids": ["S1"],
        "utterance_ids": ["u1"],
        "origin": "资源库",
        "confirmed": False,
    }
    session = _session(
        text,
        [{"id": "u1", "text": "藏青色外套", "start": 5, "end": 10, "type": "色彩", "strength": "软偏好", "status": "已落实", "mappings": [], "resolved_by": None}],
        fields,
    )
    result = run_eval.check_fabrication(session, valid_ids={"S1"})
    assert result["violations"] == [], f"干净数据被误报：{result['violations']}"
    assert result["ungrounded_fields"] == []


# ---------------------------------------------------------------------------
# 简易运行器（无 pytest 时也能跑）
# ---------------------------------------------------------------------------

def main() -> int:
    tests = [(name, obj) for name, obj in sorted(globals().items()) if name.startswith("test_") and callable(obj)]
    failed = []
    for name, fn in tests:
        try:
            fn()
        except AssertionError as exc:
            failed.append(name)
            print(f"FAIL  {name}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failed.append(name)
            print(f"ERROR {name}: {type(exc).__name__}: {exc}")
        else:
            print(f"PASS  {name}")
    print("-" * 60)
    print(f"共 {len(tests)} 项，通过 {len(tests) - len(failed)} 项，失败 {len(failed)} 项")
    if failed:
        print("失败项：" + "、".join(failed))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())