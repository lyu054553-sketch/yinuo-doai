#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""衣诺评测脚手架（里程碑 D0）。

只通过 HTTP 调用已启动的后端（默认 http://127.0.0.1:8100），不导入后端代码。
标准答案来自 data/cases.json，只读取开发集（c1、c2、c3、c4、c6）。
留出集 c5 在本脚本中无论参数如何都被硬性拒绝，不会创建任何会话。

用法（由总控执行，本 AI 不执行 shell）：
    python eval/run_eval.py                       # 默认 http://127.0.0.1:8100，跑 3 次
    python eval/run_eval.py --base-url http://127.0.0.1:8100 --repeats 3
    python eval/run_eval.py --cases c1,c2         # 只跑部分开发案例

退出码：
    0 = 运行完成，且（MOCK 模式下）脚手架自检全部满分
    1 = 运行出错（后端不可达、接口报错、结果无法解析等）
    2 = 参数被拒绝（如请求留出集 c5），或 MOCK 模式下自检不满分（说明评测脚本本身有问题）
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# 固定词表与常量（与 docs/开发/00-总体说明与接口约定.md 一致）
# ---------------------------------------------------------------------------

FIELDS = ["品类", "版型结构", "领型", "面料", "色彩", "装饰纹样", "长度比例", "松弛余量"]

STATUS_ORDER = ["有冲突", "被改写", "未落实", "需设计师判断", "间接体现", "已落实", "已与客户确认"]
SEVERITY = {s: i for i, s in enumerate(STATUS_ORDER)}

PROBLEM_STATUSES = {"有冲突", "被改写", "未落实", "需设计师判断"}

# 开发集白名单。留出集 c5 永远不在此列。
DEV_CASE_IDS = ["c1", "c2", "c3", "c4", "c6"]
HOLDOUT_CASE_ID = "c5"

# 开发集全部案例的问题句标准答案数量（用于核对标准答案数据本身，共 6 句）。
EXPECTED_GOLD_PROBLEMS = {"c1": 0, "c2": 3, "c3": 1, "c4": 1, "c6": 1}

SCRIPT_VERSION = "eval-d0-1.0"


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------

def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def frac(n: int, d: int) -> str:
    return f"{n}/{d}"


def pct(n: int, d: int) -> str:
    if d <= 0:
        return "—"
    return f"{100.0 * n / d:.1f}%"


def fmt_frac_pct(n: int, d: int) -> str:
    return f"{frac(n, d)}（{pct(n, d)}）"


def status_of(utterance: Dict[str, Any]) -> Optional[str]:
    """一句原话的状态 = 其 mappings 中最严重的状态（按约定第 4 节）。"""
    statuses = []
    for mapping in utterance.get("mappings") or []:
        st = mapping.get("status")
        if st:
            statuses.append(st)
    if not statuses:
        return None
    return min(statuses, key=lambda s: SEVERITY.get(s, 99))


def field_set(mappings: List[Dict[str, Any]]) -> List[str]:
    """一句原话对应的字段集合（保序去重；多件套的单品不参与字段集合比较）。"""
    out: List[str] = []
    for mapping in mappings or []:
        field = mapping.get("field")
        if field and field not in out:
            out.append(field)
    return out


def lcs_len(a: str, b: str) -> int:
    if not a or not b:
        return 0
    prev = [0] * (len(b) + 1)
    for ch in a:
        cur = [0] * (len(b) + 1)
        for j, ch2 in enumerate(b, 1):
            if ch == ch2:
                cur[j] = prev[j - 1] + 1
            elif prev[j] >= cur[j - 1]:
                cur[j] = prev[j]
            else:
                cur[j] = cur[j - 1]
        prev = cur
    return prev[-1]


def overlap_ratio(a: str, b: str) -> float:
    """字符重叠比例 = 最长公共子序列长度 / 两者较长者的长度。

    用较长者作分母，合并或拆分过度的预测会被压低，不会因为“包含”而误判配上。
    """
    if not a or not b:
        return 0.0
    return lcs_len(a, b) / float(max(len(a), len(b)))


def loose_class(status: Optional[str]) -> str:
    """“间接体现”和“已落实”合并算一类，用于宽松一致率（单独统计，不和问题检出混在一起）。"""
    if status in ("间接体现", "已落实"):
        return "隐式（间接体现或已落实）"
    return status or "缺失"


def json_equal(a: Any, b: Any) -> bool:
    return json.dumps(a, ensure_ascii=False, sort_keys=True) == json.dumps(b, ensure_ascii=False, sort_keys=True)


def normalize_existing_spec(spec: Any) -> Optional[Dict[str, str]]:
    """把已有方案规整成接口要求的 Record<Field,string>。

    案例三的方案是多件套嵌套（如 {"外套": "...", "连衣裙": "..."}），
    这里把嵌套项拍平成“外套：…；连衣裙：…”，避免请求体不符合接口约定。
    """
    if not isinstance(spec, dict):
        return None
    out: Dict[str, str] = {}
    for field in FIELDS:
        value = spec.get(field)
        if isinstance(value, str):
            out[field] = value
        elif isinstance(value, dict):
            out[field] = "；".join(f"{k}：{v}" for k, v in value.items())
        elif value is None:
            out[field] = ""
        else:
            out[field] = str(value)
    return out


# ---------------------------------------------------------------------------
# 数据读取（只读 data/，只取开发案例）
# ---------------------------------------------------------------------------

class EvalError(RuntimeError):
    pass


def load_gold(data_dir: str, case_ids: List[str]) -> Dict[str, Dict[str, Any]]:
    path = os.path.join(data_dir, "cases.json")
    if not os.path.isfile(path):
        raise EvalError(f"找不到标准答案文件：{path}")
    with open(path, "r", encoding="utf-8") as fh:
        raw = json.load(fh)

    by_id = {c.get("id"): c for c in raw.get("cases") or []}
    missing = [cid for cid in case_ids if cid not in by_id]
    if missing:
        raise EvalError(f"标准答案中找不到开发案例：{', '.join(missing)}")

    gold: Dict[str, Dict[str, Any]] = {}
    for cid in case_ids:
        case = by_id[cid]
        utterances = []
        for utt in case.get("utterances") or []:
            utterances.append(
                {
                    "id": utt.get("id"),
                    "text": utt.get("text") or "",
                    "status": status_of(utt),
                    "mappings": utt.get("mappings") or [],
                }
            )
        gold[cid] = {
            "id": cid,
            "title": case.get("title") or "",
            "text": case.get("customer_text") or "",
            "spec": case.get("spec"),
            "utterances": utterances,
        }
    return gold


