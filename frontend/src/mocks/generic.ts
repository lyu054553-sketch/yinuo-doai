import type { Field, Mode, Session, SpecField, SpecFields, Status, Utterance } from "../api/types";
import { FIELDS, STATUS_ORDER } from "../api/types";

// 示例数据模式下，案例二以外的示例案例用这套简单规则生成会话：
// 只做解构与审计展示，不生成追问（追问脚本只完整覆盖案例二）。
const FIELD_KEYWORDS: { field: Field; words: string[] }[] = [
  { field: "领型", words: ["领"] },
  { field: "面料", words: ["面料", "真丝", "宋锦", "锦", "缎", "纱", "醋酸"] },
  { field: "装饰纹样", words: ["纹", "花", "绣"] },
  { field: "色彩", words: ["色", "配色"] },
  { field: "长度比例", words: ["长", "短", "比例"] },
  { field: "松弛余量", words: ["宽松", "松量", "紧", "束缚", "放量", "活动"] },
  { field: "版型结构", words: ["版型", "肩", "腰", "摆", "廓形"] },
  { field: "品类", words: ["外套", "连衣裙", "套装", "演出服", "内搭"] },
];

function guessField(text: string): Field {
  for (const rule of FIELD_KEYWORDS) {
    if (rule.words.some((w) => text.includes(w))) return rule.field;
  }
  return "版型结构";
}

function splitClauses(text: string): { text: string; start: number; end: number }[] {
  const parts = text.split(/(?<=[。；！？])/);
  const result: { text: string; start: number; end: number }[] = [];
  let cursor = 0;
  parts.forEach((raw) => {
    const trimmed = raw.trim();
    if (!trimmed) {
      cursor += raw.length;
      return;
    }
    const start = text.indexOf(trimmed, cursor);
    const at = start < 0 ? cursor : start;
    result.push({ text: trimmed, start: at, end: at + trimmed.length });
    cursor = at + trimmed.length;
  });
  return result.length ? result : [{ text, start: 0, end: text.length }];
}

export function buildGenericSession(options: {
  id: string;
  title: string;
  text: string;
  existingSpec: Record<Field, string> | null;
  mode: Mode;
  sampleId: string | null;
}): Session {
  const at = new Date().toISOString();
  const clauses = splitClauses(options.text);
  const utterances: Utterance[] = clauses.map((clause, index) => {
    const field = guessField(clause.text);
    const strength = /不要|不能|必须|要/.test(clause.text) ? "硬约束" : "软偏好";
    const status: Status = strength === "硬约束" ? "需设计师判断" : "间接体现";
    const utterance: Utterance = {
      id: `u${index + 1}`,
      text: clause.text,
      start: clause.start,
      end: clause.end,
      type: field,
      strength,
      status,
      mappings: [
        {
          field,
          garment: null,
          status,
          note:
            status === "需设计师判断"
              ? "这句话是硬性要求，现有方案里有没有做到，需要设计师判断。"
              : "现有方案没有专门字段，只能从相关处理间接看出。",
          rule_id: null,
        },
      ],
      resolved_by: null,
    };
    utterance.status = [...utterance.mappings]
      .sort((a, b) => STATUS_ORDER.indexOf(a.status) - STATUS_ORDER.indexOf(b.status))[0]
      .status;
    return utterance;
  });

  const fields = {} as SpecFields;
  FIELDS.forEach((field) => {
    const raw = options.existingSpec?.[field]?.trim() ?? "";
    const hasValue = raw && raw !== "无特殊需求";
    const value = hasValue ? raw : "无特殊需求";
    const specField: SpecField = {
      value,
      resource_ids: [],
      utterance_ids: utterances.filter((u) => u.mappings.some((m) => m.field === field)).map((u) => u.id),
      origin: hasValue ? "已有方案" : "无出处",
      confirmed: false,
    };
    fields[field] = specField;
  });

  return {
    id: options.id,
    title: options.title,
    mode: options.mode,
    source: "mock",
    created_at: at,
    input: { text: options.text, existing_spec: options.existingSpec, sample_id: options.sampleId },
    utterances,
    spec_versions: [{ version: 1, created_at: at, reason: "导入示例数据", fields }],
    questions: [],
    designer_items: utterances
      .filter((u) => u.status === "需设计师判断")
      .slice(0, 3)
      .map((u, i) => ({
        id: `d${i + 1}`,
        utterance_ids: [u.id],
        field: u.mappings[0].field,
        text: `${u.text}——现有方案是否到位，需要设计师判断。`,
      })),
    skipped: [
      { field: "追问", reason: "示例数据模式只完整覆盖案例二，这个案例暂不生成追问脚本。" },
    ],
    ledger: [
      {
        version: 1,
        at,
        action: "导入",
        summary: "导入客户口述与已有方案（示例数据）",
        detail: `口述 ${utterances.length} 句。`,
        utterance_ids: [],
        changed_fields: [],
      },
      {
        version: 1,
        at,
        action: "审计",
        summary: "完成需求落实审计",
        detail: "示例数据模式只做基础审计，追问脚本未覆盖这个案例。",
        utterance_ids: [],
        changed_fields: [],
      },
    ],
    reference: null,
    images: [],
  };
}