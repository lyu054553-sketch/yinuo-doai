#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""衣诺 D2 正式评测 runner（由总控执行；本文件不自行发请求）。

跑三类对象：
1. 衣诺完整系统（HTTP `POST /api/sessions`，审计已有方案）——开发集 5 个案例 + 注入集 20 组；
2. 同一模型的“通用问法”对照组（OpenAI 兼容 chat/completions）——完全相同的样本与重复次数；
3. 两者的结果分别按开发集、注入集统计分子/分母。

硬门禁：
- 后端必须非 MOCK（`mock_mode` 必须为 false），且 `health.llm_model` 必须等于 `--model`；
- D1 审批记录 `decision=approved`，且注入集 SHA256 与审批记录完全一致；
- 注入组只发送 `customer_text` + `existing_spec`，标题/类别/expected_issues/test_injected/resources 一律不发；
- 留出集 c5 在任何参数下都被拒绝，本 runner 不会为它创建会话；
- API Key 只从环境变量读取，不写进日志、报告或 checkpoint。

失败处理：
- 每个 (lane, case, repeat) 一个 checkpoint，可 `--resume` 复用成功项，避免重复计费；
- 状态未知（超时/断连）的请求**不自动重试**；resume 只重跑“确定失败”（HTTP 错误/非 JSON）；
- 失败单位保留在问题检出的分母里，逐条列在报告第 10 节。

用法见 eval/D2_RUNBOOK.md。退出码：0 全部成功；3 跑完但有失败；2 门禁拒绝；1 其他错误。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import formal_baseline
import formal_data
import formal_http
import formal_report
import run_eval

LANES = ("yinuo", "baseline")
SUBSETS = ("dev", "injected")
DEFAULT_APPROVAL = os.path.join(formal_data.repo_root(), "orchestration", "approvals", "d1.json")
DEFAULT_DATASET = os.path.join(HERE, "datasets", "injected.json")
DEFAULT_REPORT_NAME = "d2_正式评测报告.md"
SCRIPT_VERSION = "eval-d2-1.0"


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# 参数
# ---------------------------------------------------------------------------

def parse_list(raw: str, allowed: Tuple[str, ...], name: str) -> List[str]:
    items = [item.strip() for item in (raw or "").split(",") if item.strip()]
    if not items:
        raise formal_data.FormalDataError(f"--{name} 不能为空")
    unknown = [item for item in items if item not in allowed]
    if unknown:
        raise formal_data.FormalDataError(f"--{name} 只允许 {'、'.join(allowed)}，收到 {unknown}")
    ordered: List[str] = []
    for item in items:
        if item not in ordered:
            ordered.append(item)
    return ordered


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="衣诺 D2 正式评测：衣诺完整系统 + 通用问法对照组，开发集与注入集分别统计。",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8100", help="衣诺后端地址（必须非 MOCK）")
    parser.add_argument("--magpie-url", default="http://127.0.0.1:3425/v1", help="通用问法（OpenAI 兼容）地址；可含 /v1")
    parser.add_argument("--model", default="auto/deepseek-v4.1-flash", help="模型名，必须等于后端 health.llm_model")
    parser.add_argument("--concurrency", type=int, default=3, help="并发单位数（默认 3）")
    parser.add_argument("--timeout", type=float, default=180.0, help="单次请求超时（秒）")
    parser.add_argument("--repeats", type=int, default=3, help="每个案例/组重复次数")
    parser.add_argument("--lanes", default="yinuo,baseline", help="要跑的赛道：yinuo,baseline")
    parser.add_argument("--subsets", default="dev,injected", help="要跑的子集：dev,injected")
    parser.add_argument("--data-dir", default=None, help="data/ 目录，默认仓库根目录下的 data/")
    parser.add_argument("--dataset", default=DEFAULT_DATASET, help="已批准的注入集路径")
    parser.add_argument("--approval", default=DEFAULT_APPROVAL, help="D1 审批记录路径")
    parser.add_argument("--runs-dir", default=os.path.join(HERE, "runs"), help="原始输出根目录")
    parser.add_argument("--reports-dir", default=os.path.join(HERE, "reports"), help="报告根目录")
    parser.add_argument("--run-id", default=None, help="运行编号；resume 时必须给已有的编号")
    parser.add_argument("--resume", action="store_true", help="复用该 run-id 下已成功的 checkpoint")
    parser.add_argument("--api-key-env", default="MAGPIE_API_KEY,OPENAI_API_KEY",
                        help="按顺序查找 API Key 的环境变量名（逗号分隔，只从环境读取）")
    parser.add_argument("--baseline-max-tokens", type=int, default=1200, help="对照组单次最大输出 token")
    parser.add_argument("--baseline-response-format", action="store_true",
                        help="给对照组请求加 response_format=json_object（服务端不支持时不要开）")
    parser.add_argument("--dry-run", action="store_true", help="离线检查门禁与请求体，不联网、不写 runs/")
    parser.add_argument("--tag", default="d2", help="报告文件前缀")
    parser.add_argument("--report-name", default=DEFAULT_REPORT_NAME, help="报告文件名")
    return parser.parse_args(argv)