def collect_resource_ids(node: Any, out: set) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "id" and isinstance(value, str):
                out.add(value)
            else:
                collect_resource_ids(value, out)
    elif isinstance(node, list):
        for item in node:
            collect_resource_ids(item, out)


def load_resource_ids(data_dir: str) -> set:
    path = os.path.join(data_dir, "resources.json")
    if not os.path.isfile(path):
        raise EvalError(f"找不到资源库文件：{path}")
    with open(path, "r", encoding="utf-8") as fh:
        raw = json.load(fh)
    ids: set = set()
    collect_resource_ids(raw, ids)
    return ids


# ---------------------------------------------------------------------------
# HTTP 客户端（只用标准库，不导入后端）
# ---------------------------------------------------------------------------

class HttpClient:
    def __init__(self, base_url: str, timeout: float, calls: List[Dict[str, Any]]):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.calls = calls

    def request_json(self, method: str, path: str, payload: Optional[Dict[str, Any]] = None) -> Any:
        url = self.base_url + path
        data = None
        headers = {"Accept": "application/json", "User-Agent": f"yinuo-eval/{SCRIPT_VERSION}"}
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=utf-8"

        record: Dict[str, Any] = {"method": method, "path": path, "url": url, "at": now_iso(), "ok": True}
        started = time.perf_counter()

        try:
            request = urllib.request.Request(url, data=data, headers=headers, method=method)
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                status = response.status
                body = response.read().decode("utf-8")
            record["status"] = status
            try:
                parsed = json.loads(body)
            except ValueError as exc:
                record["ok"] = False
                record["error"] = f"返回内容不是 JSON：{body[:300]}"
                raise EvalError(f"{method} {path} 返回内容不是 JSON：{exc}；前 300 字符：{body[:300]}")
            return parsed
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")
            record["ok"] = False
            record["status"] = exc.code
            record["error"] = body[:1000]
            raise EvalError(f"{method} {path} 返回 HTTP {exc.code}：{body[:400]}")
        except EvalError:
            raise
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            record["ok"] = False
            record["error"] = str(exc)
            raise EvalError(f"无法访问 {url}：{exc}")
        finally:
            record["elapsed_ms"] = round((time.perf_counter() - started) * 1000.0, 1)
            self.calls.append(record)


# ---------------------------------------------------------------------------
# 指标计算
# ---------------------------------------------------------------------------

