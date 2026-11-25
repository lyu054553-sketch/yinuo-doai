import type { ResourceLibrary } from "../api/types";

// 资源条目的显示信息：名称与来源都来自 data/resources.json + data/resources_sim.json（经 /api/resources
// 或示例数据），找不到就不填，不猜测。案例资源是上服案例参考；“模拟库”条目和仓库是演示数据，可整份替换。

const CASE_LABELS: Record<string, string> = {
  c1: "案例一",
  c2: "案例二",
  c3: "案例三",
  c4: "案例四",
  c6: "案例六",
  sim: "模拟库",
};

/** 留出评测案例：后端已经从来源里去掉，这里再兜一层，任何界面都不显示。 */
const HIDDEN_SOURCES = new Set(["c5"]);

export function visibleSources(sources: readonly string[] | null | undefined): string[] {
  return (sources ?? []).filter((s) => !HIDDEN_SOURCES.has(s));
}

export function caseLabel(source: string): string {
  return CASE_LABELS[source] ?? source;
}

export interface ResourceDisplay {
  id: string;
  /** 资源库里的名称；没收录时为 null，界面不硬填 */
  name: string | null;
  /** 来源案例；没收录或没写来源时为 null */
  sources: string[] | null;
  found: boolean;
}

export function findResource(
  library: ResourceLibrary | null,
  id: string,
): ResourceDisplay {
  if (!library) return { id, name: null, sources: null, found: false };
  const groups = Object.entries(library).filter(([key, value]) => key !== "meta" && Array.isArray(value));
  for (const [, items] of groups) {
    for (const item of items as Array<Record<string, unknown>>) {
      if (item && item.id === id) {
        const name = typeof item.name === "string" ? item.name : null;
        const sources = Array.isArray(item.sources)
          ? visibleSources(item.sources.filter((s): s is string => typeof s === "string"))
          : null;
        return { id, name, sources: sources && sources.length > 0 ? sources : null, found: true };
      }
    }
  }
  return { id, name: null, sources: null, found: false };
}

export function sourceText(sources: string[] | null): string | null {
  if (!sources || sources.length === 0) return null;
  return sources.map(caseLabel).join("、");
}
/** 方案一栏“出处”的业务说法：品类直接取客户原话；其余栏库里没对上就提示找样，不写“无出处”。 */
export function originLabel(field: string, origin: string): string | null {
  if (origin !== "无出处") return null;
  return field === "品类" ? "取自客户原话" : "库中暂无 · 待找样";
}

export interface StockEntry {
  sku: string;
  name: string;
  colorway: string;
  stock_m: number;
  status: "有货" | "少量" | "需订货";
  lead_days: number;
  location: string;
  fabric: string;
  pattern: string | null;
  color: string | null;
}

/** 方案用到的面料/纹样/色彩编号在（模拟）仓库里对得上的货：必须对上面料，纹样和颜色对上越多越靠前。 */
export function stockFor(library: ResourceLibrary | null, ids: readonly string[]): StockEntry[] {
  const inventory = (library?.inventory ?? []) as unknown as StockEntry[];
  const set = new Set(ids);
  const hasId = (prefixes: string[]) => ids.some((id) => prefixes.some((p) => id.startsWith(p)));
  const planHasPattern = hasId(["P", "MP"]);
  const planHasColor = hasId(["CO", "MCO"]);
  return inventory
    .filter((entry) => set.has(entry.fabric))
    // 方案已经定了纹样 / 颜色时，花型或颜色对不上的货不推荐
    .filter((entry) => !(planHasPattern && entry.pattern && !set.has(entry.pattern)))
    .filter((entry) => !(planHasColor && entry.color && !set.has(entry.color)))
    .map((entry) => {
      const keys = [entry.pattern, entry.color].filter((v): v is string => Boolean(v));
      const hit = keys.filter((k) => set.has(k)).length;
      const miss = keys.length - hit;
      return { entry, score: hit * 10 - miss + (entry.status === "有货" ? 1 : 0) };
    })
    .sort((a, b) => b.score - a.score || a.entry.sku.localeCompare(b.entry.sku))
    .map((x) => x.entry);
}
