import type { ResourceCatalog, ResourceLibrary, ResourceProfileItem } from "../api/types";
import devResources from "./dev-resources.json";

// 示例数据模式下的资源库：后端开发夹具 backend/storage/fixtures/dev-resources.json 的原样复制件。
// 资源是案例参考，不代表库存；名称与来源案例都照抄数据，不在前端补编。
export const MOCK_RESOURCES = devResources as unknown as ResourceLibrary;

const GROUP_LABELS: Record<string, string> = {
  silhouettes: "版型",
  shoulders: "肩型",
  collars: "领型",
  fabrics: "面料",
  crafts: "工艺",
  patterns: "纹样",
  colors: "色彩",
  lengths: "长度",
  body_rules: "身形规则",
};

const CASE_PROFILES: Record<string, { styles: string[]; scenes: string[]; basis: string }> = {
  c1: { styles: ["中式", "端庄", "气质"], scenes: ["正式场合"], basis: "案例一口述：中式、端庄、正式场合、气质" },
  c2: { styles: ["中式", "优雅"], scenes: ["正式场合"], basis: "案例二口述：中式、正式场合、优雅" },
  c3: { styles: ["优雅", "明快", "有活力", "正式", "松弛"], scenes: ["户外舞台", "演出"], basis: "案例三口述：户外舞台演出、优雅、明快、正式与松弛" },
  c4: { styles: ["中式", "内敛", "简约", "低调"], scenes: [], basis: "案例四口述：中式、内敛、简约、低调；未明确场景" },
  c6: { styles: ["简约大气", "新中式", "正式", "有质感"], scenes: ["正式场合"], basis: "案例六口述：简约大气的新中式、正式、有质感" },
};

const GROUP_GUIDANCE: Record<string, string> = {
  silhouettes: "仅作案例版型方向参考；人体适配、具体松量和纸样由版师量体后确认。",
  shoulders: "仅作案例肩型方向参考；肩线位置和袖笼活动量需试穿确认。",
  collars: "仅作案例领型方向参考；领高、领宽和颈部舒适度需设计师及试穿确认。",
  fabrics: "仅作案例面料方向参考；成分、克重、色差、库存和可采购性均未记录，需找样确认。",
  crafts: "仅作案例结构或工艺方向参考；成本、可制造性和参数由制版师或样衣工确认。",
  patterns: "仅作案例纹样方向参考；花型循环、对位和色牢度需以实际面料样确认。",
  colors: "仅作案例配色方向参考；具体色号、色差和面料适配需以实物确认。",
  lengths: "仅作案例比例方向参考；最终衣长按身高、用途和量体结果确认。",
  body_rules: "规则只提示需要留意的身形条件；处理方式和舒适度必须由设计师、版师和试穿共同确认。",
};

function unique(values: string[]): string[] {
  return [...new Set(values.filter(Boolean))];
}

export function buildMockResourceCatalog(library: ResourceLibrary): ResourceCatalog {
  const items: ResourceProfileItem[] = [];
  Object.entries(library).forEach(([group, records]) => {
    if (group === "meta" || !Array.isArray(records) || !GROUP_LABELS[group]) return;
    records.forEach((record) => {
      if (!record || typeof record !== "object" || !("id" in record)) return;
      const item = record as Record<string, unknown>;
      const sources = Array.isArray(item.sources)
        ? item.sources.filter((source): source is string => typeof source === "string" && source !== "c5")
        : [];
      const sourceProfiles = sources.map((source) => CASE_PROFILES[source]).filter(Boolean);
      const styleTags = unique(sourceProfiles.flatMap((profile) => profile.styles));
      const sceneTags = unique(sourceProfiles.flatMap((profile) => profile.scenes));
      const evidence = sourceProfiles.map((profile) => profile.basis);
      const usage = [GROUP_GUIDANCE[group]];
      if (typeof item.caution === "string" && item.caution.trim()) usage.push(`案例提示：${item.caution.trim()}`);
      if (typeof item.note === "string" && item.note.trim()) usage.push(`资料说明：${item.note.trim()}`);
      if (group === "body_rules") {
        if (typeof item.condition === "string" && item.condition.trim()) usage.push(`触发信息：${item.condition.trim()}`);
        if (typeof item.treatment === "string" && item.treatment.trim()) usage.push(`案例处理：${item.treatment.trim()}`);
      }
      const missing: string[] = [];
      if (!styleTags.length) missing.push("风格标签");
      if (!sceneTags.length) missing.push("场景标签");
      if (!sources.length) missing.push("开发案例依据");
      items.push({
        ...(item as unknown as ResourceProfileItem),
        name: typeof item.name === "string" ? item.name : String(item.condition || GROUP_LABELS[group]),
        group,
        group_label: GROUP_LABELS[group],
        style_tags: styleTags,
        scene_tags: sceneTags,
        audience_tags: [],
        usage_guidance: usage,
        evidence,
        basis_cases: sources,
        recommendation_eligible: sources.length > 0,
        missing_metadata: missing,
      });
    });
  });
  return {
    meta: {
      source: "根据示例案例与资源库派生的只读分类目录",
      evidence_policy: "风格和场景只继承客户原话中明确出现的表达；未记录字段保持空白，不猜测。",
      availability: "资源仅为历史案例参考，不代表库存、货号、报价或当前可采购性。",
    },
    items,
    counts: { total: items.length, recommendable: items.filter((item) => item.recommendation_eligible).length },
  };
}
