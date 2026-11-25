import type {
  Field,
  Question,
  QuestionOption,
  Session,
  SpecField,
  SpecFields,
  Status,
  Utterance,
} from "../api/types";
import { FIELDS, STATUS_ORDER } from "../api/types";
import { MOCK_RESOURCES } from "./resources";
import fixture from "./case2.fixture.json";

// 案例二（开发集）的标注数据，原样复制自后端会话
// backend/storage/sessions/s7051091d5586.json（已去掉无依据的“胸围＋4cm”），
// 不手工重写、不补编尺寸。示例数据模式只克隆它，改会话 id、标题和日期。
const CASE2_FIXTURE = fixture as unknown as Session;

export const CASE2_TITLE = "案例二：中式正式外套（窄领、内搭、梅花暗纹）";

export const CASE2_TEXT = CASE2_FIXTURE.input.text;

export const CASE2_SPEC = CASE2_FIXTURE.input.existing_spec as Record<Field, string>;

function isoNow(): string {
  return new Date().toISOString();
}

function cloneSession(session: Session): Session {
  return JSON.parse(JSON.stringify(session)) as Session;
}

function cloneFields(fields: SpecFields): SpecFields {
  return JSON.parse(JSON.stringify(fields)) as SpecFields;
}

/** 克隆案例二的已验收会话，只替换 id、标题与日期。 */
export function buildCase2Session(id = "mock-c2", existingTitle = CASE2_TITLE): Session {
  const session = cloneSession(CASE2_FIXTURE);
  const at = isoNow();
  session.id = id;
  session.title = existingTitle || CASE2_TITLE;
  session.created_at = at;
  session.input = {
    text: CASE2_TEXT,
    existing_spec: CASE2_SPEC ? { ...CASE2_SPEC } : null,
    sample_id: "c2",
  };
  session.spec_versions = session.spec_versions.map((v) => ({ ...v, created_at: at }));
  session.ledger = session.ledger.map((e) => ({ ...e, at }));
  session.questions = session.questions.map((q) => ({ ...q }));
  return session;
}

function currentVersion(session: Session) {
  return session.spec_versions[session.spec_versions.length - 1];
}

function currentFields(session: Session): SpecFields {
  return currentVersion(session).fields;
}

function mostSevere(statuses: Status[]): Status {
  if (statuses.length === 0) return "未落实";
  return [...statuses].sort((a, b) => STATUS_ORDER.indexOf(a) - STATUS_ORDER.indexOf(b))[0];
}

function untouchedNote(changed: string[]): string {
  const others = FIELDS.filter((field) => !changed.includes(field));
  return `其余设计字段没有改动：${others.join("、")}。`;
}

function utteranceTexts(session: Session, utteranceIds: string[]): string {
  const byId = new Map(session.utterances.map((u) => [u.id, u]));
  return utteranceIds
    .map((id) => byId.get(id))
    .filter((u): u is Utterance => Boolean(u))
    .map((u) => `「${u.text}」`)
    .join("、");
}

// ---------------------------------------------------------------------------
// 资源出处：只认示例资源库（backend/storage/fixtures/dev-resources.json 的复制件），
// 对不上就不给来源，绝不把旧出处留在新值上。
// ---------------------------------------------------------------------------

interface MiniResource {
  id: string;
  name: string;
  sources?: string[];
}

const RESOURCE_DOC = MOCK_RESOURCES as Record<string, unknown>;

function readGroup(key: string): MiniResource[] {
  const raw = RESOURCE_DOC[key];
  if (!Array.isArray(raw)) return [];
  return raw
    .filter((item): item is Record<string, unknown> => typeof item === "object" && item !== null)
    .filter((item) => typeof item.id === "string")
    .map((item) => ({
      id: item.id as string,
      name: typeof item.name === "string" ? item.name : "",
      sources: Array.isArray(item.sources) ? (item.sources as string[]) : undefined,
    }));
}

const RESOURCE_GROUPS: Record<string, MiniResource[]> = {
  silhouettes: readGroup("silhouettes"),
  shoulders: readGroup("shoulders"),
  collars: readGroup("collars"),
  fabrics: readGroup("fabrics"),
  patterns: readGroup("patterns"),
  colors: readGroup("colors"),
  lengths: readGroup("lengths"),
};

/** 设计字段 → 资源库里的集合，与后端 knowledge.FIELD_RESOURCE_KEYS 一致。 */
const FIELD_RESOURCE_KEYS: Partial<Record<Field, string[]>> = {
  版型结构: ["silhouettes", "shoulders"],
  领型: ["collars"],
  面料: ["fabrics"],
  色彩: ["colors"],
  装饰纹样: ["patterns"],
  长度比例: ["lengths"],
};

