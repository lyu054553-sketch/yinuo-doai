import type {
  Field,
  ResourceCatalog,
  ResourceProfileItem,
  RequirementsAnalysis,
  Session,
} from "../api/types";
import { caseLabel } from "./resources";

export interface ProposalResourceMatch {
  field: Field;
  id: string;
  name: string;
  sources: string[];
  style_tags: string[];
  scene_tags: string[];
  usage_guidance: string[];
}

export interface ProposalFeasibility {
  level: "提示" | "待确认" | "待补资源";
  text: string;
}

/** 按上服选面料的三条判断依据组织：风格适配、体感适配、体型适配。只引用客户原话和资源库里记录的面料特点。 */
export interface FabricReason {
  label: "风格适配" | "体感适配" | "体型适配";
  quotes: string[];
  traits: string[];
  text: string;
}

const FABRIC_REASON_RULES: { label: FabricReason["label"]; ask: string[]; traits: string[] }[] = [
  {
    label: "风格适配",
    ask: ["中式", "正式", "优雅", "端庄", "气质", "低调", "内敛", "简约", "大气", "喜庆", "见家长", "过年", "场合", "商务", "有质感", "高级"],
    traits: ["中式", "正式", "优雅", "端庄", "含蓄", "低调", "高级", "喜庆", "华丽", "稳重", "暗纹", "提花", "云纹", "光泽"],
  },
  {
    label: "体感适配",
    ask: ["舒服", "舒适", "宽松", "紧绷", "束缚", "活动", "光泽", "质感", "垂", "软", "透气", "保暖", "抬手", "方便"],
    traits: ["垂感", "柔和", "柔软", "手感", "挺而不硬", "不易贴身", "透气", "弹力", "活动方便", "柔滑", "顺滑", "光泽", "哑光", "保暖", "舒适"],
  },
  {
    label: "体型适配",
    ask: ["肚", "腩", "肩", "身形", "背", "显瘦", "修身", "偏高", "身高", "胖"],
    traits: ["挺括", "骨感", "不易贴身", "挺而不硬", "垂感", "显瘦"],
  },
];

export function fabricReasons(session: Session, catalog: ResourceCatalog | null): FabricReason[] {
  const current = session.spec_versions[session.spec_versions.length - 1];
  const ids = current.fields["面料"]?.resource_ids ?? [];
  const allTraits = unique(
    ids.flatMap((id) => {
      const item = catalog?.items.find((candidate) => candidate.id === id);
      return Array.isArray(item?.traits) ? (item.traits as string[]) : [];
    }),
  );
  return FABRIC_REASON_RULES.map((rule) => {
    const quotes = session.utterances
      .filter((u) => rule.ask.some((word) => u.text.includes(word)))
      .map((u) => u.text)
      .slice(0, 3);
    const traits = allTraits.filter((trait) => rule.traits.some((word) => trait.includes(word)));
    let text: string;
    if (quotes.length === 0) text = "客户没有提到这方面的要求。";
    else if (traits.length === 0) text = `客户：${quotes.map((q) => `「${q}」`).join("")}；资源库里没有记录这款面料在这方面的特点，需设计师判断。`;
    else text = `客户：${quotes.map((q) => `「${q}」`).join("")}；面料特点：${traits.join("、")}。`;
    return { label: rule.label, quotes, traits, text };
  });
}

export interface ProposalBrief {
  version: number;
  style_positioning: string;
  scene_fit: string;
  audience: string;
  silhouette: string;
  fabric: string;
  fabric_reasons: FabricReason[];
  color: string;
  pattern: string;
  ease: string;
  craft_composition: string;
  design_highlights: string[];
  resource_matches: ProposalResourceMatch[];
  feasibility: ProposalFeasibility[];
  gaps: string[];
}

const STYLE_TAGS = [
  "新中式", "中式", "优雅", "端庄", "气质", "简约大气", "简约", "内敛",
  "低调", "明快", "有活力", "松弛", "有质感", "正式",
];
const SCENE_TAGS = ["正式场合", "户外舞台", "演出", "婚礼", "宴会", "通勤", "发布会", "会议"];
const AUDIENCE_TAGS = ["员工", "团队", "团体", "女士", "男士", "儿童", "老人"];

