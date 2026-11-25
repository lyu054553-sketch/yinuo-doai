import type { Field, LedgerEntry, Session, Status, Strength } from "../api/types";
import { FIELDS, STATUS_ORDER } from "../api/types";

export function formatTime(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${date.getMonth() + 1}月${date.getDate()}日 ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

export function statusKey(status: Status): string {
  switch (status) {
    case "有冲突":
      return "conflict";
    case "被改写":
      return "rewritten";
    case "未落实":
      return "missing";
    case "需设计师判断":
      return "designer";
    case "间接体现":
      return "indirect";
    case "已落实":
      return "done";
    case "已与客户确认":
      return "confirmed";
    default:
      return "indirect";
  }
}

/** 给业务员看的状态说法。后端和数据里仍用原词表，只在界面上换成大白话。 */
export const STATUS_LABEL: Record<Status, string> = {
  有冲突: "前后矛盾",
  被改写: "被改了",
  未落实: "没落实",
  需设计师判断: "交给设计师",
  间接体现: "间接满足",
  已落实: "已落实",
  已与客户确认: "客户已确认",
};

export const STATUS_HINT: Record<Status, string> = {
  有冲突: "方案和客户的话方向相反，要问清楚。",
  被改写: "客户的要求被换成了别的，要确认能不能接受。",
  未落实: "方案里找不到对应的处理。",
  需设计师判断: "有对应处理，到不到位要设计师看。",
  间接体现: "没有专门写，但别的处理能满足。",
  已落实: "方案里能明确对上。",
  已与客户确认: "客户已经回答，结果写进了方案。",
};

export function statusLabel(status: Status): string {
  return STATUS_LABEL[status] ?? status;
}

/** 需要业务员处理的状态。 */
export function needsAction(status: Status): boolean {
  return status === "有冲突" || status === "被改写" || status === "未落实" || status === "需设计师判断";
}

export function sortStatuses(statuses: Status[]): Status[] {
  return [...statuses].sort((a, b) => STATUS_ORDER.indexOf(a) - STATUS_ORDER.indexOf(b));
}

export function countStatuses(session: Session): { status: Status; count: number }[] {
  const map = new Map<Status, number>();
  session.utterances.forEach((u) => {
    map.set(u.status, (map.get(u.status) ?? 0) + 1);
  });
  return sortStatuses(Array.from(map.keys())).map((status) => ({
    status,
    count: map.get(status) ?? 0,
  }));
}

export function currentVersionEntry(session: Session): LedgerEntry {
  return (
    session.ledger[session.ledger.length - 1] ?? {
      version: 1,
      at: session.created_at,
      action: "导入",
      summary: "尚无记录",
      detail: "",
      utterance_ids: [],
      changed_fields: [],
    }
  );
}

/**
 * 本版真正变了的字段：比较相邻两个方案版本的字段取值。
 * 不用台账，避免导入/审计、参考图解析、出图、核对等台账把栏位误标成“本版变更”。
 * v1 没有上一版，返回空数组。
 */
export function lastChangedFields(session: Session): Field[] {
  const versions = session.spec_versions;
  if (versions.length < 2) return [];
  const previous = versions[versions.length - 2];
  const current = versions[versions.length - 1];
  return FIELDS.filter(
    (field) => (previous.fields[field]?.value ?? "") !== (current.fields[field]?.value ?? ""),
  );
}

export const STRENGTH_HINT: Record<Strength, string> = {
  硬约束: "必须满足",
  软偏好: "可以协商",
  目标: "希望的效果",
  描述: "客观信息",
};

export function isSevere(status: Status): boolean {
  return status === "有冲突" || status === "被改写" || status === "未落实";
}

/**
 * 出图来源的中文说明：不把 none / openai_compatible 这类实现值露给业务员。
 * 仅提示词 = 只出了提示词，没有真正出图；外部上传 = 业务员自己上传的效果图。
 */
export function imageProviderLabel(provider: string): string {
  const key = (provider ?? "").trim().toLowerCase();
  if (!key || key === "none" || key === "prompt_only" || key === "prompt-only") return "仅提示词";
  if (key === "upload" || key === "external" || key === "manual" || key === "uploaded") {
    return "外部上传";
  }
  return "生图服务";
}