/** 文本匹配关键词，照抄后端 knowledge_attrs.json 的 match_keywords，不额外编造。 */
const MATCH_KEYWORDS: Record<string, string[]> = {
  S1: ["H型直身，无收腰", "H型直身结构，无收腰", "无收腰"],
  S2: ["微微收腰", "微收腰"],
  S3: ["短款微H型"],
  S4: ["收腰大A摆"],
  SH1: ["微落平肩"],
  SH2: ["自然落肩"],
  SH3: ["自然平肩", "不压肩"],
  CL1: ["低立领"],
  CL2: ["青果西装领", "青果领"],
  CL3: ["中式斜襟领", "斜襟领"],
  CL4: ["小圆领"],
  CL5: ["交领开襟", "交领"],
  F1: ["醋酸"],
  F2: ["薄款提花锦", "提花锦"],
  F3: ["提花宋锦", "宋锦"],
  F4: ["真丝里布"],
  F5: ["亮面缎面", "缎面"],
  F6: ["香云纱"],
  F8: ["真丝暗纹织花", "暗纹织花"],
  P1: ["植物暗花", "点状星光"],
  P3: ["四瓣花"],
  CO1: ["亮粉", "米白底"],
  L1: ["衣长至臀中", "臀中"],
  L2: ["裙长至脚踝", "脚踝"],
};

const RESOURCE_INDEX = new Map<string, MiniResource>();
Object.values(RESOURCE_GROUPS).forEach((list) => {
  list.forEach((item) => RESOURCE_INDEX.set(item.id, item));
});

const CASE_LABELS: Record<string, string> = {
  c1: "案例一",
  c2: "案例二",
  c3: "案例三",
  c4: "案例四",
  c6: "案例六",
};

function normalize(text: string): string {
  return String(text ?? "").replace(/\s+/g, "");
}

function resourceSourceText(resourceId: string): string {
  const item = RESOURCE_INDEX.get(resourceId);
  if (!item || !item.sources || item.sources.length === 0) return "未注明来源";
  return item.sources.map((src) => CASE_LABELS[src] ?? src).join("、");
}

function resourceText(resourceIds: string[]): string {
  if (!resourceIds.length) return "无资源出处";
  const parts = resourceIds
    .map((rid) => {
      const item = RESOURCE_INDEX.get(rid);
      if (!item) return "";
      return `${rid} ${item.name}（${resourceSourceText(rid)}）`;
    })
    .filter(Boolean);
  return parts.length ? parts.join("；") : "无资源出处";
}

/**
 * 把方案里的一栏文字对应到资源 id；对应不上返回空数组。
 * 与后端 knowledge.find_resource_ids 同规则：先看资源名是否出现在文字里，再看关键词。
 */
function findResourceIds(field: Field, text: string): string[] {
  const plain = normalize(text);
  if (!plain) return [];
  const ids: string[] = [];
  (FIELD_RESOURCE_KEYS[field] ?? []).forEach((key) => {
    (RESOURCE_GROUPS[key] ?? []).forEach((item) => {
      const name = normalize(item.name);
      if (name && plain.includes(name)) {
        ids.push(item.id);
        return;
      }
      const keywords = MATCH_KEYWORDS[item.id] ?? [];
      if (keywords.some((keyword) => plain.includes(normalize(keyword)))) {
        ids.push(item.id);
      }
    });
  });
  return ids;
}

// ---------------------------------------------------------------------------
// 回答客户问题：与后端 backend/app/pipeline/revise.py 的 apply_answer 对齐
// ---------------------------------------------------------------------------