const CRAFT_TRIGGERS: Record<string, string[]> = {
  CR1: ["无扣", "开襟"],
  CR2: ["一体式贴边", "里布贴边", "门襟贴边"],
  CR3: ["袖口直筒", "袖口贴边"],
  CR4: ["开衩", "挡布"],
  CR5: ["缝份"],
  CR6: ["亮粉", "滚边"],
  CR7: ["撞色", "袖口"],
  CR8: ["外露扣", "纽扣"],
};

function unique(values: string[]): string[] {
  return [...new Set(values.filter(Boolean))];
}

function explicitTags(session: Session, vocabulary: string[]): string[] {
  const text = [
    session.input.text,
    ...session.utterances.map((item) => item.text),
    ...session.utterances.flatMap((item) => item.mappings.map((mapping) => mapping.note)),
  ].join(" ");
  return vocabulary.filter((tag) => text.includes(tag));
}

function semanticTags(analysis: RequirementsAnalysis | null, title: string): string[] {
  const category = analysis?.categories.find((item) => item.title === title);
  if (!category) return [];
  return category.utterance_ids;
}

function fieldValue(session: Session, field: Field): string {
  const current = session.spec_versions[session.spec_versions.length - 1];
  return current.fields[field]?.value?.trim() || "未明确，需人工确认";
}

function matchedResources(
  session: Session,
  catalog: ResourceCatalog | null,
): ProposalResourceMatch[] {
  if (!catalog) return [];
  const current = session.spec_versions[session.spec_versions.length - 1];
  const index = new Map(catalog.items.map((item) => [item.id, item]));
  return Object.entries(current.fields).flatMap(([field, value]) =>
    value.resource_ids.flatMap((id) => {
      const item = index.get(id);
      if (!item) {
        return [{
          field: field as Field,
          id,
          name: "资源库未载入该编号",
          sources: [],
          style_tags: [],
          scene_tags: [],
          usage_guidance: ["需人工核对资源记录"],
        }];
      }
      return [{
        field: field as Field,
        id,
        name: item.name,
        sources: item.basis_cases,
        style_tags: item.style_tags,
        scene_tags: item.scene_tags,
        usage_guidance: item.usage_guidance,
      }];
    }),
  );
}

function craftMatches(
  session: Session,
  notes: Record<string, string>,
  analysis: RequirementsAnalysis | null,
  catalog: ResourceCatalog | null,
): ResourceProfileItem[] {
  if (!catalog) return [];
  const current = session.spec_versions[session.spec_versions.length - 1];
  const direct = [
    ...Object.values(current.fields).map((item) => item.value),
    ...Object.values(notes),
    ...semanticTags(analysis, "工艺细节").map((id) => session.utterances.find((item) => item.id === id)?.text ?? ""),
  ].join(" ");
  return catalog.items.filter((item) => {
    if (item.group !== "crafts" || !item.recommendation_eligible) return false;
    const triggers = CRAFT_TRIGGERS[item.id];
    return Boolean(triggers?.every((trigger) => direct.includes(trigger)));
  });
}