# ---------------------------------------------------------------------------
# 计划与 checkpoint
# ---------------------------------------------------------------------------

def decide_unit_action(existing: Optional[Dict[str, Any]], resume: bool) -> str:
    """resume 时如何对待已有 checkpoint：复用成功、保留未知、或重新执行。

    - 成功：直接复用，不重复计费；
    - 状态未知（超时/断连）：保留原结果，**不自动重试**；
    - 确定失败（HTTP 错误/非 JSON）：允许重跑；
    - 没有 checkpoint 或非 resume：执行。
    """
    if not resume or existing is None:
        return "execute"
    outcome = existing.get("outcome")
    if outcome == formal_http.OUTCOME_SUCCESS:
        return "reuse_success"
    if outcome == formal_http.OUTCOME_UNKNOWN:
        return "keep_unknown"
    return "execute"


def checkpoint_key(plan: Dict[str, Any]) -> str:
    return f"{plan['lane']}__{plan['subset']}__{plan['case_id']}__r{plan['repeat']}"


def checkpoint_path(run_dir: str, plan: Dict[str, Any]) -> str:
    return os.path.join(run_dir, "checkpoints", checkpoint_key(plan) + ".json")


def save_json(path: str, payload: Any) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.replace(tmp, path)


def load_checkpoint(path: str) -> Optional[Dict[str, Any]]:
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def build_plan(lanes: List[str], subsets: List[str], dev_gold: Dict[str, Dict[str, Any]],
               inj: Dict[str, Any], repeats: int) -> List[Dict[str, Any]]:
    pairs: List[Tuple[str, str, Dict[str, Any]]] = []
    if "dev" in subsets:
        for cid in formal_data.DEV_CASE_IDS:
            pairs.append(("dev", cid, dev_gold[cid]))
    if "injected" in subsets:
        for gid in inj["group_ids"]:
            pairs.append(("injected", gid, inj["gold_by_id"][gid]))
    plan: List[Dict[str, Any]] = []
    for lane in lanes:
        for subset, case_id, gold in pairs:
            count = sum(1 for u in gold["utterances"] if u.get("status") in formal_data.PROBLEM_STATUSES)
            for repeat in range(1, repeats + 1):
                plan.append(
                    {
                        "lane": lane,
                        "subset": subset,
                        "case_id": case_id,
                        "repeat": repeat,
                        "gold_problem_count": count,
                    }
                )
    return plan


# ---------------------------------------------------------------------------
# 请求构造（发送前硬门禁）
# ---------------------------------------------------------------------------

def build_yinuo_request(plan: Dict[str, Any], gold: Dict[str, Any]) -> Dict[str, Any]:
    if plan["subset"] == "dev":
        payload = formal_data.dev_payload(gold)
    else:
        payload = formal_data.injected_payload(gold)
    formal_data.assert_payload_clean("yinuo", plan["subset"], gold, payload)
    return payload


