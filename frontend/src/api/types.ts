// 与《总体说明与接口约定》第 5 节逐字段对应。键用英文，值用中文词表。

export type Field =
  | "品类"
  | "版型结构"
  | "领型"
  | "面料"
  | "色彩"
  | "装饰纹样"
  | "长度比例"
  | "松弛余量";

export const FIELDS: Field[] = [
  "品类",
  "版型结构",
  "领型",
  "面料",
  "色彩",
  "装饰纹样",
  "长度比例",
  "松弛余量",
];

export type Status =
  | "有冲突"
  | "被改写"
  | "未落实"
  | "需设计师判断"
  | "间接体现"
  | "已落实"
  | "已与客户确认";

/** 严重程度从高到低；数字越小越严重 */
export const STATUS_ORDER: Status[] = [
  "有冲突",
  "被改写",
  "未落实",
  "需设计师判断",
  "间接体现",
  "已落实",
  "已与客户确认",
];

export type Strength = "硬约束" | "软偏好" | "目标" | "描述";

export type Confidence = "确定" | "疑似" | "看不出";

export type Mode = "审计已有方案" | "从零生成方案";

export interface Mapping {
  field: Field;
  garment: string | null;
  status: Status;
  note: string;
  rule_id: string | null;
}

export interface Utterance {
  id: string;
  text: string;
  start: number;
  end: number;
  type: string;
  strength: Strength;
  status: Status;
  mappings: Mapping[];
  resolved_by: string | null;
}

export interface SpecField {
  value: string;
  resource_ids: string[];
  utterance_ids: string[];
  origin: "资源库" | "已有方案" | "客户确认" | "人工修改" | "无出处";
  confirmed: boolean;
}

export type SpecFields = Record<Field, SpecField>;

export interface SpecVersion {
  version: number;
  created_at: string;
  reason: string;
  fields: SpecFields;
}

export interface QuestionOption {
  id: string;
  label: string;
  detail: string;
  resource_ids: string[];
  patch: Partial<Record<Field, string>>;
  note: string;
  todo: string | null;
}

export interface Question {
  id: string;
  utterance_ids: string[];
  field: Field;
  title: string;
  context: string;
  why: string;
  options: QuestionOption[];
  answer: string | null;
}

export interface DesignerItem {
  id: string;
  utterance_ids: string[];
  field: Field;
  text: string;
}

export interface SkipItem {
  field: Field | string;
  reason: string;
}

export type LedgerAction =
  | "导入"
  | "审计"
  | "客户确认"
  | "人工修改"
  | "参考图解析"
  | "出图"
  | "核对"
  | "引用旧案"
  | "换面料"
  | "复述确认";

export interface LedgerEntry {
  version: number;
  at: string;
  action: LedgerAction;
  summary: string;
  detail: string;
  utterance_ids: string[];
  changed_fields: Field[];
}

export interface ReferenceField {
  value: string | boolean;
  confidence: Confidence;
  used: boolean;
}

export interface ReferenceAnalysis {
  image_url: string;
  fields: Record<string, ReferenceField>;
  conflicts: { field: Field; message: string; utterance_ids: string[] }[];
  model: string;
}

export type ImageKind = "成衣挂拍" | "面料细节" | "上身效果";

export interface CheckResult {
  items: {
    field: Field;
    expect: string;
    observed: string;
    verdict: "符合" | "不符合" | "看不出";
    confidence: Confidence;
  }[];
  model: string;
}

export interface GeneratedImage {
  id: string;
  version: number;
  kind: ImageKind;
  prompt: string;
  url: string | null;
  provider: string;
  check: CheckResult | null;
}

export interface SessionInput {
  text: string;
  existing_spec: Record<Field, string> | null;
  sample_id: string | null;
}

export interface Session {
  id: string;
  title: string;
  mode: Mode;
  source: "mock" | "llm";
  created_at: string;
  input: SessionInput;
  utterances: Utterance[];
  spec_versions: SpecVersion[];
  questions: Question[];
  designer_items: DesignerItem[];
  skipped: SkipItem[];
  ledger: LedgerEntry[];
  reference: ReferenceAnalysis | null;
  images: GeneratedImage[];
  restatement?: Restatement | null;
}

