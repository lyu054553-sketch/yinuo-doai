#!/usr/bin/env python3
"""衣诺 · 注入问题集离线校验（D1）。

只读本地文件，不联网、不调模型、不启动服务。用来在把数据交给项目负责人审核之前，
检查 eval/datasets/injected.json 是否满足任务 D1 的硬性要求。

用法：
    python eval/validate_injected.py
    python eval/validate_injected.py --dataset eval/datasets/injected.json

退出码：0 = 全部通过；1 = 有检查未通过。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

FIELDS = ["品类", "版型结构", "领型", "面料", "色彩", "装饰纹样", "长度比例", "松弛余量"]
PROBLEM_STATUSES = ["有冲突", "被改写", "未落实", "需设计师判断"]
ALL_STATUSES = ["有冲突", "被改写", "未落实", "需设计师判断", "间接体现", "已落实", "已与客户确认"]
HOLDOUT_ONLY_RESOURCE_IDS = {"F7", "P2", "CO2"}  # 只来自留出集 c5，禁止引用
HOLDOUT_CASE_IDS = {"c5"}  # 留出集案例：任何比对都要跳过，且不得读取或打印其内容

# 被测输入（customer_text + existing_spec）不得出现的答案提示词，防止标签泄漏
ANSWER_HINTS = ["测试注入", "虚构", "资源库", "不存在", "无出处", "编造", "查无", "库存"]

UTTERANCE_TYPES = {"描述", "风格", "身形", "领型", "松量", "纹样", "面料", "色彩", "场景", "活动", "装饰"}
STRENGTHS = {"硬约束", "软偏好", "目标", "描述"}

REQUIRED_CATEGORIES = [
    "软偏好被改写",
    "宽松与收腰冲突",
    "身形要求遗漏",
    "虚构面料（测试注入）",
]


class Checker:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.notes: list[str] = []

    def check(self, cond: bool, msg: str) -> bool:
        if not cond:
            self.errors.append(msg)
        return cond

    def note(self, msg: str) -> None:
        self.notes.append(msg)


def load_json(path: Path):
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def collect_resource_ids(resources: dict) -> tuple[set[str], set[str]]:
    """返回 (所有资源 id, 所有资源名称)。只遍历含 id 的列表型条目。"""
    ids: set[str] = set()
    names: set[str] = set()
    for key, value in resources.items():
        if key == "meta" or not isinstance(value, list):
            continue
        for item in value:
            if isinstance(item, dict) and "id" in item:
                ids.add(str(item["id"]))
                if "name" in item:
                    names.add(str(item["name"]))
    return ids, names


def main() -> int:
    here = Path(__file__).resolve().parent
    repo_root = here.parent
    parser = argparse.ArgumentParser(description="校验注入问题集（离线，不联网）")
    parser.add_argument("--dataset", default=str(here / "datasets" / "injected.json"))
    parser.add_argument("--resources", default=str(repo_root / "data" / "resources.json"))
    args = parser.parse_args()

    c = Checker()
    dataset_path = Path(args.dataset)
    resources_path = Path(args.resources)

    if not c.check(dataset_path.exists(), f"找不到数据文件：{dataset_path}"):
        print("\n".join(c.errors))
        return 1
    if not c.check(resources_path.exists(), f"找不到资源库：{resources_path}"):
        print("\n".join(c.errors))
        return 1

    data = load_json(dataset_path)
    resources = load_json(resources_path)
    resource_ids, resource_names = collect_resource_ids(resources)

    meta = data.get("meta", {})
    groups = data.get("groups", [])

    # ---------- 顶层 ----------
    c.check(meta.get("status") == "pending_review", "meta.status 应为 pending_review（待审核）")
    c.check(isinstance(groups, list), "groups 应为数组")
    if not isinstance(groups, list):
        print("\n".join(c.errors))
        return 1
    c.check(len(groups) == 20, f"应为 20 组，实际 {len(groups)} 组")
    c.check(meta.get("fields") == FIELDS, "meta.fields 必须按约定的八个字段顺序")
    c.check(set(meta.get("problem_statuses", [])) == set(PROBLEM_STATUSES),
            "meta.problem_statuses 与约定不一致")
    c.check(bool(str(meta.get("resources_note", "")).strip()),
            "meta.resources_note 缺失：需说明 resources 只是对照候选，不代表原方案已采用这些 id")
    c.check(bool(str(meta.get("injected_label_note", "")).strip()),
            "meta.injected_label_note 缺失：需说明“测试注入”标注只在元数据、不在被测输入")

    seen_ids: set[str] = set()
    seen_issue_ids: set[str] = set()
    clean_ids: list[str] = []
    category_counts: dict[str, int] = {k: 0 for k in REQUIRED_CATEGORIES}
    total_issues = 0
    status_counts: dict[str, int] = {k: 0 for k in PROBLEM_STATUSES}

    for gi, g in enumerate(groups, start=1):
        gid = g.get("id", f"#{gi}")
        prefix = f"[{gid}]"

        c.check(bool(gid), f"{prefix} 缺少 id")
        c.check(gid not in seen_ids, f"{prefix} id 重复")
        seen_ids.add(gid)
        c.check(isinstance(g.get("title"), str) and bool(g.get("title")), f"{prefix} 缺少 title")
        c.check(isinstance(g.get("category"), str) and bool(g.get("category")), f"{prefix} 缺少 category")

        text = g.get("customer_text", "")
        c.check(isinstance(text, str) and len(text) >= 20, f"{prefix} customer_text 过短或缺失")

        # 八字段
        spec = g.get("existing_spec", {})
        c.check(isinstance(spec, dict), f"{prefix} existing_spec 应为对象")
        c.check(list(spec.keys()) == FIELDS,
                f"{prefix} existing_spec 字段应恰好是八个且顺序正确，实际 {list(spec.keys())}")
        for f in FIELDS:
            v = spec.get(f, "")
            c.check(isinstance(v, str) and v.strip() != "", f"{prefix} 字段“{f}”为空或不是字符串")

        # 标签只在 test_injected 元数据里，被测输入不得含答案提示（否则等于把答案抄给系统）
        exposed = text + "\n" + "\n".join(str(spec.get(f, "")) for f in FIELDS)
        for hint in ANSWER_HINTS:
            c.check(hint not in exposed,
                    f"{prefix} 被测输入（customer_text/existing_spec）出现了答案提示“{hint}”，"
                    "会造成标签泄漏，请删掉，只保留面料名称与属性")

        # 资源 id
        rids = g.get("resources", [])
        c.check(isinstance(rids, list), f"{prefix} resources 应为数组")
        for rid in rids:
            c.check(rid in resource_ids, f"{prefix} 引用了资源库中不存在的 id：{rid}")
            c.check(rid not in HOLDOUT_ONLY_RESOURCE_IDS,
                    f"{prefix} 引用了只来自留出集 c5 的资源：{rid}")

        # 测试注入的虚构面料
        injected = g.get("test_injected", [])
        c.check(isinstance(injected, list), f"{prefix} test_injected 应为数组")
        for item in injected:
            f = item.get("field")
            value = str(item.get("value", ""))
            c.check(f in FIELDS, f"{prefix} test_injected 的 field 不在八个字段内：{f}")
            c.check(bool(value), f"{prefix} test_injected 缺少 value")
            c.check("测试注入" in (str(item.get("label", "")) + str(item.get("note", ""))),
                    f"{prefix} 虚构面料未标明“测试注入”")
            c.check(value not in resource_names,
                    f"{prefix} 虚构面料“{value}”竟然与资源库名称相同，需换名")
            # “测试注入”标注只允许出现在 test_injected 元数据里；
            # 被测输入（existing_spec）只保留面料名称与属性，下面单独检查不得出现答案提示。
            spec_val = str(spec.get(f, "")) if f in FIELDS else ""
            c.check(value in spec_val,
                    f"{prefix} 方案字段“{f}”应保留面料名称“{value}”（去掉提示词、保留名称与属性）")

        # 原话
        utterances = g.get("utterances", [])
        if not c.check(isinstance(utterances, list), f"{prefix} utterances 应为数组"):
            continue
        c.check(len(utterances) >= 4, f"{prefix} 原话条数应不少于 4 条，实际 {len(utterances)}")
        utt_mappings: dict[str, list] = {}
        problem_utts: list[str] = []
        for ui, u in enumerate(utterances):
            if not c.check(isinstance(u, list) and len(u) == 4,
                           f"{prefix} 第 {ui + 1} 条原话格式应为 [text,type,strength,[mappings]]"):
                continue
            utext, utype, ustrength, mappings = u
            c.check(isinstance(utext, str) and utext.strip() != "", f"{prefix} 第 {ui + 1} 条原话为空")
            c.check(utext in text, f"{prefix} 原话不是原文子串：{utext}")
            c.check(utext not in utt_mappings, f"{prefix} 组内原话重复：{utext}")
            utt_mappings[utext] = mappings
            c.check(utype in UTTERANCE_TYPES, f"{prefix} 原话类型不常见：{utype}")
            c.check(ustrength in STRENGTHS, f"{prefix} 原话强度不合法：{ustrength}")
            c.check(isinstance(mappings, list) and len(mappings) >= 1,
                    f"{prefix} 原话“{utext}”没有映射")
            has_problem = False
            if isinstance(mappings, list):
                for m in mappings:
                    if not c.check(isinstance(m, list) and len(m) == 3,
                                   f"{prefix} 映射格式应为 [field,status,note]：{m}"):
                        continue
                    mfield, mstatus, mnote = m
                    c.check(mfield in FIELDS, f"{prefix} 映射字段不在八个字段内：{mfield}")
                    c.check(mstatus in ALL_STATUSES, f"{prefix} 映射状态不合法：{mstatus}")
                    c.check(isinstance(mnote, str) and mnote.strip() != "",
                            f"{prefix} 映射“{mfield}”缺少说明")
                    if mstatus in PROBLEM_STATUSES:
                        has_problem = True
            if has_problem:
                problem_utts.append(utext)

        # 问题定义
        issues = g.get("expected_issues", [])
        c.check(isinstance(issues, list), f"{prefix} expected_issues 应为数组")
        if not isinstance(issues, list):
            issues = []
        c.check(len(issues) <= 2, f"{prefix} 每组最多埋 2 个问题，实际 {len(issues)} 个")
        c.check(g.get("issue_count") == len(issues),
                f"{prefix} issue_count={g.get('issue_count')} 与 expected_issues 条数 {len(issues)} 不一致")
        c.check(len(problem_utts) == len(issues),
                f"{prefix} 标准答案不自洽：状态为问题句的原话 {len(problem_utts)} 条"
                f"（{problem_utts}），expected_issues {len(issues)} 条")
        c.check(bool(g.get("clean")) == (len(issues) == 0),
                f"{prefix} clean 标记与问题数不一致（问题数 {len(issues)}）")
        if len(issues) == 0:
            clean_ids.append(gid)

        total_issues += len(issues)
        for it in issues:
            iid = it.get("utterance_id", "")
            c.check(bool(iid) and iid.startswith(gid + "-"), f"{prefix} 问题 id 格式异常：{iid}")
            c.check(iid not in seen_issue_ids, f"{prefix} 问题 id 重复：{iid}")
            seen_issue_ids.add(iid)
            itext = it.get("text", "")
            c.check(itext in utt_mappings, f"{prefix} 问题的原话不在本组原话列表中：{itext}")
            c.check(itext in text, f"{prefix} 问题的原话不是原文子串：{itext}")
            c.check(it.get("field") in FIELDS, f"{prefix} 问题字段不在八个字段内：{it.get('field')}")
            c.check(it.get("expected_status") in PROBLEM_STATUSES,
                    f"{prefix} 问题标准状态应为四类问题状态之一：{it.get('expected_status')}")
            c.check(it.get("category") in meta.get("issue_categories", {}),
                    f"{prefix} 问题类别未在 meta.issue_categories 中登记：{it.get('category')}")
            c.check(isinstance(it.get("reason"), str) and len(it.get("reason", "")) >= 15,
                    f"{prefix} 问题原因过短，需写清为什么算问题")
            c.check(isinstance(it.get("normal_control"), str) and len(it.get("normal_control", "")) >= 10,
                    f"{prefix} 缺少“正常对照”写法")
            cat = it.get("category")
            if cat in category_counts:
                category_counts[cat] += 1
            st = it.get("expected_status")
            if st in status_counts:
                status_counts[st] += 1
            # 问题原话的映射里必须真有一条达到该字段与该状态
            mappings = utt_mappings.get(itext, [])
            c.check(any(isinstance(m, list) and len(m) == 3
                        and m[0] == it.get("field") and m[1] == it.get("expected_status")
                        for m in mappings),
                    f"{prefix} 问题“{itext}”在 {it.get('field')} 上没有对应的 "
                    f"{it.get('expected_status')} 映射")

    # ---------- 组间统计 ----------
    c.check(len(clean_ids) >= 4, f"完全无问题的组应至少 4 组，实际 {len(clean_ids)} 组：{clean_ids}")
    c.check(sorted(clean_ids) == sorted(meta.get("clean_group_ids", [])),
            f"meta.clean_group_ids 与实际无问题组不一致：{meta.get('clean_group_ids')} vs {clean_ids}")
    c.check(total_issues == meta.get("expected_total_issues"),
            f"meta.expected_total_issues={meta.get('expected_total_issues')} 与实际问题总数 {total_issues} 不一致")
    c.check(0 < total_issues <= 20 * 2, f"问题总数异常：{total_issues}")
    for cat in REQUIRED_CATEGORIES:
        c.check(category_counts[cat] >= 1, f"类别“{cat}”没有任何样本")

    injected_values = [str(item.get("value", ""))
                       for g in groups for item in (g.get("test_injected", []) or [])]
    c.check(len(injected_values) >= 2, "应至少有 2 组使用测试注入的虚构面料")
    for v in injected_values:
        c.check(v not in resource_names, f"虚构面料“{v}”与资源库名称相同")

    # 不照抄案例：只比较“完整客户口述”是否与开发案例整段完全相同；
    # 留出集（c5/holdout）一律跳过，不读取、不比对、不打印其内容。普通短语复用是允许的。
    cases_path = repo_root / "data" / "cases.json"
    if cases_path.exists():
        try:
            cases = load_json(cases_path)
            dev_texts: dict[str, str] = {}
            skipped = 0
            for case in cases.get("cases", []):
                if not isinstance(case, dict):
                    continue
                cid = str(case.get("id", ""))
                if (cid in HOLDOUT_CASE_IDS
                        or str(case.get("split", "")).lower() != "dev"
                        or case.get("holdout") is True):
                    skipped += 1
                    continue  # 只比对明确标注为 dev 的开发案例；留出集或其他一律跳过，不取原文
                ctext = str(case.get("customer_text", "") or "").strip()
                if ctext:
                    dev_texts[ctext] = cid
            for g in groups:
                ctext = str(g.get("customer_text", "")).strip()
                c.check(ctext not in dev_texts,
                        f"[{g.get('id')}] 完整客户口述与开发案例 {dev_texts.get(ctext)} 完全相同，需改写")
            c.note(f"已对 {len(dev_texts)} 个开发案例做“整段口述完全相同”检查；"
                   f"跳过留出集或其他非开发案例 {skipped} 个（不读取其内容）。普通短语复用允许。")
        except (json.JSONDecodeError, OSError):
            c.note("data/cases.json 读取失败，跳过“整段口述完全相同”检查")
    else:
        c.note("未找到 data/cases.json，跳过“整段口述完全相同”检查")

    # ---------- 输出 ----------
    print("=" * 60)
    print("衣诺 · 注入问题集离线校验（不联网，不调模型）")
    print("=" * 60)
    print(f"数据文件：{dataset_path}")
    print(f"资源库：  {resources_path}（共 {len(resource_ids)} 个资源 id）")
    print(f"组数：{len(groups)}；完全无问题组：{len(clean_ids)} 组 {clean_ids}")
    print(f"埋入问题：{total_issues} 个；按状态 {status_counts}")
    print("类别覆盖：")
    for cat in REQUIRED_CATEGORIES:
        print(f"  - {cat}：{category_counts[cat]} 个")
    for n in c.notes:
        print(f"[提示] {n}")
    print("-" * 60)
    if c.errors:
        print(f"未通过：{len(c.errors)} 项")
        for e in c.errors:
            print(f"  ✗ {e}")
        return 1
    print("通过：20 组、八字段齐全、每组 0–2 个问题、问题与原话自洽、资源 id 均真实存在、")
    print("      被测输入（口述＋方案）无答案提示、虚构面料只在元数据标测试注入、至少 4 组完全无问题；")
    print("      整段口述与开发案例均不相同，留出集已跳过未读取。可以交项目负责人审核。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