export function answerQuestion(
  session: Session,
  questionId: string,
  optionId: string,
): Session {
  const question = session.questions.find((q) => q.id === questionId);
  if (!question) throw new Error("这个问题不存在，可能已被处理。");
  const option = question.options.find((o) => o.id === optionId);
  if (!option) throw new Error("没有这个选项，请重新选择。");

  // 同一题重复选同一个选项：幂等返回，不升版本、不记台账。
  if (question.answer === option.id) return session;

  const next = cloneSession(session);
  const target = next.questions.find((q) => q.id === questionId);
  if (!target) throw new Error("这个问题不存在，可能已被处理。");
  const current = currentVersion(next);

  const patchFields = Object.keys(option.patch) as Field[];
  patchFields.forEach((field) => {
    if (!FIELDS.includes(field)) throw new Error("选项里出现了不认识的设计字段。");
  });

  const changes: { field: Field; oldValue: string; value: string }[] = [];
  patchFields.forEach((field) => {
    const value = option.patch[field];
    if (typeof value !== "string") return;
    if (current.fields[field].value !== value) {
      changes.push({ field, oldValue: current.fields[field].value, value });
    }
  });

  target.answer = option.id;
  // 方案没有实际变化：只记录答案，不升版本。
  if (changes.length === 0) return next;

  const version = current.version + 1;
  const at = isoNow();
  const fields = cloneFields(current.fields);
  changes.forEach(({ field, value }) => {
    fields[field] = {
      value,
      resource_ids: [...option.resource_ids],
      utterance_ids: [...target.utterance_ids],
      origin: "客户确认",
      confirmed: true,
    };
  });
  next.spec_versions.push({
    version,
    created_at: at,
    reason: `客户确认：${target.title}`,
    fields,
  });

  // 相关原话改为“已与客户确认”，并把这一栏的理由换成客户真实的选择。
  next.utterances = next.utterances.map((utterance) => {
    if (!target.utterance_ids.includes(utterance.id)) return utterance;
    const mappings = utterance.mappings.map((mapping) =>
      mapping.field === target.field
        ? {
            ...mapping,
            status: "已与客户确认" as Status,
            note: `客户确认：${option.label}`,
          }
        : mapping,
    );
    const updated: Utterance = {
      ...utterance,
      mappings,
      status: "已与客户确认",
      resolved_by: target.id,
    };
    return updated;
  });

  const texts = utteranceTexts(next, target.utterance_ids);
  next.ledger.push({
    version,
    at,
    action: "客户确认",
    summary: changes
      .map(({ field, oldValue, value }) => `【${field}】${oldValue || "未写"} → ${value}`)
      .join("；"),
    detail:
      `客户回答了「${target.title}」，选择「${option.label}」。${option.note} ` +
      `依据原话：${texts || "（无）"}` +
      untouchedNote(changes.map((c) => c.field)),
    utterance_ids: [...target.utterance_ids],
    changed_fields: changes.map((c) => c.field),
  });

  return next;
}

// ---------------------------------------------------------------------------
// 人工修改一栏：与后端 revise.py 的 apply_manual_patch 对齐
// ---------------------------------------------------------------------------

/** 人工改掉一栏，能替客户解决的出入：有冲突、被改写、未落实。 */
const RESOLVABLE_STATUSES: Status[] = ["有冲突", "被改写", "未落实"];
/** 仍算“这句原话还有问题”的状态。 */
const BLOCKING_STATUSES: Status[] = ["有冲突", "被改写", "未落实", "需设计师判断"];

/**
 * 人工改完一栏后，替客户把相关原话处理掉。
 * 只处理这一栏原来有出入的原话；别的字段还有问题就原样保留。
 * 不设 resolved_by：这是业务员人工改的，没有对应的客户问题 id，不能编造。
 */
function confirmManualResolution(
  session: Session,
  field: Field,
  reason: string,
): { resolvedIds: string[]; before: Set<Status> } {
  const resolvedIds: string[] = [];
  const before = new Set<Status>();

  session.utterances = session.utterances.map((utterance) => {
    const targets = utterance.mappings.filter((m) => m.field === field);
    if (!targets.some((m) => RESOLVABLE_STATUSES.includes(m.status))) return utterance;

    targets.forEach((m) => before.add(m.status));
    const mappings = utterance.mappings.map((mapping) => {
      if (mapping.field === field) {
        return {
          ...mapping,
          status: "已与客户确认" as Status,
          note: `业务员按客户要求人工修改了【${field}】（原因：${reason}），改记为已与客户确认。`,
        };
      }
      return { ...mapping };
    });

    const stillBlocked = mappings.some((m) => BLOCKING_STATUSES.includes(m.status));
    const finalMappings = stillBlocked
      ? mappings
      : mappings.map((mapping) => {
          if (mapping.status !== "已落实" && mapping.status !== "间接体现") return mapping;
          const tail = `业务员人工修改【${field}】后，这句原话已没有遗留问题。`;
          return {
            ...mapping,
            status: "已与客户确认" as Status,
            note: mapping.note ? `${mapping.note}；${tail}` : tail,
          };
        });

    resolvedIds.push(utterance.id);
    return {
      ...utterance,
      mappings: finalMappings,
      status: mostSevere(finalMappings.map((m) => m.status)),
    };
  });

  return { resolvedIds, before };
}