export interface RestatementPoint {
  id: string;
  field: Field;
  utterance_ids: string[];
  question: string;
  answer: "对" | "不对" | null;
  note: string;
}

export interface Restatement {
  version: number;
  created_at: string;
  text: string;
  points: RestatementPoint[];
  source: "llm" | "template";
}

export type ReuseVerdict = "和本单一样" | "可直接用" | "要按本单改" | "不能照搬" | "本单没提" | "旧单没要求";

export interface SimilarCase {
  case_id: string;
  label: string;
  title: string;
  customer_text: string;
  shared: string[];
  score: number;
  reusable: number;
  fields: { field: Field; value: string; resource_ids: string[]; verdict: ReuseVerdict; reason: string }[];
}

export interface FabricOption {
  id: string;
  name: string;
  source: string;
  simulated: boolean;
  traits: string[];
  current: boolean;
  reasons: { label: string; text: string; matched: boolean }[];
  stock: { sku: string; colorway: string; status: string; stock_m: number; lead_days: number; location: string } | null;
}

export interface SessionSummary {
  id: string;
  title: string;
  created_at: string;
  version: number;
}

export interface Sample {
  id: string;
  title: string;
  text: string;
  existing_spec: Record<Field, string> | null;
  holdout: boolean;
}

export interface HealthInfo {
  ok: boolean;
  mock_mode: boolean;
  llm_model: string;
  image_provider: string;
  vision_ok: boolean;
}

export interface Handoff {
  markdown: string;
}

export interface RequirementsAnalysis {
  model: string;
  generated_at?: string;
  categories: { title: string; utterance_ids: string[] }[];
  model_categories?: { title: string; utterance_ids: string[] }[];
  review?: {
    status: string;
    reviewed_at: string | null;
    note: string;
  };
  revisions?: {
    at: string;
    action: string;
    note?: string;
    categories?: { title: string; utterance_ids: string[] }[];
    before?: { title: string; utterance_ids: string[] }[];
    after?: { title: string; utterance_ids: string[] }[];
  }[];
}

export interface PromptItem {
  kind: ImageKind;
  text: string;
}

export interface PromptsResult {
  version: number;
  items: PromptItem[];
  checks: { field: Field; expect: string }[];
}

/** 资源条目：版型、肩型、领型、面料、纹样、色彩、长度、工艺共用，都有 name。 */
export interface ResourceItem {
  id: string;
  name: string;
  garment?: string;
  sources?: string[];
  traits?: string[];
  note?: string;
  caution?: string;
  [key: string]: unknown;
}

/** 身形规则条目：data/resources.json 里 body_rules 本来没有 name，单独定义，不伪造。 */
export interface BodyRuleItem {
  id: string;
  condition: string;
  treatment: string;
  fields: Field[];
  sources?: string[];
  confidence?: string;
  name?: string;
  [key: string]: unknown;
}

export interface ResourceLibrary {
  silhouettes?: ResourceItem[];
  shoulders?: ResourceItem[];
  collars?: ResourceItem[];
  fabrics?: ResourceItem[];
  patterns?: ResourceItem[];
  colors?: ResourceItem[];
  lengths?: ResourceItem[];
  crafts?: ResourceItem[];
  body_rules?: BodyRuleItem[];
  /** 模拟仓库（演示数据）：货号、色号、库存米数、状态 */
  inventory?: Array<Record<string, unknown>>;
  [key: string]: unknown;
}

export interface ResourceProfileItem extends ResourceItem {
  group: string;
  group_label: string;
  style_tags: string[];
  scene_tags: string[];
  audience_tags: string[];
  usage_guidance: string[];
  evidence: string[];
  /** 模拟资源库条目（演示数据） */
  simulated?: boolean;
  basis_cases: string[];
  recommendation_eligible: boolean;
  missing_metadata: string[];
}

export interface ResourceCatalog {
  meta: {
    source: string;
    evidence_policy: string;
    availability: string;
  };
  items: ResourceProfileItem[];
  counts: {
    total: number;
    recommendable: number;
  };
}

export interface CreateSessionRequest {
  text: string;
  existing_spec?: Record<Field, string> | null;
  sample_id?: string | null;
  title?: string;
}

export interface AnswerRequest {
  question_id: string;
  option_id: string;
}

export interface PatchSpecRequest {
  field: Field;
  value: string;
  reason: string;
}
