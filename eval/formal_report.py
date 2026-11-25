#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""D2 汇总与报告：把逐个 (lane, case, repeat) 的结果聚合成分子/分母，并生成中文报告。

统计口径（与任务 D 一致）：
- 问题检出分母 = 计划单位里的问题句总数（问题句数 × 计划重复次数），
  **失败的重复不剔除**；成功项才可能计入分子。
- 拆句/字段/状态只在成功的单位上统计，并在报告里单独写明成功与失败数。
- 误报按标准状态拆分：标准“已落实”被报成问题，与标准“间接体现”被报成问题，
  两者分开计数，不与问题检出混在一起。
- 对照组只比较问题检出，不比较拆句召回/精确率。
"""

from __future__ import annotations

import statistics
from typing import Any, Dict, List, Optional, Tuple

LANES = ("yinuo", "baseline")
SUBSETS = ("dev", "injected")
LANE_LABEL = {"yinuo": "衣诺（完整系统）", "baseline": "通用问法对照组"}
SUBSET_LABEL = {"dev": "开发集（案例集）", "injected": "注入问题集"}

SEVERE_TEXT_TYPES = {"原话不是原文子串", "原话位置与原文不符", "原话缺少合法位置", "引文不是原文子串（改写或概括）"}
SEVERE_RESOURCE_TYPES = {"资源 id 不在 resources.json 中", "选项引用了不存在的资源 id"}
MINOR_TYPES = {"字段/条目不合法", "条目不是对象"}


def frac(n: int, d: int) -> str:
    return f"{n}/{d}"


def pct(n: int, d: int) -> str:
    if d <= 0:
        return "—"
    return f"{100.0 * n / d:.1f}%"


def fmt(n: int, d: int) -> str:
    return f"{n}/{d}（{pct(n, d)}）"


def _mean(values: List[float]) -> Optional[float]:
    return round(sum(values) / len(values), 1) if values else None


def _fp_split(false_positives: List[Dict[str, Any]]) -> Dict[str, int]:
    counts = {"已落实": 0, "间接体现": 0, "其他": 0}
    for fp in false_positives or []:
        status = fp.get("gold_status")
        if status == "已落实":
            counts["已落实"] += 1
        elif status == "间接体现":
            counts["间接体现"] += 1
        else:
            counts["其他"] += 1
    return counts


def _yinuo_metrics(unit: Dict[str, Any]) -> Dict[str, Any]:
    return unit["metrics"] or {}


def empty_stat() -> Dict[str, Any]:
    return {
        "units_planned": 0,
        "units_success": 0,
        "units_definite_failure": 0,
        "units_unknown_failure": 0,
        "gold_problems": 0,
        "detected": 0,
        "gold_problems_success": 0,
        "fp_total": 0,
        "fp_已落实": 0,
        "fp_间接体现": 0,
        "fp_其他": 0,
        "seg_paired": 0,
        "seg_gold": 0,
        "seg_pred": 0,
        "field_match": 0,
        "field_gold": 0,
        "field_pred": 0,
        "status_paired": 0,
        "status_agree": 0,
        "status_loose_agree": 0,
        "fabrication_total": 0,
        "severe_text": 0,
        "severe_resource": 0,
        "ungrounded_fields": 0,
        "ungrounded_units": 0,
        "claims_unmatched": 0,
        "baseline_items": 0,
        "baseline_invalid_items": 0,
        "baseline_field_agree": 0,
        "latencies": [],
    }


def aggregate(units: List[Dict[str, Any]], planned: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """units 为已分析的单位；planned 为计划单位（带 gold_problem_count）。"""
    stats: Dict[str, Dict[str, Any]] = {lane: {subset: empty_stat() for subset in SUBSETS} for lane in LANES}

    keyed = {(u["lane"], u["subset"], u["case_id"], u["repeat"]): u for u in units}
    for plan in planned:
        lane, subset = plan["lane"], plan["subset"]
        st = stats[lane][subset]
        st["units_planned"] += 1
        st["gold_problems"] += int(plan.get("gold_problem_count") or 0)
        unit = keyed.get((lane, subset, plan["case_id"], plan["repeat"]))
        if unit is None:
            st["units_unknown_failure"] += 1
            continue
        if not unit.get("ok"):
            if unit.get("outcome") == "unknown":
                st["units_unknown_failure"] += 1
            else:
                st["units_definite_failure"] += 1
            continue

        st["units_success"] += 1
        metrics = unit["metrics"] or {}
        if lane == "yinuo":
            problem = metrics.get("problem") or {}
            st["gold_problems_success"] += int(problem.get("gold_problem_count") or 0)
            st["detected"] += int(problem.get("detected") or 0)
            seg = metrics.get("seg") or {}
            st["seg_gold"] += int(seg.get("gold") or 0)
            st["seg_pred"] += int(seg.get("pred") or 0)
            st["seg_paired"] += int(seg.get("paired") or 0)
            field = metrics.get("field") or {}
            st["field_gold"] += int(field.get("gold_fields") or 0)
            st["field_pred"] += int(field.get("pred_fields") or 0)
            st["field_match"] += int(field.get("match") or 0)
            status = metrics.get("status") or {}
            st["status_paired"] += int(status.get("paired") or 0)
            st["status_agree"] += int(status.get("agree") or 0)
            st["status_loose_agree"] += int(status.get("loose_agree") or 0)
            fabrication = metrics.get("fabrication") or {}
            violations = fabrication.get("violations") or []
            ungrounded = fabrication.get("ungrounded_fields") or []
            st["ungrounded_fields"] += len(ungrounded)
            if ungrounded:
                st["ungrounded_units"] += 1
        else:
            problem = metrics or {}
            st["gold_problems_success"] += int(problem.get("gold_problem_count") or 0)
            st["detected"] += int(problem.get("detected") or 0)
            st["baseline_items"] += int(problem.get("items_count") or 0)
            st["baseline_invalid_items"] += int(problem.get("invalid_items") or 0)
            st["baseline_field_agree"] += int(problem.get("field_agree") or 0)
            st["claims_unmatched"] += len(problem.get("claims_unmatched") or [])
            violations = list(problem.get("fabricated_texts") or [])
            violations += [
                {"type": "字段/条目不合法", "error": e.get("error")} for e in (problem.get("schema_errors") or [])
            ]
            ungrounded = []  # 对照组不产出八字段方案，无“无出处栏”

        fp_counts = _fp_split(
            (metrics.get("problem") or {}).get("false_positives") if lane == "yinuo" else metrics.get("false_positives")
        )
        st["fp_total"] += sum(fp_counts.values())
        st["fp_已落实"] += fp_counts["已落实"]
        st["fp_间接体现"] += fp_counts["间接体现"]
        st["fp_其他"] += fp_counts["其他"]

        st["fabrication_total"] += len(violations)
        for violation in violations:
            vtype = violation.get("type")
            if vtype in SEVERE_RESOURCE_TYPES:
                st["severe_resource"] += 1
            elif vtype in SEVERE_TEXT_TYPES:
                st["severe_text"] += 1
        if isinstance(unit.get("elapsed_ms"), (int, float)):
            st["latencies"].append(float(unit["elapsed_ms"]))
    return stats


def aggregate_latency(stats: Dict[str, Any]) -> Dict[str, Any]:
    values = stats.get("latencies") or []
    return {
        "count": len(values),
        "mean_ms": _mean(values),
        "min_ms": min(values) if values else None,
        "max_ms": max(values) if values else None,
    }


# ---------------------------------------------------------------------------
# 明细：逐句、稳定性、无出处
# ---------------------------------------------------------------------------

def gold_order(gold: Dict[str, Any]) -> List[str]:
    return [u["id"] for u in gold["utterances"] if u.get("status") in ("有冲突", "被改写", "未落实", "需设计师判断")]


def unit_index(units: List[Dict[str, Any]]) -> Dict[Tuple[str, str, str, int], Dict[str, Any]]:
    return {(u["lane"], u["subset"], u["case_id"], u["repeat"]): u for u in units}


def problem_rows_for(units: List[Dict[str, Any]], lane: str, subset: str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for unit in units:
        if unit["lane"] != lane or unit["subset"] != subset or not unit.get("ok"):
            continue
        metrics = unit.get("metrics") or {}
        source = (metrics.get("problem") or {}).get("rows") if lane == "yinuo" else metrics.get("rows")
        for row in source or []:
            entry = dict(row)
            entry["lane"] = lane
            entry["subset"] = subset
            entry["case_id"] = unit["case_id"]
            entry["repeat"] = unit["repeat"]
            rows.append(entry)
    return rows


def stability_for(units: List[Dict[str, Any]], lane: str, subset: str, case_id: str,
                  repeats: int) -> Dict[str, Any]:
    index = unit_index(units)
    signatures: List[Dict[str, Any]] = []
    for repeat in range(1, repeats + 1):
        unit = index.get((lane, subset, case_id, repeat))
        if unit is None:
            signatures.append({"repeat": repeat, "ok": False, "outcome": "missing", "signature": "无记录"})
            continue
        if not unit.get("ok"):
            signatures.append(
                {"repeat": repeat, "ok": False, "outcome": unit.get("outcome"), "signature": f"失败（{unit.get('outcome')}）"}
            )
            continue
        metrics = unit.get("metrics") or {}
        if lane == "yinuo":
            rows = (metrics.get("problem") or {}).get("rows") or []
            detected = tuple(sorted((r.get("gold_utterance_id"), bool(r.get("detected"))) for r in rows))
            fps = tuple(sorted(str(fp.get("gold_utterance_id")) for fp in (metrics.get("problem") or {}).get("false_positives") or []))
        else:
            rows = metrics.get("rows") or []
            detected = tuple(sorted((r.get("gold_utterance_id"), bool(r.get("detected"))) for r in rows))
            fps = tuple(sorted(str(fp.get("gold_utterance_id")) for fp in metrics.get("false_positives") or []))
        signatures.append(
            {
                "repeat": repeat,
                "ok": True,
                "outcome": "success",
                "detected": dict(detected),
                "fp_ids": list(fps),
                "signature": (detected, fps),
            }
        )
    all_ok = all(s.get("ok") for s in signatures)
    unique = {s.get("signature") for s in signatures}
    consistent = bool(all_ok and len(unique) == 1)
    if not all_ok:
        status = "有失败，不能判定一致"
    elif consistent:
        status = "一致"
    else:
        status = "不一致"
    return {"signatures": signatures, "consistent": consistent, "status": status}


def ungrounded_rows(units: List[Dict[str, Any]], subset: str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for unit in units:
        if unit.get("lane") != "yinuo" or unit.get("subset") != subset or not unit.get("ok"):
            continue
        fabrication = (unit.get("metrics") or {}).get("fabrication") or {}
        rows.append(
            {
                "case_id": unit["case_id"],
                "repeat": unit["repeat"],
                "fields": fabrication.get("ungrounded_fields") or [],
                "count": len(fabrication.get("ungrounded_fields") or []),
            }
        )
    return rows


def all_violations(units: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for unit in units:
        if not unit.get("ok"):
            continue
        metrics = unit.get("metrics") or {}
        if unit["lane"] == "yinuo":
            violations = ((metrics.get("fabrication") or {}).get("violations")) or []
        else:
            violations = list(metrics.get("fabricated_texts") or [])
            violations += [
                {"type": "字段/条目不合法", "error": e.get("error"), "item_index": e.get("item_index")}
                for e in (metrics.get("schema_errors") or [])
            ]
        for violation in violations:
            entry = dict(violation)
            entry.update({"lane": unit["lane"], "subset": unit["subset"], "case_id": unit["case_id"], "repeat": unit["repeat"]})
            out.append(entry)
    return out


# ---------------------------------------------------------------------------
# Markdown 报告
# ---------------------------------------------------------------------------

def _table(lines: List[str], header: List[str], rows: List[List[str]]) -> None:
    lines.append("| " + " | ".join(header) + " |")
    lines.append("|" + "---|" * len(header))
    for row in rows:
        lines.append("| " + " | ".join(str(cell) for cell in row) + " |")


def build_report(summary: Dict[str, Any]) -> str:
    env = summary.get("environment") or {}
    stats = summary.get("stats") or {}
    units = summary.get("units") or []
    planned = summary.get("planned") or []
    golds: Dict[str, Dict[str, Any]] = summary.get("golds") or {}
    failures = summary.get("failures") or []
    repeats = int(env.get("repeats") or 0)
    lanes = summary.get("lanes") or list(LANES)
    subsets = summary.get("subsets") or list(SUBSETS)
    index = unit_index(units)

    yinuo_dev = stats.get("yinuo", {}).get("dev", empty_stat())
    yinuo_inj = stats.get("yinuo", {}).get("injected", empty_stat())
    base_dev = stats.get("baseline", {}).get("dev", empty_stat())
    base_inj = stats.get("baseline", {}).get("injected", empty_stat())

    lines: List[str] = []
    lines.append(f"# 衣诺 D2 正式评测报告（{summary.get('run_id')}）")
    lines.append("")
    lines.append("**一句话结论：** " + summary.get("headline", "见下方分项数字。"))
    lines.append("")
    lines.append(
        "> 本报告只陈述本次实际运行的数字与失败，不写对外宣传口径；对外结论需项目负责人另定。"
        "所有比例均给出分子/分母。失败的重复保留在分母里，不悄悄剔除。"
    )
    lines.append("")

    # 1. 运行环境
    lines.append("## 1. 运行环境与门禁")
    lines.append("")
    rows = [
        ["运行编号", f"`{summary.get('run_id')}`"],
        ["开始/结束时间", f"{summary.get('started_at')} ~ {summary.get('finished_at')}"],
        ["衣诺后端", f"`{env.get('base_url')}`"],
        ["后端 mock 模式", "否（0）" if env.get("mock_mode") is False else f"{env.get('mock_mode')}（必须为否）"],
        ["后端 health.llm_model", f"`{env.get('health_model')}`"],
        ["要求的模型名（--model）", f"`{env.get('model')}`"],
        ["模型名一致", "是" if env.get("model_matches") else "否"],
        ["生图 provider", f"`{env.get('image_provider')}`"],
        ["视觉服务", f"`{env.get('vision_ok')}`"],
        ["衣诺会话 source", "、".join(env.get("session_sources") or []) or "—"],
        ["衣诺会话创建时间范围", " ~ ".join(env.get("session_created_range") or []) or "—"],
        ["对照组返回模型名", "、".join(env.get("baseline_models_returned") or []) or "—"],
        ["通用问法地址", f"`{env.get('baseline_endpoint')}`"],
        ["对照组 API Key", f"来自环境变量 `{env.get('api_key_env') or '（未设置）'}`" if env.get("api_key_present") else "未设置（本机服务无鉴权）"],
        ["每个案例重复次数", str(repeats)],
        ["并发数", str(env.get("concurrency"))],
        ["单次超时（秒）", str(env.get("timeout"))],
        ["计划/完成/失败单位", f"{summary.get('planned_count')} / {summary.get('success_count')} / {summary.get('failure_count')}"],
        ["注入集 SHA256", f"`{(env.get('dataset_sha256') or '')[:16]}…`"],
        ["审批记录", f"`{env.get('approval_path')}`（decision=approved，recorded_at={env.get('approval_recorded_at')}）"],
        ["审批哈希匹配", "是" if env.get("approval_sha256_match") else "否"],
        ["方案冻结/留出授权", f"{env.get('scheme_frozen')} / {env.get('holdout_authorized')}"],
        ["留出集 c5", "未运行（runner 硬性拒绝）"],
    ]
    _table(lines, ["项目", "值"], rows)
    lines.append("")
    lines.append(
        "> 说明：注入集 `meta.status` 若仍写作 `pending_review`，是数据文件未改的旧标签；"
        "门禁以审批记录 `orchestration/approvals/d1.json` 与数据 SHA256 一致为准，审批记录与数据均未被本 runner 修改。"
    )
    lines.append("")

    # 2. 样本
    lines.append("## 2. 样本与标准答案")
    lines.append("")
    rows = [
        ["开发集案例", "、".join(env.get("dev_case_ids") or [])],
        ["开发集问题句", f"{env.get('dev_issue_count')} 句（案例二 3、案例三/四/六各 1、案例一 0）"],
        ["注入组数", f"{env.get('injected_group_count')} 组"],
        ["注入问题数", f"{env.get('injected_issue_count')} 个"],
        ["注入无问题组", "、".join(env.get("injected_clean_groups") or []) or "—"],
        ["计划单位（lane×案例×重复）", str(summary.get("planned_count"))],
    ]
    _table(lines, ["项目", "值"], rows)
    lines.append("")
    lines.append(
        "> 注入组只把 `customer_text` 与 `existing_spec` 发给被测系统；标题、类别、"
        "`expected_issues`、`test_injected`、`resources` 等答案信息一律不发送，发送前有硬门禁校验。"
    )
    lines.append("")

    # 3. 主表
    lines.append("## 3. 指标主表（分子/分母）")
    lines.append("")
    header = ["指标", "衣诺·开发集", "衣诺·注入集", "对照组·开发集", "对照组·注入集"]
    rows = [
        [
            "问题句检出（计划分母，失败不剔除）",
            fmt(yinuo_dev["detected"], yinuo_dev["gold_problems"]),
            fmt(yinuo_inj["detected"], yinuo_inj["gold_problems"]),
            fmt(base_dev["detected"], base_dev["gold_problems"]),
            fmt(base_inj["detected"], base_inj["gold_problems"]),
        ],
        [
            "问题句检出（仅成功重复）",
            fmt(yinuo_dev["detected"], yinuo_dev["gold_problems_success"]),
            fmt(yinuo_inj["detected"], yinuo_inj["gold_problems_success"]),
            fmt(base_dev["detected"], base_dev["gold_problems_success"]),
            fmt(base_inj["detected"], base_inj["gold_problems_success"]),
        ],
        [
            "误报：标准“已落实”被报成问题",
            str(yinuo_dev["fp_已落实"]),
            str(yinuo_inj["fp_已落实"]),
            str(base_dev["fp_已落实"]),
            str(base_inj["fp_已落实"]),
        ],
        [
            "误报：标准“间接体现”被报成问题（单列）",
            str(yinuo_dev["fp_间接体现"]),
            str(yinuo_inj["fp_间接体现"]),
            str(base_dev["fp_间接体现"]),
            str(base_inj["fp_间接体现"]),
        ],
        [
            "状态完全一致",
            fmt(yinuo_dev["status_agree"], yinuo_dev["status_paired"]),
            fmt(yinuo_inj["status_agree"], yinuo_inj["status_paired"]),
            "不适用",
            "不适用",
        ],
        [
            "状态宽松一致（间接体现≈已落实）",
            fmt(yinuo_dev["status_loose_agree"], yinuo_dev["status_paired"]),
            fmt(yinuo_inj["status_loose_agree"], yinuo_inj["status_paired"]),
            "不适用",
            "不适用",
        ],
        [
            "拆句召回（只算成功重复）",
            fmt(yinuo_dev["seg_paired"], yinuo_dev["seg_gold"]),
            fmt(yinuo_inj["seg_paired"], yinuo_inj["seg_gold"]),
            "不适用",
            "不适用",
        ],
        [
            "拆句精确（只算成功重复）",
            fmt(yinuo_dev["seg_paired"], yinuo_dev["seg_pred"]),
            fmt(yinuo_inj["seg_paired"], yinuo_inj["seg_pred"]),
            "不适用",
            "不适用",
        ],
        [
            "字段召回（只算成功重复）",
            fmt(yinuo_dev["field_match"], yinuo_dev["field_gold"]),
            fmt(yinuo_inj["field_match"], yinuo_inj["field_gold"]),
            "不适用",
            "不适用",
        ],
        [
            "字段精确（只算成功重复）",
            fmt(yinuo_dev["field_match"], yinuo_dev["field_pred"]),
            fmt(yinuo_inj["field_match"], yinuo_inj["field_pred"]),
            "不适用",
            "不适用",
        ],
        [
            "成功 / 计划单位",
            f"{yinuo_dev['units_success']}/{yinuo_dev['units_planned']}",
            f"{yinuo_inj['units_success']}/{yinuo_inj['units_planned']}",
            f"{base_dev['units_success']}/{base_dev['units_planned']}",
            f"{base_inj['units_success']}/{base_inj['units_planned']}",
        ],
        [
            "失败（确定 / 状态未知）",
            f"{yinuo_dev['units_definite_failure']} / {yinuo_dev['units_unknown_failure']}",
            f"{yinuo_inj['units_definite_failure']} / {yinuo_inj['units_unknown_failure']}",
            f"{base_dev['units_definite_failure']} / {base_dev['units_unknown_failure']}",
            f"{base_inj['units_definite_failure']} / {base_inj['units_unknown_failure']}",
        ],
    ]
    _table(lines, header, rows)
    lines.append("")
    lines.append(
        "> 对照组只比较问题检出：它只输出问题清单，拿不到完整拆句结果，"
        "拆句/字段/状态指标一律写“不适用”，不与衣诺的完整系统指标混比。"
        "对照组的检出判定为：其问题条目的 text 与某句标准问题句字符重叠 ≥ 0.5 即算检出（对对照组往宽处算）。"
    )
    lines.append("")
    lines.append(
        "> 若某列显示 `0/0（—）`，且该赛道/子集未出现在本次 `lanes`/`subsets`，表示本次未运行，不是“零检出”。"
    )
    lines.append("")

    # 4. 分案例 / 分字段统计略过，进入逐句核对
    lines.append("## 4. 逐句核对（全部赛道与子集）")
    lines.append("")
    detail_no = 0
    for lane in ("yinuo", "baseline"):
        if lane not in lanes:
            continue
        for subset in ("dev", "injected"):
            if subset not in subsets:
                continue
            detail_no += 1
            lines.append(f"### 4.{detail_no} {LANE_LABEL[lane]} · {SUBSET_LABEL[subset]}")
            lines.append("")
            case_ids = env.get("dev_case_ids") if subset == "dev" else env.get("injected_group_ids")
            case_ids = case_ids or []
            total_rows = 0
            for case_id in case_ids:
                gold = golds.get(case_id)
                if not gold:
                    continue
                order = gold_order(gold)
                if not order:
                    continue
                lines.append(f"**{case_id}**（{gold.get('title') or ''}）")
                lines.append("")
                header = ["标准原话", "标准状态", "标准字段"]
                header += [f"第 {r} 次" for r in range(1, repeats + 1)]
                table_rows: List[List[str]] = []
                for uid in order:
                    utt = next(u for u in gold["utterances"] if u["id"] == uid)
                    cells = [str(utt.get("text")), str(utt.get("status")), "、".join(
                        [m["field"] for m in utt.get("mappings") or []]) or "—"]
                    for repeat in range(1, repeats + 1):
                        unit = index.get((lane, subset, case_id, repeat))
                        if unit is None:
                            cells.append("无记录")
                            continue
                        if not unit.get("ok"):
                            cells.append(f"失败（{unit.get('outcome')}）")
                            continue
                        metrics = unit.get("metrics") or {}
                        source = (metrics.get("problem") or {}).get("rows") if lane == "yinuo" else metrics.get("rows")
                        row = next((r for r in source or [] if r.get("gold_utterance_id") == uid), None)
                        if row is None:
                            cells.append("未产出对应行")
                        elif not row.get("detected"):
                            cells.append("✗ 漏检")
                        elif lane == "yinuo":
                            cells.append(f"✓ 检出（{row.get('pred_status')}）")
                        else:
                            mark = "✓" if row.get("field_match") else "✓（字段不符）"
                            cells.append(f"{mark} 检出")
                    table_rows.append(cells)
                    total_rows += 1
                _table(lines, header, table_rows)
                lines.append("")
            lines.append(f"> 本块标准问题句共 {total_rows} 句。"
                         f"判定：标准状态属于 {{有冲突, 被改写, 未落实, 需设计师判断}} 即问题句；"
                         f"衣诺要求与之配对的预测原话状态也属于这四类；对照组要求其问题条目与标准问题句配对成功。")
            lines.append("")

    # 6. 误报
    lines.append("## 5. 误报清单")
    lines.append("")
    any_fp = False
    for lane in lanes:
        for subset in subsets:
            fps = []
            for unit in units:
                if unit["lane"] != lane or unit["subset"] != subset or not unit.get("ok"):
                    continue
                metrics = unit.get("metrics") or {}
                source = (metrics.get("problem") or {}).get("false_positives") if lane == "yinuo" else metrics.get("false_positives")
                for fp in source or []:
                    entry = dict(fp)
                    entry.update({"case_id": unit["case_id"], "repeat": unit["repeat"]})
                    fps.append(entry)
            if not fps:
                continue
            any_fp = True
            lines.append(f"### {LANE_LABEL[lane]} · {SUBSET_LABEL[subset]}（{len(fps)} 次）")
            lines.append("")
            rows = []
            for fp in fps:
                rows.append([
                    fp.get("case_id"),
                    fp.get("repeat"),
                    fp.get("gold_text") or "",
                    fp.get("gold_status") or "",
                    fp.get("pred_text") or fp.get("item_text") or "",
                    fp.get("pred_status") or fp.get("item_field") or "",
                    "标准已落实" if fp.get("gold_status") == "已落实" else ("标准间接体现（单列）" if fp.get("gold_status") == "间接体现" else "其他"),
                ])
            _table(lines, ["案例", "重复", "标准原话", "标准状态", "预测原话/条目", "预测状态/字段", "误报类型"], rows)
            lines.append("")
    if not any_fp:
        lines.append("本次没有误报（标准答案非问题句、被报成问题状态的记录为 0）。")
        lines.append("")

    # 7. 对照组的未匹配主张
    if "baseline" in lanes:
        lines.append("## 6. 对照组未匹配的问题主张")
        lines.append("")
        lines.append(
            "这些是通用问法提出、但无法与任何标准原话配对（重叠 < 0.5）的问题主张。"
            "它们不算误报（没有对应到某句标准原话），单独列出，供判断对照组是否在“自由发挥”。"
        )
        lines.append("")
        rows = []
        for unit in units:
            if unit["lane"] != "baseline" or not unit.get("ok"):
                continue
            for claim in (unit.get("metrics") or {}).get("claims_unmatched") or []:
                rows.append([
                    unit["case_id"],
                    unit["repeat"],
                    claim.get("item_index"),
                    (claim.get("text") or "")[:60],
                    claim.get("field") or "",
                    "原文子串" if claim.get("substring_ok") else "非原文子串",
                ])
        if rows:
            _table(lines, ["案例", "重复", "条目", "主张原话（截断）", "字段", "引文"], rows)
        else:
            lines.append("无。")
        lines.append("")

    # 8. 不编造
    lines.append("## 7. 不编造与“无出处”")
    lines.append("")
    violations = all_violations(units)
    severe_text = [v for v in violations if v.get("type") in SEVERE_TEXT_TYPES]
    severe_res = [v for v in violations if v.get("type") in SEVERE_RESOURCE_TYPES]
    other = [v for v in violations if v not in severe_text and v not in severe_res]
    lines.append(f"- 严重：原话/引文虚构或位置错误 —— {len(severe_text)} 条")
    lines.append(f"- 严重：资源 id 不存在 —— {len(severe_res)} 条")
    lines.append(f"- 其他：条目/字段不合法等 —— {len(other)} 条")
    lines.append("")
    if violations:
        rows = []
        for v in violations:
            rows.append([
                v.get("lane"),
                v.get("case_id"),
                v.get("repeat"),
                v.get("type"),
                v.get("field") or v.get("utterance_id") or v.get("item_index") or "",
                v.get("resource_id") or (v.get("text") or "")[:40] or v.get("error") or "",
            ])
        _table(lines, ["赛道", "案例", "重复", "类型", "字段/原话/条目", "细节"], rows)
    else:
        lines.append("违规 0 条。")
    lines.append("")
    lines.append("“无出处”栏数（仅衣诺方案，按每次成功重复统计）：")
    lines.append("")
    for subset in subsets:
        rows = ungrounded_rows(units, subset)
        if not rows:
            continue
        lines.append(f"**{SUBSET_LABEL[subset]}**")
        lines.append("")
        table_rows = [[r["case_id"], r["repeat"], "、".join(r["fields"]) or "无", r["count"]] for r in rows]
        _table(lines, ["案例", "重复", "无出处字段", "栏数"], table_rows)
        lines.append("")

    # 9. 稳定性
    lines.append("## 8. 稳定性（同一案例多次重复的问题检出是否一致）")
    lines.append("")
    rows = []
    for lane in lanes:
        for subset in subsets:
            if subset not in subsets:
                continue
            case_ids = env.get("dev_case_ids") if subset == "dev" else env.get("injected_group_ids")
            for case_id in case_ids or []:
                item = stability_for(units, lane, subset, case_id, repeats)
                detail = []
                for sig in item["signatures"]:
                    if sig.get("ok"):
                        detected = sig.get("detected") or {}
                        detail.append(
                            "第{}次：{}".format(
                                sig["repeat"],
                                "，".join(f"{uid}={'✓' if ok else '✗'}" for uid, ok in detected.items()) or "无标准问题句",
                            )
                        )
                    else:
                        detail.append(f"第{sig['repeat']}次：失败（{sig.get('outcome')}）")
                rows.append([LANE_LABEL[lane], SUBSET_LABEL[subset], case_id, "；".join(detail), item["status"]])
    _table(lines, ["赛道", "子集", "案例", "各次检出", "结论"], rows)
    lines.append("")
    lines.append(
        "> 只有成功跑完所有重复且逐句检出结果完全相同，才标记“一致”。"
        "有失败时结论写“有失败，不能判定一致”，不把缺失当一致。"
    )
    lines.append("")

    # 10. 耗时
    lines.append("## 9. 耗时")
    lines.append("")
    rows = []
    for lane in lanes:
        for subset in subsets:
            latency = aggregate_latency(stats.get(lane, {}).get(subset, empty_stat()))
            rows.append([
                LANE_LABEL[lane],
                SUBSET_LABEL[subset],
                latency["count"],
                latency["mean_ms"] if latency["mean_ms"] is not None else "—",
                latency["min_ms"] if latency["min_ms"] is not None else "—",
                latency["max_ms"] if latency["max_ms"] is not None else "—",
            ])
    _table(lines, ["赛道", "子集", "成功次数", "平均毫秒", "最小", "最大"], rows)
    lines.append("")

    # 11. 失败
    lines.append("## 10. 失败清单")
    lines.append("")
    if failures:
        lines.append(
            "失败单位保留在问题检出分母里；状态未知的请求**没有自动重试**，resume 时也只重跑“确定失败”。"
        )
        lines.append("")
        rows = []
        for f in failures:
            rows.append([
                f.get("lane"),
                f.get("case_id"),
                f.get("repeat"),
                f.get("outcome"),
                f.get("http_status"),
                (f.get("error") or "")[:160],
            ])
        _table(lines, ["赛道", "案例", "重复", "结果", "HTTP", "错误（截断）"], rows)
    else:
        lines.append("本次所有计划单位都成功。")
    lines.append("")

    # 12. 局限
    lines.append("## 11. 局限（引用任何数字都必须同时引用）")
    lines.append("")
    lines.append("- 开发集只有 5 个案例、6 个标准问题句，且开发过程中已被反复查看，**不能代表未见过的真实客户口述**。")
    lines.append("- 注入问题集是团队自己造的（D1），与开发过程同源；埋点是否成立需业务/设计确认，不能当作独立第三方测试集。")
    lines.append("- 样本量很小：注入集 18 个问题、5 个无问题组。按重复 3 次后分母是 54 次问题句与 15 组无问题样本，仍是小样本，只能作参考。")
    lines.append("- 标准答案由团队整理、未经设计师逐条确认；标准答案的错误会同时进入分子和分母。")
    lines.append("- 对照组用同一模型直接问，只统计问题检出；它的“未匹配主张”不等于误报，也不与完整系统的拆句/字段指标混比。")
    lines.append("- 拆句配对用“最长公共子序列 / 较长者长度 ≥ 0.5、一对一贪心”，与人工判断可能有个别出入；对照组同样按此往宽处匹配。")
    lines.append("- 留出集 c5 本次没有运行；只有项目负责人宣布方案冻结后，才能用单独脚本跑一次并单独报告。")
    lines.append("- 本报告不代表任何对外结论；对外口径需另按 D4 挑选 3–5 条并注明样本量与局限。")
    lines.append("")

    # 13. 原始输出
    lines.append("## 12. 原始输出")
    lines.append("")
    lines.append(f"- 运行目录：`{summary.get('run_dir')}/`")
    lines.append("  - `checkpoints/`：每个 (lane, 案例, 重复) 的请求体、原始响应、结果分类与耗时；resume 依据")
    lines.append("  - `health.json`：后端健康检查原文")
    lines.append("  - `summary.json`：本报告的全部结构化数字")
    lines.append("  - `calls.jsonl`：每次网络调用一行（地址、结果、耗时；不含 API Key）")
    lines.append("")
    return "\n".join(lines)