def pair_utterances(preds: List[Dict[str, Any]], golds: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """用字符重叠把预测原话与标准原话一对一配对；重叠比例 >= 0.5 才算配上。

    贪心：按重叠比例从高到低取，每句最多配一次。
    """
    candidates: List[Tuple[float, int, int]] = []
    for pi, pred in enumerate(preds):
        ptext = (pred.get("text") or "").strip()
        if not ptext:
            continue
        for gi, gold in enumerate(golds):
            gtext = (gold.get("text") or "").strip()
            if not gtext:
                continue
            ratio = overlap_ratio(ptext, gtext)
            if ratio >= 0.5:
                candidates.append((ratio, pi, gi))
    candidates.sort(key=lambda item: (-item[0], item[1], item[2]))

    used_pred: set = set()
    used_gold: set = set()
    pairs: List[Dict[str, Any]] = []
    for ratio, pi, gi in candidates:
        if pi in used_pred or gi in used_gold:
            continue
        used_pred.add(pi)
        used_gold.add(gi)
        pairs.append(
            {"pred_index": pi, "gold_index": gi, "ratio": round(ratio, 4), "pred": preds[pi], "gold": golds[gi]}
        )
    pairs.sort(key=lambda item: item["gold_index"])
    return pairs


def check_fabrication(session: Dict[str, Any], valid_ids: set) -> Dict[str, Any]:
    """不编造：原话必须是原文子串且位置正确；引用的资源 id 必须存在；统计“无出处”栏数。"""
    violations: List[Dict[str, Any]] = []
    text = (session.get("input") or {}).get("text") or ""

    for utt in session.get("utterances") or []:
        content = utt.get("text") or ""
        utt_id = utt.get("id")
        if not content or content not in text:
            violations.append({"type": "原话不是原文子串", "utterance_id": utt_id, "text": content})
            continue
        start = utt.get("start")
        end = utt.get("end")
        if not isinstance(start, int) or not isinstance(end, int):
            violations.append({"type": "原话缺少合法位置", "utterance_id": utt_id, "start": start, "end": end})
            continue
        if start < 0 or end > len(text) or text[start:end] != content:
            violations.append(
                {
                    "type": "原话位置与原文不符",
                    "utterance_id": utt_id,
                    "text": content,
                    "start": start,
                    "end": end,
                    "slice": text[max(0, start):max(0, end)],
                }
            )

    spec_versions = session.get("spec_versions") or []
    latest = spec_versions[-1] if spec_versions else None
    ungrounded: List[str] = []
    if latest:
        fields = latest.get("fields") or {}
        for field in FIELDS:
            spec_field = fields.get(field) or {}
            for rid in spec_field.get("resource_ids") or []:
                if rid not in valid_ids:
                    violations.append(
                        {
                            "type": "资源 id 不在 resources.json 中",
                            "field": field,
                            "resource_id": rid,
                            "version": latest.get("version"),
                        }
                    )
            if spec_field.get("origin") == "无出处":
                ungrounded.append(field)

    for question in session.get("questions") or []:
        for option in question.get("options") or []:
            for rid in option.get("resource_ids") or []:
                if rid not in valid_ids:
                    violations.append(
                        {
                            "type": "选项引用了不存在的资源 id",
                            "question_id": question.get("id"),
                            "option_id": option.get("id"),
                            "resource_id": rid,
                        }
                    )

    return {
        "violations": violations,
        "ungrounded_fields": ungrounded,
        "latest_version": latest.get("version") if latest else None,
    }


def evaluate_session(case_id: str, session: Dict[str, Any], gold: Dict[str, Any], valid_ids: set) -> Dict[str, Any]:
    if not isinstance(session, dict):
        raise EvalError(f"{case_id}：POST /api/sessions 返回不是 JSON 对象")

    gold_utts = gold["utterances"]
    pred_utts = session.get("utterances") or []
    pairs = pair_utterances(pred_utts, gold_utts)

    # 1. 拆句
    seg = {
        "gold": len(gold_utts),
        "pred": len(pred_utts),
        "paired": len(pairs),
        "recall_num": len(pairs),
        "recall_den": len(gold_utts),
        "precision_num": len(pairs),
        "precision_den": len(pred_utts),
    }

    # 2. 字段对应（只看配上的原话，字段集合比较）
    field_gold = 0
    field_pred = 0
    field_match = 0
    pair_details: List[Dict[str, Any]] = []
    for pair in pairs:
        g_fields = field_set(pair["gold"].get("mappings") or [])
        p_fields = field_set(pair["pred"].get("mappings") or [])
        match = len(set(g_fields) & set(p_fields))
        field_gold += len(g_fields)
        field_pred += len(p_fields)
        field_match += match
        pair_details.append(
            {
                "gold_utterance_id": pair["gold"].get("id"),
                "pred_utterance_id": pair["pred"].get("id"),
                "ratio": pair["ratio"],
                "gold_fields": g_fields,
                "pred_fields": p_fields,
            }
        )
    field = {
        "gold_fields": field_gold,
        "pred_fields": field_pred,
        "match": field_match,
        "recall_num": field_match,
        "recall_den": field_gold,
        "precision_num": field_match,
        "precision_den": field_pred,
    }

    # 3. 状态一致率（六种状态逐句比较；另算宽松一致率）
    status_paired = 0
    status_agree = 0
    loose_agree = 0
    confusion: Counter = Counter()
    for pair in pairs:
        g_status = pair["gold"].get("status")
        p_status = status_of(pair["pred"])
        pair["gold_status"] = g_status
        pair["pred_status"] = p_status
        status_paired += 1
        confusion[(g_status or "缺失", p_status or "缺失")] += 1
        if g_status == p_status:
            status_agree += 1
        if loose_class(g_status) == loose_class(p_status):
            loose_agree += 1
    status = {
        "paired": status_paired,
        "agree": status_agree,
        "loose_agree": loose_agree,
        "confusion": {
            f"{g} → {p}": cnt
            for (g, p), cnt in sorted(confusion.items(), key=lambda kv: (kv[0][0] or "", kv[0][1] or ""))
        },
    }

    # 4. 问题检出
    pair_by_gold = {pair["gold_index"]: pair for pair in pairs}
    problem_rows: List[Dict[str, Any]] = []
    detected = 0
    for gi, gold_utt in enumerate(gold_utts):
        if gold_utt.get("status") not in PROBLEM_STATUSES:
            continue
        pair = pair_by_gold.get(gi)
        p_status = pair["pred_status"] if pair else None
        hit = bool(pair is not None and p_status in PROBLEM_STATUSES)
        if hit:
            detected += 1
        problem_rows.append(
            {
                "gold_index": gi,
                "gold_utterance_id": gold_utt.get("id"),
                "gold_text": gold_utt.get("text"),
                "gold_status": gold_utt.get("status"),
                "gold_fields": field_set(gold_utt.get("mappings") or []),
                "paired": pair is not None,
                "match_ratio": pair["ratio"] if pair else None,
                "pred_utterance_id": pair["pred"].get("id") if pair else None,
                "pred_status": p_status,
                "detected": hit,
            }
        )

    false_positives: List[Dict[str, Any]] = []
    for pair in pairs:
        if pair["gold_status"] in PROBLEM_STATUSES:
            continue
        if pair["pred_status"] in PROBLEM_STATUSES:
            false_positives.append(
                {
                    "gold_utterance_id": pair["gold"].get("id"),
                    "gold_text": pair["gold"].get("text"),
                    "gold_status": pair["gold_status"],
                    "pred_utterance_id": pair["pred"].get("id"),
                    "pred_text": pair["pred"].get("text"),
                    "pred_status": pair["pred_status"],
                }
            )

    problem = {
        "gold_problem_count": len(problem_rows),
        "detected": detected,
        "fp_count": len(false_positives),
        "rows": problem_rows,
        "false_positives": false_positives,
    }

    # 5. 不编造
    fabrication = check_fabrication(session, valid_ids)

    return {
        "case_id": case_id,
        "seg": seg,
        "field": field,
        "status": status,
        "problem": problem,
        "fabrication": fabrication,
        "pairs": pair_details,
    }


# ---------------------------------------------------------------------------
# 运行
# ---------------------------------------------------------------------------

def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="衣诺评测脚手架（D0）：通过 HTTP 对开发集做拆句、字段、问题检出、状态、不编造、稳定性与耗时统计。",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8100", help="后端 HTTP 地址")
    parser.add_argument("--repeats", type=int, default=3, help="每个案例重复次数（>=1）")
    parser.add_argument("--cases", default=",".join(DEV_CASE_IDS), help="逗号分隔的开发案例 id；只允许 c1,c2,c3,c4,c6")
    parser.add_argument("--data-dir", default=None, help="data 目录，默认取脚本上级目录下的 data/")
    parser.add_argument("--timeout", type=float, default=120.0, help="单次 HTTP 请求超时（秒）")
    parser.add_argument("--allow-nonmock", action="store_true", help="允许在后端非 MOCK 模式运行（D0 默认禁止）")
    parser.add_argument("--tag", default="d0", help="本次运行标签，用于报告文件名")
    return parser.parse_args(argv)


def validate_cases(raw_cases: str) -> List[str]:
    case_ids = [item.strip() for item in (raw_cases or "").split(",") if item.strip()]
    if not case_ids:
        raise EvalError("未指定任何案例。")
    if HOLDOUT_CASE_ID in case_ids:
        raise EvalError("拒绝运行：c5 是留出集，方案冻结前禁止任何运行。本次未创建 c5 会话。")
    unknown = [cid for cid in case_ids if cid not in DEV_CASE_IDS]
    if unknown:
        raise EvalError(f"只允许开发集 {'、'.join(DEV_CASE_IDS)}，收到未知或非开发案例：{', '.join(unknown)}")
    deduped: List[str] = []
    for cid in case_ids:
        if cid not in deduped:
            deduped.append(cid)
    return deduped


def save_json(path: str, payload: Any) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)

    try:
        case_ids = validate_cases(args.cases)
        if args.repeats < 1:
            raise EvalError(f"--repeats 必须 >= 1，收到 {args.repeats}")

        script_dir = os.path.dirname(os.path.abspath(__file__))
        data_dir = args.data_dir or os.path.join(os.path.dirname(script_dir), "data")
        run_id = datetime.now().strftime("%Y%m%dT%H%M%S")
        runs_dir = os.path.join(script_dir, "runs", run_id)
        reports_dir = os.path.join(script_dir, "reports")
        os.makedirs(runs_dir, exist_ok=True)
        os.makedirs(reports_dir, exist_ok=True)

        gold = load_gold(data_dir, case_ids)
        valid_ids = load_resource_ids(data_dir)

        # 核对标准答案数据本身：开发集问题句共 6 句（仅在跑完整开发集时检查）
        if case_ids == DEV_CASE_IDS:
            actual = {
                cid: sum(1 for u in gold[cid]["utterances"] if u["status"] in PROBLEM_STATUSES)
                for cid in case_ids
            }
            if actual != EXPECTED_GOLD_PROBLEMS:
                raise EvalError(
                    f"标准答案数据与预期不符：问题句数量为 {actual}，预期 {EXPECTED_GOLD_PROBLEMS}。"
                    "评测脚本或数据有误，停止运行。"
                )

        calls: List[Dict[str, Any]] = []
        http = HttpClient(args.base_url, args.timeout, calls)

        health = http.request_json("GET", "/api/health")
        mock_mode = bool(health.get("mock_mode"))

        if not mock_mode and not args.allow_nonmock:
            raise EvalError(
                "拒绝运行：后端不是 MOCK 模式。D0 只在 MOCK_MODE=1 下检验评测脚本；"
                "如确需在模型模式运行，请显式加 --allow-nonmock。"
            )

        samples = http.request_json("GET", "/api/samples")
        if not isinstance(samples, list):
            raise EvalError(f"GET /api/samples 返回格式不对：{type(samples).__name__}")
        samples_by_id = {s.get("id"): s for s in samples if isinstance(s, dict)}

        missing_samples = [cid for cid in case_ids if cid not in samples_by_id]
        if missing_samples:
            raise EvalError(f"GET /api/samples 中缺少开发案例：{', '.join(missing_samples)}")

        for cid in case_ids:
            if samples_by_id[cid].get("holdout") is True:
                raise EvalError(f"示例 {cid} 被标记为留出案例，拒绝运行。本次未创建该会话。")

        # 原始样例只存开发集，避免把留出集内容写进 eval/runs
        save_json(os.path.join(runs_dir, "samples_dev_only.json"), [samples_by_id[cid] for cid in case_ids])
        save_json(os.path.join(runs_dir, "health.json"), health)

        results: Dict[str, List[Dict[str, Any]]] = {cid: [] for cid in case_ids}
        session_meta: Dict[str, List[Dict[str, Any]]] = {cid: [] for cid in case_ids}
        data_warnings: List[Dict[str, Any]] = []
        text_warnings: List[Dict[str, Any]] = []

        for cid in case_ids:
            case_gold = gold[cid]
            sample = samples_by_id[cid]

            sample_text = sample.get("text")
            if sample_text is not None and sample_text != case_gold["text"]:
                warning = {
                    "kind": "text",
                    "case_id": cid,
                    "warning": "GET /api/samples 的原文与 data/cases.json 的客户口述不一致",
                }
                data_warnings.append(warning)
                text_warnings.append(warning)

            existing_spec = sample.get("existing_spec")
            if existing_spec is None:
                existing_spec = case_gold["spec"]
            elif not json_equal(existing_spec, case_gold["spec"]):
                data_warnings.append(
                    {
                        "kind": "spec",
                        "case_id": cid,
                        "warning": "GET /api/samples 的已有方案与 data/cases.json 的方案不一致（标准答案按后者标注）",
                    }
                )
            post_spec = normalize_existing_spec(existing_spec)

            for repeat in range(1, args.repeats + 1):
                payload = {
                    "text": case_gold["text"],
                    "existing_spec": post_spec,
                    "sample_id": cid,
                    "title": case_gold["title"],
                }
                if payload["sample_id"] not in DEV_CASE_IDS:
                    raise EvalError("内部错误：试图为非开发案例创建会话，已中止。")

                session = http.request_json("POST", "/api/sessions", payload)
                post_ms = calls[-1]["elapsed_ms"] if calls else None

                save_json(
                    os.path.join(runs_dir, f"{cid}_r{repeat}.session.json"),
                    {"meta": {"case_id": cid, "repeat": repeat, "at": now_iso()}, "session": session},
                )

                metrics = evaluate_session(cid, session, case_gold, valid_ids)
                spec_versions = session.get("spec_versions") or []
                metrics["repeat"] = repeat
                metrics["post_ms"] = post_ms
                metrics["session_meta"] = {
                    "session_id": session.get("id"),
                    "source": session.get("source"),
                    "mode": session.get("mode"),
                    "created_at": session.get("created_at"),
                    "sample_id": (session.get("input") or {}).get("sample_id"),
                    "version": spec_versions[-1].get("version") if spec_versions else None,
                }
                save_json(os.path.join(runs_dir, f"{cid}_r{repeat}.metrics.json"), metrics)

                results[cid].append(metrics)
                session_meta[cid].append(metrics["session_meta"])

        # ---------------- 汇总 ----------------
        agg = {
            "seg": {k: 0 for k in ("gold", "pred", "paired", "recall_num", "recall_den", "precision_num", "precision_den")},
            "field": {
                k: 0
                for k in ("gold_fields", "pred_fields", "match", "recall_num", "recall_den", "precision_num", "precision_den")
            },
            "status": {"paired": 0, "agree": 0, "loose_agree": 0},
            "problem": {"gold_problem_count": 0, "detected": 0, "fp_count": 0},
            "fabrication_violations": 0,
        }
        for cid in case_ids:
            for metrics in results[cid]:
                for key in agg["seg"]:
                    agg["seg"][key] += metrics["seg"][key]
                for key in agg["field"]:
                    agg["field"][key] += metrics["field"][key]
                for key in agg["status"]:
                    agg["status"][key] += metrics["status"][key]
                agg["problem"]["gold_problem_count"] += metrics["problem"]["gold_problem_count"]
                agg["problem"]["detected"] += metrics["problem"]["detected"]
                agg["problem"]["fp_count"] += metrics["problem"]["fp_count"]
                agg["fabrication_violations"] += len(metrics["fabrication"]["violations"])

        # 稳定性：问题检出结果（每条问题句是否检出 + 误报数）在多次重复间是否一致
        stability: Dict[str, Dict[str, Any]] = {}
        for cid in case_ids:
            signatures = []
            for metrics in results[cid]:
                sig = tuple((row["gold_utterance_id"], row["detected"]) for row in metrics["problem"]["rows"])
                fp_ids = tuple(sorted(fp["gold_utterance_id"] for fp in metrics["problem"]["false_positives"]))
                signatures.append({"signature": sig, "fp_ids": fp_ids})
            unique = {(s["signature"], s["fp_ids"]) for s in signatures}
            stability[cid] = {
                "consistent": len(unique) == 1,
                "signatures": [
                    {
                        "repeat": idx + 1,
                        "detected": {uid: det for uid, det in s["signature"]},
                        "false_positive_gold_ids": list(s["fp_ids"]),
                    }
                    for idx, s in enumerate(signatures)
                ],
            }

        # 耗时
        timing: Dict[str, Any] = {}
        for cid in case_ids:
            values = [m.get("post_ms") for m in results[cid] if isinstance(m.get("post_ms"), (int, float))]
            timing[cid] = {
                "post_ms": values,
                "mean_ms": round(sum(values) / len(values), 1) if values else None,
            }
        all_post = [v for item in timing.values() for v in item["post_ms"]]
        timing["_overall"] = {
            "count": len(all_post),
            "mean_ms": round(sum(all_post) / len(all_post), 1) if all_post else None,
            "min_ms": min(all_post) if all_post else None,
            "max_ms": max(all_post) if all_post else None,
        }

        # 每份方案“无出处”栏数（取最后一次重复）
        ungrounded: Dict[str, Any] = {}
        for cid in case_ids:
            last = results[cid][-1]["fabrication"]
            ungrounded[cid] = {"fields": last["ungrounded_fields"], "count": len(last["ungrounded_fields"])}

        violations: List[Dict[str, Any]] = []
        for cid in case_ids:
            for metrics in results[cid]:
                for violation in metrics["fabrication"]["violations"]:
                    entry = dict(violation)
                    entry["case_id"] = cid
                    entry["repeat"] = metrics["repeat"]
                    violations.append(entry)

        problem_rows_all: List[Dict[str, Any]] = []
        fp_all: List[Dict[str, Any]] = []
        for cid in case_ids:
            for metrics in results[cid]:
                for row in metrics["problem"]["rows"]:
                    entry = dict(row)
                    entry["case_id"] = cid
                    entry["repeat"] = metrics["repeat"]
                    problem_rows_all.append(entry)
                for fp in metrics["problem"]["false_positives"]:
                    entry = dict(fp)
                    entry["case_id"] = cid
                    entry["repeat"] = metrics["repeat"]
                    fp_all.append(entry)

        # MOCK 模式门禁：脚手架自检必须满分
        checks_failed: List[str] = []
        if mock_mode:
            if agg["seg"]["recall_num"] != agg["seg"]["recall_den"]:
                checks_failed.append("拆句召回率未满分")
            if agg["seg"]["precision_num"] != agg["seg"]["precision_den"]:
                checks_failed.append("拆句精确率未满分")
            if agg["field"]["recall_num"] != agg["field"]["recall_den"]:
                checks_failed.append("字段对应召回率未满分")
            if agg["field"]["precision_num"] != agg["field"]["precision_den"]:
                checks_failed.append("字段对应精确率未满分")
            if agg["problem"]["detected"] != agg["problem"]["gold_problem_count"]:
                checks_failed.append("问题检出未全中")
            if agg["problem"]["fp_count"] != 0:
                checks_failed.append("存在误报")
            if agg["status"]["agree"] != agg["status"]["paired"]:
                checks_failed.append("状态一致率未满分")
            if agg["fabrication_violations"] != 0:
                checks_failed.append("存在不编造违规")
            if any(not stability[cid]["consistent"] for cid in case_ids):
                checks_failed.append("多次重复的问题检出结果不一致")
            for cid in case_ids:
                if any(meta.get("source") != "mock" for meta in session_meta[cid]):
                    checks_failed.append(f"{cid} 的会话 source 不是 mock")
                if any(w.get("case_id") == cid for w in text_warnings):
                    checks_failed.append(f"{cid} 的样例原文与标准答案不一致")

        all_meta = [m for cid in case_ids for m in session_meta[cid]]
        sources = sorted({str(m.get("source")) for m in all_meta})

        summary = {
            "script_version": SCRIPT_VERSION,
            "tag": args.tag,
            "run_id": run_id,
            "at": now_iso(),
            "base_url": args.base_url,
            "health": health,
            "mock_mode": mock_mode,
            "repeats": args.repeats,
            "case_ids": case_ids,
            "sessions": len(all_meta),
            "sources": sources,
            "session_meta": session_meta,
            "agg": agg,
            "stability": stability,
            "timing": timing,
            "ungrounded": ungrounded,
            "violations": violations,
            "problems": problem_rows_all,
            "false_positives": fp_all,
            "data_warnings": data_warnings,
            "mock_gate_checked": mock_mode,
            "checks_failed": checks_failed,
            "results": results,
        }
        save_json(os.path.join(runs_dir, "summary.json"), summary)
        with open(os.path.join(runs_dir, "calls.jsonl"), "w", encoding="utf-8") as fh:
            for call in calls:
                fh.write(json.dumps(call, ensure_ascii=False) + "\n")

        markdown = build_report(summary)
        report_path = os.path.join(reports_dir, f"{args.tag}_评测脚手架报告.md")
        with open(report_path, "w", encoding="utf-8") as fh:
            fh.write(markdown)

        # 控制台摘要（给总控看）
        print("=" * 72)
        print(f"衣诺评测 {args.tag} 运行完成：{run_id}")
        print(f"后端：{args.base_url}  mock_mode={mock_mode}  模型={health.get('llm_model')}")
        print(f"案例：{', '.join(case_ids)}  重复 {args.repeats} 次  会话 {len(all_meta)} 个  source={sources}")
        print(
            "拆句：召回 {}；精确 {}".format(
                fmt_frac_pct(agg["seg"]["recall_num"], agg["seg"]["recall_den"]),
                fmt_frac_pct(agg["seg"]["precision_num"], agg["seg"]["precision_den"]),
            )
        )
        print(
            "字段：召回 {}；精确 {}".format(
                fmt_frac_pct(agg["field"]["recall_num"], agg["field"]["recall_den"]),
                fmt_frac_pct(agg["field"]["precision_num"], agg["field"]["precision_den"]),
            )
        )
        print(
            f"问题检出：{agg['problem']['detected']}/{agg['problem']['gold_problem_count']}；"
            f"误报 {agg['problem']['fp_count']} 次"
        )
        print(
            f"状态一致：{agg['status']['agree']}/{agg['status']['paired']}；"
            f"宽松一致：{agg['status']['loose_agree']}/{agg['status']['paired']}"
        )
        print(f"不编造违规：{agg['fabrication_violations']} 条")
        if mock_mode:
            if checks_failed:
                print(f"MOCK 自检：未通过 —— {'；'.join(checks_failed)}")
            else:
                print("MOCK 自检：全部满分（仅说明评测脚本可用，不代表真实模型效果）")
        print(f"报告：{report_path}")
        print(f"原始输出：{runs_dir}")
        print("=" * 72)

        if mock_mode and checks_failed:
            print("请先修评测脚本或核对标准答案，不要对外使用本次数字。", file=sys.stderr)
            return 2
        return 0

    except EvalError as exc:
        print(f"评测中止：{exc}", file=sys.stderr)
        return 2 if str(exc).startswith("拒绝运行") else 1
    except KeyboardInterrupt:
        print("评测被中断。", file=sys.stderr)
        return 1