export function patchSpec(
  session: Session,
  field: Field,
  value: string,
  reason: string,
): Session {
  if (!FIELDS.includes(field)) throw new Error("字段不正确，只能是八个设计字段之一。");
  const trimmedReason = (reason ?? "").trim();
  if (!trimmedReason) throw new Error("请填写修改原因，交接单要靠它说明为什么改这一栏。");
  const trimmedValue = (value ?? "").trim();
  if (!trimmedValue) throw new Error("这一栏不能留空。");

  const current = currentVersion(session);
  const oldValue = current.fields[field].value;
  // 内容没变：不升版本、不记台账。
  if (oldValue === trimmedValue) return session;

  const next = cloneSession(session);
  const base = currentVersion(next);
  // 新版本用独立的字段副本，旧版本保持原样，界面上才看得出哪一栏变了。
  const fields = cloneFields(base.fields);
  const previous = fields[field];
  fields[field] = {
    value: trimmedValue,
    // 只认得出出处的值才给资源 id；对不上就留空，不沿用旧出处。
    resource_ids: findResourceIds(field, trimmedValue),
    utterance_ids: [...previous.utterance_ids],
    origin: "人工修改",
    confirmed: false,
  };

  const version = base.version + 1;
  const at = isoNow();
  next.spec_versions.push({
    version,
    created_at: at,
    reason: `人工修改：${trimmedReason}`,
    fields,
  });

  const { resolvedIds, before } = confirmManualResolution(next, field, trimmedReason);

  // 这一栏还没回答的追问不再列出来；已经答过的问题记录保留。
  const pendingBefore = next.questions.length;
  next.questions = next.questions.filter(
    (question) => !(question.field === field && question.answer === null),
  );
  const dropped = pendingBefore - next.questions.length;

  const detailParts = [`人工原因：${trimmedReason}`];
  if (resolvedIds.length) {
    const texts = utteranceTexts(next, resolvedIds);
    const oldStatuses = Array.from(before)
      .sort((a, b) => STATUS_ORDER.indexOf(a) - STATUS_ORDER.indexOf(b))
      .join("、");
    detailParts.push(
      `这一栏原来的出入（${oldStatuses}）已由业务员改掉，` +
        `相关原话 ${texts} 中【${field}】的映射改记为已与客户确认。`,
    );
  }
  if (dropped) {
    detailParts.push(`【${field}】还没回答的追问（${dropped} 个）不再列出，已回答的问题记录保留。`);
  }

  next.ledger.push({
    version,
    at,
    action: "人工修改",
    summary: `【${field}】${oldValue || "未写"} → ${trimmedValue}`,
    detail: `${detailParts.join("。")}${untouchedNote([field])}`,
    utterance_ids: [...previous.utterance_ids],
    changed_fields: [field],
  });

  return next;
}

// ---------------------------------------------------------------------------
// 交接单：与后端 backend/app/pipeline/handoff.py 的六个部分对齐
// ---------------------------------------------------------------------------

/** data/workflow.json 第 5 步“打样”的核对项，照抄数据。 */
const WORKFLOW_STEP5_CHECKS = ["肩位是否压肩", "袖笼活动量", "交领形态", "衣长比例", "暗纹呈现效果"];

/** 系统不做决定的事，照抄后端 handoff.SYSTEM_LIMITS。 */
const SYSTEM_LIMITS = [
  "资源都是上服案例里出现过的参考，不代表集团现有库存、货号或可采购面料。",
  "价格、工期和交付时间由业务与生产部门确认，系统不报价、不排期。",
  "量体数据由量体师现场采集，系统只记录客户口述和方案。",
  "版型纸样与具体工艺参数由制版师、样衣工决定，系统只做提醒。",
  "面料实样需要客户当面确认，色差以实物为准。",
];

function answeredQuestions(
  session: Session,
): { question: Question; option: QuestionOption }[] {
  const items: { question: Question; option: QuestionOption }[] = [];
  session.questions.forEach((question) => {
    if (!question.answer) return;
    const option = question.options.find((o) => o.id === question.answer);
    if (option) items.push({ question, option });
  });
  return items;
}