def build_baseline_request(plan: Dict[str, Any], gold: Dict[str, Any], args: argparse.Namespace) -> Dict[str, Any]:
    spec_payload = run_eval.normalize_existing_spec(gold["spec"])
    request = formal_baseline.build_request(
        model=args.model,
        customer_text=gold["text"],
        existing_spec=spec_payload,
        max_tokens=args.baseline_max_tokens,
        response_format=args.baseline_response_format,
    )
    # 对照组同样不得夹带答案：把整段请求序列化后检查提示词与答案文本
    serialized = json.dumps(request, ensure_ascii=False)
    for token in formal_data.LEAK_TOKENS:
        if token and token in serialized:
            raise formal_data.FormalDataError(
                f"[{plan['lane']}/{plan['case_id']}] 对照组请求出现答案提示词「{token}」，拒绝发送"
            )
    for text in formal_data.answer_strings(gold):
        if text in serialized:
            raise formal_data.FormalDataError(
                f"[{plan['lane']}/{plan['case_id']}] 对照组请求出现标准答案文字「{text[:30]}」，拒绝发送"
            )
    if plan["subset"] == "injected":
        for label in (gold.get("title"), gold.get("category")):
            if label and str(label) in serialized:
                raise formal_data.FormalDataError(
                    f"[{plan['lane']}/{plan['case_id']}] 对照组请求出现注入组标题/类别，拒绝发送"
                )
    return request


# ---------------------------------------------------------------------------
# 单个单位的执行
# ---------------------------------------------------------------------------

class CallLog:
    def __init__(self, path: str):
        self.path = path
        self.lock = threading.Lock()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(self.path, "a", encoding="utf-8"):
            pass

    def append(self, entry: Dict[str, Any]) -> None:
        line = json.dumps(entry, ensure_ascii=False)
        with self.lock:
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")


def execute_unit(plan: Dict[str, Any], gold: Dict[str, Any], args: argparse.Namespace,
                 env: Dict[str, Any], api_key: Optional[str], calllog: CallLog,
                 run_dir: str) -> Dict[str, Any]:
    checkpoint: Dict[str, Any] = {
        "unit": plan,
        "lane": plan["lane"],
        "subset": plan["subset"],
        "case_id": plan["case_id"],
        "repeat": plan["repeat"],
        "recorded_at": now_iso(),
        "outcome": formal_http.OUTCOME_UNKNOWN,
        "http_status": None,
        "elapsed_ms": None,
        "error": None,
        "request": None,
        "response": None,
        "model_returned": None,
        "api_key_present": bool(api_key),
    }

    if plan["lane"] == "yinuo":
        payload = build_yinuo_request(plan, gold)
        checkpoint["request"] = payload
        result = formal_http.request_json(
            "POST", env["base_url"].rstrip("/") + "/api/sessions", payload=payload, timeout=args.timeout
        )
    else:
        request = build_baseline_request(plan, gold, args)
        checkpoint["request"] = request
        result = formal_http.request_json(
            "POST", env["baseline_endpoint"], payload=request, timeout=args.timeout, api_key=api_key
        )

    checkpoint["outcome"] = result["outcome"]
    checkpoint["http_status"] = result["http_status"]
    checkpoint["elapsed_ms"] = result["elapsed_ms"]
    checkpoint["error"] = result["error"]
    checkpoint["response"] = result["parsed"]
    checkpoint["raw_truncated"] = result.get("raw_truncated")
    if result["outcome"] != formal_http.OUTCOME_SUCCESS:
        checkpoint["raw"] = result.get("raw", "")

    if plan["lane"] == "baseline" and isinstance(result["parsed"], dict):
        checkpoint["model_returned"] = result["parsed"].get("model")

    save_json(checkpoint_path(run_dir, plan), checkpoint)
    calllog.append(
        {
            "at": checkpoint["recorded_at"],
            "lane": plan["lane"],
            "subset": plan["subset"],
            "case_id": plan["case_id"],
            "repeat": plan["repeat"],
            "url": result["url"],
            "outcome": result["outcome"],
            "http_status": result["http_status"],
            "elapsed_ms": result["elapsed_ms"],
            "error": result["error"],
            "api_key_present": bool(api_key),
        }
    )
    return checkpoint


# ---------------------------------------------------------------------------
# 分析（在 checkpoint 之上重新计算，resume 不需重发请求）
# ---------------------------------------------------------------------------