# ---------------------------------------------------------------------------
# 报告
# ---------------------------------------------------------------------------

def build_report(summary: Dict[str, Any]) -> str:
    health = summary.get("health") or {}
    agg = summary.get("agg") or {}
    case_ids = summary.get("case_ids") or []
    mock_mode = summary.get("mock_mode")
    results = summary.get("results") or {}
    checks_failed = summary.get("checks_failed") or []
    stability = summary.get("stability") or {}
    sources = "、".join(summary.get("sources") or []) or "—"
    repeats = summary.get("repeats") or 0

    if mock_mode:
        if checks_failed:
            conclusion = (
                "MOCK 自检未通过（" + "；".join(checks_failed) + "）：评测脚本或标准答案数据有误，本次数字不可用。"
            )
        else:
            conclusion = (
                f"MOCK 模式下开发集 {len(case_ids)} 个案例 × {repeats} 次全部与标准答案一致，评测脚本的配对与计分逻辑可用；"
                "本结果仅检验脚手架，**不代表真实模型效果**，不得作为能力数字写入参赛方案。"
            )
    else:
        conclusion = (
            "模型模式评测：问题检出 "
            f"{agg.get('problem', {}).get('detected')}/{agg.get('problem', {}).get('gold_problem_count')}，"
            f"误报 {agg.get('problem', {}).get('fp_count')} 次（详见后续章节）。"
        )

    lines: List[str] = []
    lines.append(f"# 衣诺评测 {summary.get('tag', 'd0')}：脚手架自检报告")
    lines.append("")
    lines.append(f"**一句话结论：** {conclusion}")
    lines.append("")
    if mock_mode:
        lines.append(
            "> ⚠️ **模式声明：** 本次运行于后端示例模式（`MOCK_MODE=1`，会话 `source=mock`），"
            "系统返回的就是 `data/cases.json` 的标注结果。满分只证明评测脚本能正确配对和计分，"
            "**不能作为衣诺真实解构效果的任何证据**。"
        )
        lines.append("")

    lines.append("## 1. 运行环境")
    lines.append("")
    lines.append("| 项目 | 值 |")
    lines.append("|---|---|")
    lines.append(f"| 运行编号 | `{summary.get('run_id')}` |")
    lines.append(f"| 脚本版本 | `{summary.get('script_version')}` |")
    lines.append(f"| 运行时间 | {summary.get('at')} |")
    lines.append(f"| 后端地址 | `{summary.get('base_url')}` |")
    lines.append(f"| MOCK 模式 | {'是（1）' if mock_mode else '否（0）'} |")
    lines.append(f"| 大模型名 | `{health.get('llm_model')}` |")
    lines.append(f"| 生图 provider | `{health.get('image_provider')}` |")
    lines.append(f"| 案例 | {', '.join(case_ids)} |")
    lines.append(f"| 重复次数 | {repeats} |")
    lines.append(f"| 会话数 | {summary.get('sessions')} |")
    lines.append(f"| 会话 source | {sources} |")
    lines.append("| 留出集 c5 | 未运行（脚本硬性拒绝） |")
    lines.append("")

    lines.append("## 2. 指标汇总（所有案例 × 所有重复，微平均）")
    lines.append("")
    lines.append("| 指标 | 结果（分子/分母） | 说明 |")
    lines.append("|---|---|---|")
    lines.append(
        f"| 拆句召回率 | {fmt_frac_pct(agg['seg']['recall_num'], agg['seg']['recall_den'])} | "
        "标准原话中有多少句配上了预测原话（重叠 ≥ 0.5、一对一贪心配对） |"
    )
    lines.append(
        f"| 拆句精确率 | {fmt_frac_pct(agg['seg']['precision_num'], agg['seg']['precision_den'])} | "
        "预测原话中有多少句配上了标准原话 |"
    )
    lines.append(
        f"| 字段对应召回率 | {fmt_frac_pct(agg['field']['recall_num'], agg['field']['recall_den'])} | "
        "只看配上的原话，标准字段集合被覆盖的比例（字段集合微平均） |"
    )
    lines.append(
        f"| 字段对应精确率 | {fmt_frac_pct(agg['field']['precision_num'], agg['field']['precision_den'])} | "
        "预测出的字段中有多少在标准答案里 |"
    )
    lines.append(
        f"| 问题句检出 | {frac(agg['problem']['detected'], agg['problem']['gold_problem_count'])}"
        f"（{pct(agg['problem']['detected'], agg['problem']['gold_problem_count'])}） | "
        "标准状态为有冲突/被改写/未落实/需设计师判断的原话，系统是否报成问题状态之一 |"
    )
    lines.append(
        f"| 问题句误报 | {agg['problem']['fp_count']} 次 | "
        "标准答案非问题句，却被系统报成问题状态的次数 |"
    )
    lines.append(
        f"| 状态完全一致率 | {fmt_frac_pct(agg['status']['agree'], agg['status']['paired'])} | "
        "六种状态逐句比较（只看配上的原话） |"
    )
    lines.append(
        f"| 状态宽松一致率 | {fmt_frac_pct(agg['status']['loose_agree'], agg['status']['paired'])} | "
        "把“间接体现”和“已落实”算作同一类后的一致率（单独统计，不和问题检出混在一起） |"
    )
    lines.append(
        f"| 不编造违规 | {agg['fabrication_violations']} 条 | "
        "原话非原文子串/位置不符、资源 id 不存在，逐条列在第 6 节 |"
    )
    lines.append("")

    lines.append("## 3. 分案例结果（取每个案例第 1 次重复；MOCK 下各次相同）")
    lines.append("")
    lines.append("| 案例 | 拆句召回 | 拆句精确 | 字段召回 | 字段精确 | 问题检出 | 误报 | 状态一致 | 无出处栏数 |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for cid in case_ids:
        metrics = results[cid][0]
        ungrounded = (summary.get("ungrounded") or {}).get(cid) or {}
        lines.append(
            "| {} | {} | {} | {} | {} | {} | {} | {} | {} |".format(
                cid,
                fmt_frac_pct(metrics["seg"]["recall_num"], metrics["seg"]["recall_den"]),
                fmt_frac_pct(metrics["seg"]["precision_num"], metrics["seg"]["precision_den"]),
                fmt_frac_pct(metrics["field"]["recall_num"], metrics["field"]["recall_den"]),
                fmt_frac_pct(metrics["field"]["precision_num"], metrics["field"]["precision_den"]),
                frac(metrics["problem"]["detected"], metrics["problem"]["gold_problem_count"]),
                metrics["problem"]["fp_count"],
                fmt_frac_pct(metrics["status"]["agree"], metrics["status"]["paired"]),
                ungrounded.get("count", 0),
            )
        )
    lines.append("")
    lines.append(
        "> 案例“无出处栏数”统计的是当前方案版本中 `origin=无出处` 的字段数。"
        "MOCK 模式下方案来自案例原始规格，若某栏本身没有资源出处，会如实计入。"
    )
    lines.append("")

    grouped: Dict[Tuple[str, str], Dict[int, Dict[str, Any]]] = {}
    for row in summary.get("problems") or []:
        key = (row.get("case_id"), row.get("gold_utterance_id"))
        grouped.setdefault(key, {})[row.get("repeat")] = row
    ordered_keys = sorted(
        grouped.keys(),
        key=lambda k: (
            case_ids.index(k[0]) if k[0] in case_ids else 99,
            min(int(r.get("gold_index") or 0) for r in grouped[k].values()),
        ),
    )

    lines.append(f"## 4. 问题句逐句核对（本次共 {len(ordered_keys)} 句）")
    lines.append("")
    if ordered_keys:
        header_cells = ["案例", "标准原话", "标准状态", "标准字段"]
        header_cells += [f"第 {rep} 次" for rep in range(1, repeats + 1)]
        header_cells.append("结论")
        lines.append("| " + " | ".join(header_cells) + " |")
        lines.append("|" + "---|" * len(header_cells))
        for key in ordered_keys:
            rows_by_rep = grouped[key]
            first = rows_by_rep[min(rows_by_rep)]
            row_cells = [
                str(first.get("case_id")),
                str(first.get("gold_text") or ""),
                str(first.get("gold_status") or ""),
                "、".join(first.get("gold_fields") or []) or "—",
            ]
            all_hit = True
            for rep in range(1, repeats + 1):
                row = rows_by_rep.get(rep)
                if row is None:
                    row_cells.append("未记录")
                    all_hit = False
                    continue
                if row.get("detected"):
                    row_cells.append(f"✓ 检出（{row.get('pred_status')}）")
                else:
                    all_hit = False
                    if row.get("paired"):
                        row_cells.append(f"✗ 漏检（{row.get('pred_status')}）")
                    else:
                        row_cells.append("✗ 未配对")
            row_cells.append("全部检出" if all_hit else "未全部检出")
            lines.append("| " + " | ".join(row_cells) + " |")
    else:
        lines.append("本次案例集没有标准问题句。")
    lines.append("")
    lines.append(
        "> 判定口径：标准状态属于 {有冲突, 被改写, 未落实, 需设计师判断} 即“问题句”；"
        "与之配对的预测原话状态只要也属于这四种之一，即算检出。"
    )
    lines.append("")

    lines.append("## 5. 误报清单")
    lines.append("")
    fps = summary.get("false_positives") or []
    if not fps:
        lines.append("0 次误报：标准答案为非问题状态的原话，系统都没有报成问题状态。")
    else:
        lines.append("| 案例 | 重复 | 标准原话 | 标准状态 | 预测原话 | 预测状态 |")
        lines.append("|---|---|---|---|---|---|")
        for fp in fps:
            lines.append(
                "| {} | {} | {} | {} | {} | {} |".format(
                    fp.get("case_id"),
                    fp.get("repeat"),
                    fp.get("gold_text"),
                    fp.get("gold_status"),
                    fp.get("pred_text"),
                    fp.get("pred_status"),
                )
            )
    lines.append("")

    lines.append("## 6. 不编造检查")
    lines.append("")
    violations = summary.get("violations") or []
    lines.append(
        "逐条检查了：每句原话是否为原文子串、`start/end` 是否与原文一致、"
        "方案字段引用的资源 id 是否存在于 `data/resources.json`、追问选项是否引用了不存在的资源 id。"
    )
    lines.append("")
    if not violations:
        lines.append("违规 0 条。")
    else:
        lines.append("| 案例 | 重复 | 类型 | 字段/原话 | 资源 id |")
        lines.append("|---|---|---|---|---|")
        for violation in violations:
            lines.append(
                "| {} | {} | {} | {} | {} |".format(
                    violation.get("case_id"),
                    violation.get("repeat"),
                    violation.get("type"),
                    violation.get("field") or violation.get("utterance_id"),
                    violation.get("resource_id") or "",
                )
            )
    lines.append("")
    lines.append("每份方案“无出处”栏数：")
    lines.append("")
    lines.append("| 案例 | 无出处字段 | 栏数 |")
    lines.append("|---|---|---|")
    for cid in case_ids:
        item = (summary.get("ungrounded") or {}).get(cid) or {}
        fields = item.get("fields") or []
        lines.append(f"| {cid} | {'、'.join(fields) if fields else '无'} | {item.get('count', 0)} |")
    lines.append("")

    lines.append("## 7. 稳定性（问题检出在多次重复间是否一致）")
    lines.append("")
    lines.append("| 案例 | 各次检出（问题句 id → 是否检出） | 误报 id | 一致 |")
    lines.append("|---|---|---|---|")
    for cid in case_ids:
        item = stability.get(cid) or {}
        sig_texts = []
        fp_texts = []
        for entry in item.get("signatures") or []:
            detected = entry.get("detected") or {}
            sig_texts.append(
                "第{}次：{}".format(
                    entry.get("repeat"),
                    "，".join(f"{uid}={ '✓' if det else '✗'}" for uid, det in detected.items()) or "无问题句",
                )
            )
            fp_ids = entry.get("false_positive_gold_ids") or []
            fp_texts.append("第{}次：{}".format(entry.get("repeat"), "，".join(fp_ids) or "无"))
        lines.append(
            f"| {cid} | {'；'.join(sig_texts)} | {'；'.join(fp_texts)} | {'一致' if item.get('consistent') else '不一致'} |"
        )
    lines.append("")

    lines.append("## 8. 耗时（每次 `POST /api/sessions`）")
    lines.append("")
    lines.append("| 案例 | 各次耗时（毫秒） | 平均（毫秒） |")
    lines.append("|---|---|---|")
    for cid in case_ids:
        item = (summary.get("timing") or {}).get(cid) or {}
        values = item.get("post_ms") or []
        lines.append(f"| {cid} | {' / '.join(str(v) for v in values) or '—'} | {item.get('mean_ms')} |")
    overall = (summary.get("timing") or {}).get("_overall") or {}
    lines.append(
        "| 全部 | 共 {} 次 | 平均 {}（最小 {}，最大 {}） |".format(
            overall.get("count"), overall.get("mean_ms"), overall.get("min_ms"), overall.get("max_ms")
        )
    )
    lines.append("")

    lines.append("## 9. 状态混淆（六种状态，所有重复合计）")
    lines.append("")
    confusion: Counter = Counter()
    for cid in case_ids:
        for metrics in results[cid]:
            for key, count in (metrics["status"].get("confusion") or {}).items():
                confusion[key] += count
    if confusion:
        lines.append("| 标准状态 → 预测状态 | 句数 |")
        lines.append("|---|---|")
        for key in sorted(confusion):
            lines.append(f"| {key} | {confusion[key]} |")
    else:
        lines.append("无配对原话，未统计。")
    lines.append("")

    lines.append("## 10. 局限（务必随数字一起引用）")
    lines.append("")
    lines.append("- 本次是 **MOCK 模式脚手架自检**，系统返回标准答案本身，满分不能说明真实解构能力。")
    lines.append(f"- 开发集只有 {len(case_ids)} 个案例，且开发过程中已反复看过，不能代表未见过的真实客户口述。")
    lines.append("- 注入问题集（D1）尚未构建，本报告没有“举一反三”的泛化数据；对照组（直接问大模型）也尚未运行。")
    lines.append("- 拆句配对采用“最长公共子序列 / 较长者长度 ≥ 0.5”的贪心一对一配对，与人工判断可能有个别出入。")
    lines.append("- 字段对应只比较字段集合，不比较多件套的单品归属，也不评价字段值写得对不对。")
    lines.append("- 标准答案本身由 Claude 整理、未经设计师确认（见 `data/README.md`），错误会同时计入分子和分母。")
    lines.append("- 留出集 c5 本次没有运行，脚本对 c5 做了硬性拒绝，任何参数都不会为它创建会话。")
    lines.append("")

    if summary.get("data_warnings"):
        lines.append("## 11. 数据一致性警告")
        lines.append("")
        for warning in summary.get("data_warnings") or []:
            lines.append(f"- {warning.get('case_id')}：{warning.get('warning')}")
        lines.append("")

    lines.append("## 12. 原始输出")
    lines.append("")
    lines.append(f"- 本次全部原始数据：`eval/runs/{summary.get('run_id')}/`")
    lines.append("  - `cX_rN.session.json`：每次调用的完整 Session 返回")
    lines.append("  - `cX_rN.metrics.json`：该次调用的评测明细（配对、字段、状态、问题句）")
    lines.append("  - `calls.jsonl`：每个 HTTP 调用的地址、状态码、耗时")
    lines.append("  - `summary.json`：汇总指标与全部门禁检查")
    lines.append("  - `samples_dev_only.json`：只含开发案例的样例，未写入留出集内容")
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    sys.exit(main())