function handoffTodos(session: Session): string[] {
  const todos: string[] = [];
  answeredQuestions(session).forEach(({ question, option }) => {
    if (option.todo) todos.push(`【${question.field}】${option.todo}`);
  });
  const fields = currentFields(session);
  FIELDS.forEach((field) => {
    const item = fields[field];
    if (item.origin !== "无出处" || !item.value) return;
    if (field === "品类") {
      todos.push(
        `【品类】${item.value}直接取自客户口述；资源库没有独立的品类项，` +
          "不需要另找品类样布，照常按品类打版即可。",
      );
      return;
    }
    todos.push(`【${field}】方案里这一栏没有资源出处，需要人工找样确认。`);
  });
  return todos;
}

function samplingChecks(session: Session): string[] {
  const fields = currentFields(session);
  const checks = [...WORKFLOW_STEP5_CHECKS];
  const extra: string[] = [];
  if (session.designer_items.some((item) => item.field === "领型")) {
    extra.push("领宽按设计师确认后的尺寸放样");
  }
  const patternIds = fields["装饰纹样"].resource_ids;
  if (patternIds.length) {
    extra.push(`纹样对位与呈现效果（${resourceText(patternIds)}）`);
  }
  if (fields["松弛余量"].value.includes("内搭")) {
    extra.push("内搭放量是否足够，抬手和坐姿是否紧绷");
  }
  if (fields["色彩"].value && fields["色彩"].value !== "无特殊需求") {
    extra.push("颜色与滚边、袖口的对位和色差");
  }
  extra.forEach((item) => {
    if (!checks.includes(item)) checks.push(item);
  });
  return checks;
}

function handoffSource(field: Field, item: SpecField): string {
  if (item.origin === "无出处") {
    return field === "品类"
      ? "客户口述（资源库无独立品类项，无需找样布）"
      : "无出处（需人工确认）";
  }
  return `${item.origin}；${resourceText(item.resource_ids)}`;
}

export function buildHandoff(session: Session): string {
  const version = currentVersion(session);
  const fields = version.fields;
  const lines: string[] = [];

  lines.push("# 衣诺 · 交接单");
  lines.push("");
  lines.push(`- 会话：${session.title}（${session.id}）`);
  lines.push(`- 模式：${session.mode}`);
  lines.push(`- 数据来源：${session.source === "mock" ? "示例数据" : "大模型解构"}`);
  lines.push(`- 当前版本：第 ${version.version} 版`);
  lines.push(`- 生成时间：${isoNow()}`);
  lines.push("");

  lines.push(`## 一、当前方案（第 ${version.version} 版）`);
  lines.push("");
  lines.push("| 设计字段 | 方案 | 出处 | 客户已确认 |");
  lines.push("| --- | --- | --- | --- |");
  FIELDS.forEach((field) => {
    const item = fields[field];
    const value = (item.value || "未写").replace(/\|/g, "／");
    lines.push(`| ${field} | ${value} | ${handoffSource(field, item)} | ${item.confirmed ? "是" : "否"} |`);
  });
  lines.push("");

  lines.push("## 二、和客户确认的结果");
  lines.push("");
  const answered = answeredQuestions(session);
  if (answered.length === 0) {
    lines.push("还没有和客户确认的事项。");
  } else {
    answered.forEach(({ question, option }, index) => {
      const texts = utteranceTexts(session, question.utterance_ids);
      lines.push(`${index + 1}. 【${question.field}】${question.title}`);
      lines.push(`   - 客户选择：${option.label}`);
      lines.push(`   - 依据原话：${texts || "（无）"}`);
      if (option.note) lines.push(`   - 说明：${option.note}`);
    });
  }
  lines.push("");

  lines.push("## 三、请设计师判断");
  lines.push("");
  if (session.designer_items.length === 0) {
    lines.push("- 暂无。");
  } else {
    session.designer_items.forEach((item) => {
      lines.push(`- 【${item.field}】${item.text}`);
    });
  }
  lines.push("");

  lines.push("## 四、待办");
  lines.push("");
  const todos = handoffTodos(session);
  if (todos.length === 0) {
    lines.push("- 暂无。");
  } else {
    todos.forEach((todo) => lines.push(`- ${todo}`));
  }
  lines.push("");

  lines.push("## 五、打样核对项（按上服定制流程第 5 步“打样”）");
  lines.push("");
  samplingChecks(session).forEach((check) => lines.push(`- ${check}`));
  lines.push("");

  lines.push("## 六、不由系统决定的事项");
  lines.push("");
  SYSTEM_LIMITS.forEach((item) => lines.push(`- ${item}`));
  lines.push("");

  lines.push("---");
  lines.push("本交接单由衣诺生成，原话和资源都注明出处；需要人判断的地方已单独列出。");
  lines.push("");
  return lines.join("\n");
}