export function buildProposalBrief(
  session: Session,
  notes: Record<string, string>,
  catalog: ResourceCatalog | null,
  analysis: RequirementsAnalysis | null = null,
): ProposalBrief {
  const current = session.spec_versions[session.spec_versions.length - 1];
  const resources = matchedResources(session, catalog);
  const crafts = craftMatches(session, notes, analysis, catalog);
  const styleTags = explicitTags(session, STYLE_TAGS);
  const sceneTags = explicitTags(session, SCENE_TAGS);
  const audienceTags = explicitTags(session, AUDIENCE_TAGS);
  const highlighted = unique(resources.map((item) => {
    const source = catalog?.items.find((candidate) => candidate.id === item.id);
    const traits = Array.isArray(source?.traits) ? source.traits.slice(0, 2) : [];
    return traits.length ? `${item.id} ${item.name}：${traits.join("、")}` : `${item.id} ${item.name}`;
  })).slice(0, 8);
  const gaps: string[] = [];
  Object.entries(current.fields).forEach(([field, item]) => {
    if (item.origin === "无出处" && item.value && field !== "品类") {
      gaps.push(`${field}库中暂无对应做法，需要人工找样或由设计师补充依据。`);
    }
  });
  if (!notes["风格调性"] && styleTags.length === 0) gaps.push("风格定位未明确。");
  if (!notes["场景适配度"] && !notes["使用场景"] && sceneTags.length === 0) gaps.push("适配场景未明确。");
  if (!notes["使用人群"] && audienceTags.length === 0) gaps.push("使用人群未明确；系统不根据案例反推人群。");
  if (!notes["工艺细节"] && crafts.length === 0) gaps.push("客户未明确提出可匹配工艺，当前不自动套用案例工艺。");

  const feasibility: ProposalFeasibility[] = [];
  session.questions.filter((item) => !item.answer).forEach((item) => {
    feasibility.push({ level: "待确认", text: `客户问题待回答：${item.title}` });
  });
  session.designer_items.forEach((item) => {
    feasibility.push({ level: "待确认", text: `设计师判断：${item.text}` });
  });
  resources.forEach((item) => {
    item.usage_guidance.forEach((text) => feasibility.push({ level: "提示", text: `${item.id}：${text}` }));
  });
  gaps.forEach((text) => feasibility.push({ level: "待补资源", text }));
  if (feasibility.length === 0) {
    feasibility.push({ level: "提示", text: "当前没有自动识别的未决事项；材料、版型、量体和打样仍需人工确认。" });
  }

  const craftText = notes["工艺细节"] || crafts.map((item) => `${item.id} ${item.name}`).join("；") || "未明确，系统不套用未要求的工艺。";
  const sceneText = notes["场景适配度"] || notes["使用场景"] || (sceneTags.length ? sceneTags.join("、") : "未明确，需人工确认");
  const audienceText = notes["使用人群"] || (audienceTags.length ? audienceTags.join("、") : "未明确，需人工确认");
  const styleText = notes["风格调性"] || (styleTags.length ? styleTags.join("、") : "未明确，需人工确认");

  return {
    version: current.version,
    style_positioning: styleText,
    scene_fit: sceneText,
    audience: audienceText,
    silhouette: fieldValue(session, "版型结构"),
    fabric: fieldValue(session, "面料"),
    fabric_reasons: fabricReasons(session, catalog),
    color: fieldValue(session, "色彩"),
    pattern: fieldValue(session, "装饰纹样"),
    ease: fieldValue(session, "松弛余量"),
    craft_composition: craftText,
    design_highlights: highlighted.length ? highlighted : ["尚未形成可追溯的设计亮点。"],
    resource_matches: resources,
    feasibility,
    gaps,
  };
}

export function proposalMarkdown(session: Session, brief: ProposalBrief): string {
  const lines = [
    `# 衣诺定制方案初稿 · v${brief.version}`,
    "",
    `- 会话：${session.title}（${session.id}）`,
    "- 性质：业务与设计参考初稿，不是最终定稿、报价或生产指令。",
    "",
    "## 风格定位",
    brief.style_positioning,
    "",
    "## 面料推荐",
    brief.fabric,
    "",
    "判断依据（按上服选面料的三条依据）：",
    ...brief.fabric_reasons.map((item) => `- ${item.label}：${item.text}`),
    "",
    "## 版型选择",
    brief.silhouette,
    "",
    "## 工艺组合",
    brief.craft_composition,
    "",
    "## 适配场景与人群",
    `- 场景：${brief.scene_fit}`,
    `- 人群：${brief.audience}`,
    "",
    "## 色彩、纹样与松量",
    `- 色彩：${brief.color}`,
    `- 纹样：${brief.pattern}`,
    `- 松量：${brief.ease}`,
    "",
    "## 设计亮点",
    ...brief.design_highlights.map((item) => `- ${item}`),
    "",
    "## 资源匹配与使用规范",
    ...(brief.resource_matches.length
      ? brief.resource_matches.flatMap((item) => [
          `- ${item.field}：${item.id} ${item.name}；来源：${item.sources.map(caseLabel).join("、") || "未记录"}`,
          ...item.usage_guidance.map((text) => `  - ${text}`),
        ])
      : ["- 当前没有可追溯的资源匹配。"]),
    "",
    "## 可行性与待办",
    ...brief.feasibility.map((item) => `- 【${item.level}】${item.text}`),
    "",
    "## 边界",
    "- 资源来自上服案例或“模拟库”演示数据，不代表库存、货号、价格或当前可采购性。",
    "- 最终定稿、量体、制版、报价、采购和下单由业务及专业人员完成。",
    "",
  ];
  return lines.join("\n");
}