def analyze_unit(plan: Dict[str, Any], checkpoint: Dict[str, Any], gold: Dict[str, Any],
                 valid_ids: set) -> Dict[str, Any]:
    unit = {
        "lane": plan["lane"],
        "subset": plan["subset"],
        "case_id": plan["case_id"],
        "repeat": plan["repeat"],
        "ok": False,
        "outcome": checkpoint.get("outcome"),
        "http_status": checkpoint.get("http_status"),
        "error": checkpoint.get("error"),
        "elapsed_ms": checkpoint.get("elapsed_ms"),
        "metrics": None,
        "model_returned": checkpoint.get("model_returned"),
        "session_meta": None,
        "checkpoint_key": checkpoint_key(plan),
    }
    if checkpoint.get("outcome") != formal_http.OUTCOME_SUCCESS:
        return unit

    response = checkpoint.get("response")
    if plan["lane"] == "yinuo":
        if not isinstance(response, dict) or not isinstance(response.get("utterances"), list):
            unit["outcome"] = "invalid_response"
            unit["error"] = "POST /api/sessions 返回不是合法 Session（缺 utterances）"
            return unit
        if response.get("source") == "mock":
            unit["outcome"] = "mock_source"
            unit["error"] = "后端返回 source=mock：疑似退回示例模式，本次结果不可作为模型模式结果"
            return unit
        try:
            metrics = run_eval.evaluate_session(plan["case_id"], response, gold, valid_ids)
        except Exception as exc:  # noqa: BLE001  后端返回结构异常时记为失败，不中断整轮
            unit["outcome"] = "invalid_response"
            unit["error"] = f"Session 无法评测：{type(exc).__name__}: {exc}"
            return unit
        spec_versions = response.get("spec_versions") or []
        unit["metrics"] = metrics
        unit["session_meta"] = {
            "session_id": response.get("id"),
            "source": response.get("source"),
            "mode": response.get("mode"),
            "created_at": response.get("created_at"),
            "sample_id": (response.get("input") or {}).get("sample_id"),
            "version": spec_versions[-1].get("version") if spec_versions else None,
        }
        unit["ok"] = True
        return unit

    parsed = formal_baseline.parse_baseline_response(response)
    if not parsed.get("ok"):
        unit["outcome"] = "parse_failure"
        unit["error"] = parsed.get("error") or "对照组输出无法解析"
        return unit
    unit["metrics"] = formal_baseline.evaluate_baseline(
        plan["case_id"], parsed.get("items") or [], gold["text"], gold
    )
    unit["items_count"] = unit["metrics"].get("items_count")
    unit["ok"] = True
    return unit


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def make_headline(env: Dict[str, Any], stats: Dict[str, Any], planned_count: int,
                  success_count: int, failure_count: int) -> str:
    def cell(lane: str, subset: str) -> str:
        st = stats.get(lane, {}).get(subset, {})
        return formal_report.fmt(int(st.get("detected") or 0), int(st.get("gold_problems") or 0))

    return (
        f"模型 `{env.get('model')}`（后端自报 `{env.get('health_model')}`）下，"
        f"衣诺问题句检出：开发集 {cell('yinuo', 'dev')}、注入集 {cell('yinuo', 'injected')}；"
        f"同一模型通用问法对照组：开发集 {cell('baseline', 'dev')}、注入集 {cell('baseline', 'injected')}。"
        f"计划 {planned_count} 个单位，成功 {success_count}，失败 {failure_count}（失败保留在分母，逐条见第 10 节）。"
        f"样本量小，开发集已在开发中看过，注入集为团队自造，详见第 11 节局限。"
        "本报告不构成对外结论。"
    )


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    try:
        if args.repeats < 1:
            raise formal_data.FormalDataError("--repeats 必须 >= 1")
        if args.concurrency < 1:
            raise formal_data.FormalDataError("--concurrency 必须 >= 1")
        if args.timeout <= 0:
            raise formal_data.FormalDataError("--timeout 必须 > 0")
        lanes = parse_list(args.lanes, LANES, "lanes")
        subsets = parse_list(args.subsets, SUBSETS, "subsets")

        data_dir = args.data_dir or os.path.join(formal_data.repo_root(), "data")
        dataset_path = os.path.abspath(args.dataset)
        approval_path = os.path.abspath(args.approval)

        # ---- 门禁与数据 ----
        approval = formal_data.check_approval(approval_path, dataset_path)
        inj = formal_data.load_injected_dataset(dataset_path)
        dev_gold = formal_data.load_dev_gold(data_dir)
        valid_ids = run_eval.load_resource_ids(data_dir)

        repeats = args.repeats
        plan = build_plan(lanes, subsets, dev_gold, inj, repeats)
        golds: Dict[str, Dict[str, Any]] = dict(dev_gold)
        golds.update(inj["gold_by_id"])

        # ---- dry-run：只检查门禁与请求体，不联网、不写 runs/ ----
        if args.dry_run:
            failures: List[str] = []
            checked = 0
            for unit in plan:
                gold = golds[unit["case_id"]]
                try:
                    if unit["lane"] == "yinuo":
                        build_yinuo_request(unit, gold)
                    else:
                        build_baseline_request(unit, gold, args)
                except formal_data.FormalDataError as exc:
                    failures.append(f"{checkpoint_key(unit)}：{exc}")
                checked += 1
            print("=" * 72)
            print("D2 dry-run（离线，不联网、不写 runs/）")
            print(f"审批：{approval_path}  decision=approved  SHA256 匹配={approval['sha256_match']}")
            print(f"方案冻结={approval['scheme_frozen']}  留出授权={approval['holdout_authorized']}")
            print(f"注入集：{len(inj['group_ids'])} 组 / {inj['issue_count']} 个问题 / 无问题组 {inj['clean_group_ids']}")
            print(f"开发集：{len(dev_gold)} 个案例 / {sum(1 for cid in dev_gold for u in dev_gold[cid]['utterances'] if u['status'] in formal_data.PROBLEM_STATUSES)} 个问题句")
            print(f"计划单位：{len(plan)}（lanes={lanes}，subsets={subsets}，repeats={repeats}）")
            print(f"请求体门禁：检查 {checked} 个，失败 {len(failures)} 个")
            for item in failures:
                print(f"  ✗ {item}")
            print(f"模型：{args.model}（运行时须等于 health.llm_model）")
            print(f"对照组地址：{formal_http.chat_endpoint(args.magpie_url)}")
            print(f"API Key 环境变量：{args.api_key_env}（只读环境，不打印内容）")
            print("=" * 72)
            return 0 if not failures else 2

        # ---- 健康检查（模型名与非 MOCK 门禁；先检查，再创建运行目录） ----
        base_url = args.base_url.rstrip("/")
        health_call = formal_http.get_json(base_url + "/api/health", timeout=min(args.timeout, 60.0))
        if health_call.get("outcome") != formal_http.OUTCOME_SUCCESS:
            raise formal_data.FormalDataError(f"后端健康检查失败：{health_call.get('error')}")
        health = health_call.get("parsed") or {}
        if not isinstance(health, dict):
            raise formal_data.FormalDataError("GET /api/health 返回不是 JSON 对象")

        mock_mode = health.get("mock_mode")
        if mock_mode is not False:
            print("拒绝运行：后端处于 MOCK 模式或未报告 mock_mode=false；D2 必须在模型模式运行。", file=sys.stderr)
            return 2
        health_model = health.get("llm_model")
        if str(health_model) != args.model:
            print(
                f"拒绝运行：health.llm_model={health_model!r} 与 --model={args.model!r} 不一致。"
                "请让后端用同一模型启动，或改用一致的 --model。",
                file=sys.stderr,
            )
            return 2

        # ---- 运行目录 ----
        if args.resume:
            if not args.run_id:
                raise formal_data.FormalDataError("--resume 必须同时给 --run-id")
            run_id = args.run_id
            run_dir = os.path.join(args.runs_dir, run_id)
            if not os.path.isdir(os.path.join(run_dir, "checkpoints")):
                raise formal_data.FormalDataError(f"找不到可恢复的 checkpoint 目录：{run_dir}/checkpoints")
        else:
            if args.run_id:
                run_id = args.run_id
            else:
                run_id = datetime.now().strftime("%Y%m%dT%H%M%S")
            run_dir = os.path.join(args.runs_dir, run_id)
            if os.path.isdir(os.path.join(run_dir, "checkpoints")) and os.listdir(os.path.join(run_dir, "checkpoints")):
                raise formal_data.FormalDataError(
                    f"运行目录已有 checkpoint：{run_dir}。换一个 --run-id，或加 --resume 继续。"
                )
        os.makedirs(os.path.join(run_dir, "checkpoints"), exist_ok=True)
        calls_path = os.path.join(run_dir, "calls.jsonl")
        calllog = CallLog(calls_path)
        save_json(os.path.join(run_dir, "health.json"), health)

        key_info = formal_http.resolve_api_key(
            [item.strip() for item in args.api_key_env.split(",") if item.strip()]
        )
        api_key = key_info.get("key") if "baseline" in lanes else None
        baseline_endpoint = formal_http.chat_endpoint(args.magpie_url) if "baseline" in lanes else None

        env = {
            "base_url": base_url,
            "mock_mode": mock_mode,
            "health_model": health_model,
            "model": args.model,
            "model_matches": True,
            "image_provider": health.get("image_provider"),
            "vision_ok": health.get("vision_ok"),
            "baseline_endpoint": baseline_endpoint,
            "api_key_env": key_info.get("env") if key_info.get("present") else None,
            "api_key_present": bool(key_info.get("present")),
            "api_key_checked": key_info.get("checked"),
            "repeats": repeats,
            "concurrency": args.concurrency,
            "timeout": args.timeout,
            "dataset_sha256": inj["sha256"],
            "approval_path": approval_path,
            "approval_recorded_at": approval.get("recorded_at"),
            "approval_sha256_match": approval["sha256_match"],
            "scheme_frozen": approval["scheme_frozen"],
            "holdout_authorized": approval["holdout_authorized"],
            "dev_case_ids": formal_data.DEV_CASE_IDS,
            "dev_issue_count": sum(
                1 for cid in dev_gold for u in dev_gold[cid]["utterances"] if u["status"] in formal_data.PROBLEM_STATUSES
            ),
            "injected_group_count": len(inj["group_ids"]),
            "injected_issue_count": inj["issue_count"],
            "injected_clean_groups": inj["clean_group_ids"],
            "injected_group_ids": inj["group_ids"],
            "script_version": SCRIPT_VERSION,
        }
        save_json(os.path.join(run_dir, "environment.json"), env)

        # ---- 执行计划（含 resume） ----
        started_at = now_iso()
        checkpoints: Dict[str, Dict[str, Any]] = {}
        to_execute: List[Dict[str, Any]] = []
        reused_success = 0
        reused_unknown = 0

        for unit in plan:
            key = checkpoint_key(unit)
            existing = load_checkpoint(checkpoint_path(run_dir, unit)) if args.resume else None
            action = decide_unit_action(existing, args.resume)
            if action == "reuse_success":
                checkpoints[key] = existing
                reused_success += 1
                continue
            if action == "keep_unknown":
                checkpoints[key] = existing
                reused_unknown += 1
                calllog.append(
                    {
                        "at": now_iso(),
                        "lane": unit["lane"],
                        "subset": unit["subset"],
                        "case_id": unit["case_id"],
                        "repeat": unit["repeat"],
                        "outcome": "skipped_unknown_on_resume",
                        "note": "状态未知，不自动重试",
                    }
                )
                continue
            to_execute.append(unit)

        print(f"计划 {len(plan)} 个单位；复用成功 {reused_success}；保留未重试的未知状态 {reused_unknown}；"
              f"本次执行 {len(to_execute)} 个（并发 {args.concurrency}）。")

        errors: List[str] = []
        if to_execute:
            with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
                futures = {
                    pool.submit(
                        execute_unit, unit, golds[unit["case_id"]], args, env, api_key, calllog, run_dir
                    ): unit
                    for unit in to_execute
                }
                for future in as_completed(futures):
                    unit = futures[future]
                    try:
                        checkpoint = future.result()
                    except Exception as exc:  # noqa: BLE001
                        errors.append(f"{checkpoint_key(unit)}：{type(exc).__name__}: {exc}")
                        traceback.print_exc()
                        checkpoint = {
                            "unit": unit,
                            "lane": unit["lane"],
                            "subset": unit["subset"],
                            "case_id": unit["case_id"],
                            "repeat": unit["repeat"],
                            "recorded_at": now_iso(),
                            "outcome": formal_http.OUTCOME_DEFINITE,
                            "error": f"本地异常：{type(exc).__name__}: {exc}",
                            "response": None,
                            "elapsed_ms": None,
                        }
                        save_json(checkpoint_path(run_dir, unit), checkpoint)
                    checkpoints[checkpoint_key(unit)] = checkpoint

        # ---- 分析 ----
        units: List[Dict[str, Any]] = []
        for unit in plan:
            checkpoint = checkpoints.get(checkpoint_key(unit))
            if checkpoint is None:
                units.append(
                    {
                        "lane": unit["lane"],
                        "subset": unit["subset"],
                        "case_id": unit["case_id"],
                        "repeat": unit["repeat"],
                        "ok": False,
                        "outcome": "missing",
                        "http_status": None,
                        "error": "没有 checkpoint（可能被中断）",
                        "elapsed_ms": None,
                        "metrics": None,
                    }
                )
                continue
            analyzed = analyze_unit(unit, checkpoint, golds[unit["case_id"]], valid_ids)
            units.append(analyzed)
            # 语义失败写回 checkpoint，便于人工排查（不改动原始 response）
            if not analyzed["ok"] and checkpoint.get("outcome") == formal_http.OUTCOME_SUCCESS:
                checkpoint["analysis_outcome"] = analyzed["outcome"]
                checkpoint["analysis_error"] = analyzed["error"]
                save_json(checkpoint_path(run_dir, unit), checkpoint)

        stats = formal_report.aggregate(units, plan)
        session_sources = sorted(
            {str((u.get("session_meta") or {}).get("source")) for u in units if u.get("session_meta")}
        )
        session_created = sorted(
            str((u.get("session_meta") or {}).get("created_at"))
            for u in units
            if (u.get("session_meta") or {}).get("created_at")
        )
        env["session_sources"] = session_sources
        env["session_created_range"] = [session_created[0], session_created[-1]] if session_created else []
        env["baseline_models_returned"] = sorted(
            {str(u.get("model_returned")) for u in units if u.get("model_returned")}
        )
        save_json(os.path.join(run_dir, "environment.json"), env)
        failures = [
            {
                "lane": u["lane"],
                "subset": u["subset"],
                "case_id": u["case_id"],
                "repeat": u["repeat"],
                "outcome": u["outcome"],
                "http_status": u.get("http_status"),
                "error": u.get("error"),
            }
            for u in units
            if not u["ok"]
        ]
        success_count = sum(1 for u in units if u["ok"])
        finished_at = now_iso()

        summary: Dict[str, Any] = {
            "script_version": SCRIPT_VERSION,
            "run_id": run_id,
            "run_dir": run_dir,
            "started_at": started_at,
            "finished_at": finished_at,
            "lanes": lanes,
            "subsets": subsets,
            "environment": env,
            "planned_count": len(plan),
            "success_count": success_count,
            "failure_count": len(failures),
            "reused_success": reused_success,
            "reused_unknown": reused_unknown,
            "local_errors": errors,
            "planned": plan,
            "units": units,
            "stats": stats,
            "failures": failures,
        }
        summary["headline"] = make_headline(env, stats, len(plan), success_count, len(failures))

        report_input = dict(summary)
        report_input["golds"] = golds
        markdown = formal_report.build_report(report_input)

        os.makedirs(args.reports_dir, exist_ok=True)
        report_path = os.path.join(args.reports_dir, args.report_name)
        with open(report_path, "w", encoding="utf-8") as fh:
            fh.write(markdown)
        save_json(os.path.join(run_dir, "summary.json"), summary)

        # ---- 控制台摘要 ----
        print("=" * 72)
        print(f"衣诺 D2 运行完成：{run_id}")
        print(f"计划 {len(plan)}；成功 {success_count}；失败 {len(failures)}")
        for lane in lanes:
            for subset in subsets:
                st = stats.get(lane, {}).get(subset, {})
                print(
                    f"  {formal_report.LANE_LABEL[lane]} · {formal_report.SUBSET_LABEL[subset]}："
                    f"问题检出 {st.get('detected')}/{st.get('gold_problems')}；"
                    f"误报 已落实 {st.get('fp_已落实')}、间接体现 {st.get('fp_间接体现')}；"
                    f"单位 {st.get('units_success')}/{st.get('units_planned')}"
                )
        print(f"报告：{report_path}")
        print(f"原始输出：{run_dir}")
        print("=" * 72)
        return 0 if not failures else 3

    except formal_data.FormalDataError as exc:
        print(f"D2 评测中止：{exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("D2 评测被中断；已完成的 checkpoint 可用 --resume 恢复。", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())