import { useEffect, useMemo, useState } from "react";
import { getResourceCatalog, getResources, getSamples } from "../api/client";
import { readableError } from "../api/errors";
import type { ResourceCatalog, ResourceProfileItem, Sample } from "../api/types";
import { caseLabel, visibleSources, type StockEntry } from "../utils/resources";
import { CASE_ART } from "../utils/caseArt";
import TopBar, { ModeBadge } from "../components/TopBar";

const groups = [
  ["silhouettes", "版型"], ["shoulders", "肩型"], ["collars", "领型"],
  ["fabrics", "面料"], ["crafts", "工艺"], ["patterns", "纹样"],
  ["colors", "色彩"], ["lengths", "长度"], ["body_rules", "身形规则"],
] as const;

export default function ResourcesPage(_props: { onBack: () => void }) {
  const [catalog, setCatalog] = useState<ResourceCatalog | null>(null);
  const [samples, setSamples] = useState<Sample[]>([]);
  const [error, setError] = useState("");
  const [group, setGroup] = useState("all");
  const [query, setQuery] = useState("");
  const [style, setStyle] = useState("all");
  const [scene, setScene] = useState("all");
  const [stock, setStock] = useState<StockEntry[]>([]);

  useEffect(() => {
    let active = true;
    Promise.all([getResourceCatalog(), getSamples()]).then(([data, cases]) => { if (active) { setCatalog(data); setSamples(cases.filter((item) => !item.holdout)); } })
      .catch((err) => { if (active) setError(readableError(err)); });
    getResources().then((lib) => { if (active) setStock((lib.inventory ?? []) as unknown as StockEntry[]); }).catch(() => undefined);
    return () => { active = false; };
  }, []);

  const visibleItems = useMemo(() => catalog?.items.filter((item) => item.recommendation_eligible) ?? [], [catalog]);
  const styleOptions = useMemo(() => [...new Set(visibleItems.flatMap((item) => item.style_tags))].sort((a, b) => a.localeCompare(b, "zh-CN")), [visibleItems]);
  const sceneOptions = useMemo(() => [...new Set(visibleItems.flatMap((item) => item.scene_tags))].sort((a, b) => a.localeCompare(b, "zh-CN")), [visibleItems]);
  const items = useMemo(() => visibleItems.filter((item) => {
    if (group === "inventory") return false;
    if (group !== "all" && group !== "cases" && item.group !== group) return false;
    if (style !== "all" && !item.style_tags.includes(style)) return false;
    if (scene !== "all" && !item.scene_tags.includes(scene)) return false;
    return JSON.stringify(item).toLocaleLowerCase().includes(query.trim().toLocaleLowerCase());
  }), [visibleItems, group, query, style, scene]);
  const cases = useMemo(() => (group === "all" || group === "cases") ? samples.filter((item) =>
    JSON.stringify([item.title, item.text, item.existing_spec]).toLocaleLowerCase().includes(query.trim().toLocaleLowerCase())) : [], [samples, group, query]);

  function downloadCatalog() {
    const rows: string[][] = [
      ["类型", "编号", "名称", "来源案例", "风格标签", "场景标签", "使用人群", "已记录特点", "适配与使用规范", "缺失信息"],
      ...items.map((item) => [
        item.group_label,
        item.id,
        item.name,
        item.basis_cases.map(caseLabel).join("、") || "未记录",
        item.style_tags.join("、") || "未记录",
        item.scene_tags.join("、") || "未记录",
        item.audience_tags.join("、") || "未记录",
        Array.isArray(item.traits) ? item.traits.join("、") : "未记录",
        item.usage_guidance.join("；"),
        item.missing_metadata.join("、") || "无",
      ]),
    ];
    const cell = (value: string) => {
      const safe = /^[\s\u0000-\u001f]*[=+\-@]/.test(value) ? `'${value}` : value;
      return `"${safe.replace(/"/g, '""')}"`;
    };
    const content = `\ufeff${rows.map((row) => row.map(cell).join(",")).join("\r\n")}\r\n`;
    const url = URL.createObjectURL(new Blob([content], { type: "text/csv;charset=utf-8" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = "衣诺定制资源分类清单.csv";
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  return <div className="app-page">
    <TopBar center={<span className="v2-session-title"><h1>资源知识库</h1><p>方案里引用的版型、面料、纹样等编号都在这里查</p></span>} right={<ModeBadge />} />
    <main className="page">
      <div className="page-head"><div><p className="eyebrow">资产知识库</p><h1>查找定制做法与面料</h1><p className="page-sub">两部分：从上服案例拆出的做法（注明来自哪个案例），以及标“模拟库”的常见做法和模拟仓库（演示数据，落地时整份替换为上服真实数据）。都只作方案参考，下单以仓库实盘为准。</p></div></div>
      <div className="resource-search-controls">
        <label>关键词<input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索名称、特点、场景或来源" /></label>
        <label>资源类型<select value={group} onChange={(event) => setGroup(event.target.value)}><option value="all">全部类型</option>{groups.map(([key, label]) => <option value={key} key={key}>{label}</option>)}<option value="cases">历史案例</option>{stock.length > 0 && <option value="inventory">仓库（模拟）</option>}</select></label>
        <label>风格标签<select value={style} onChange={(event) => setStyle(event.target.value)}><option value="all">全部风格</option>{styleOptions.map((item) => <option value={item} key={item}>{item}</option>)}</select></label>
        <label>场景标签<select value={scene} onChange={(event) => setScene(event.target.value)}><option value="all">全部场景</option>{sceneOptions.map((item) => <option value={item} key={item}>{item}</option>)}</select></label>
        <button type="button" className="btn btn-secondary resource-export" onClick={downloadCatalog} disabled={!items.length}>导出当前分类清单（CSV）</button>
      </div>
      {error && <div className="alert alert-error" role="alert">{error}</div>}
      {!catalog && !error && <p>正在读取资源库…</p>}
      {group === "inventory" && <div className="v4-inventory"><p className="meta-line">模拟仓库共 {stock.length} 个货号。米数、交期均为演示数值，不是上服库存。</p><table><thead><tr><th>货号</th><th>面料</th><th>色号 / 花型</th><th>状态</th><th>库存</th><th>位置</th><th>对应资源</th></tr></thead><tbody>{stock.filter((e) => JSON.stringify(e).includes(query.trim())).map((e) => <tr key={e.sku} data-status={e.status}><td><code>{e.sku}</code></td><td>{e.name}</td><td>{e.colorway}</td><td><b>{e.status}</b></td><td>{e.stock_m > 0 ? `${e.stock_m} 米` : e.lead_days > 0 ? `约 ${e.lead_days} 天到货` : "—"}</td><td>{e.location}</td><td><code>{[e.fabric, e.pattern, e.color].filter(Boolean).join(" · ")}</code></td></tr>)}</tbody></table></div>}
      {catalog && group !== "inventory" && <><p className="meta-line" aria-live="polite">可推荐资源 {catalog.counts.recommendable} 条（含模拟库 {catalog.items.filter((i) => i.simulated).length} 条）；当前筛选到 {items.length} 条资源、{cases.length} 个案例。</p>{items.length === 0 && cases.length === 0 ? <p className="empty-hint">没有找到匹配的资源，请换个关键词、标签或类型。</p> : <ul className="resource-catalog">{items.map((item: ResourceProfileItem) => {
        const body = "condition" in item && typeof item.condition === "string" ? item.condition : null;
        const traits = Array.isArray(item.traits) ? item.traits : [];
        return <li className="resource-catalog-item" key={item.id}>
          <div className="resource-catalog-title"><span>{item.group_label} · {item.id}{item.simulated ? <i className="v4-sim-badge">模拟库</i> : null}</span><strong>{item.name || body}</strong></div>
          {typeof item.garment === "string" && <p>适用单品：{item.garment}</p>}
          <p><strong>风格标签：</strong>{item.style_tags.join("、") || "未记录"}</p>
          <p><strong>场景标签：</strong>{item.scene_tags.join("、") || "未记录"}</p>
          <p><strong>使用人群：</strong>{item.audience_tags.join("、") || "未记录"}</p>
          {traits.length > 0 && <p>已记录特点：{traits.join("、")}</p>}
          {"treatment" in item && typeof item.treatment === "string" && <p>案例处理：{item.treatment}</p>}
          {typeof item.note === "string" && <p>说明：{item.note}</p>}
          {typeof item.caution === "string" && <p>注意：{item.caution}</p>}
          <div className="resource-usage"><strong>适配与使用规范</strong><ul>{item.usage_guidance.map((text) => <li key={text}>{text}</li>)}</ul></div>
          {item.missing_metadata.length > 0 && <p className="resource-missing">未记录：{item.missing_metadata.join("、")}</p>}
          <p>来源：{visibleSources(item.sources).length ? visibleSources(item.sources).map(caseLabel).join("、") : "未记录"}</p>
          {item.basis_cases.length > 0 && <details className="resource-evidence"><summary>查看分类依据</summary>{item.evidence.map((text) => <p key={text}>{text}</p>)}</details>}
        </li>;
      })}{cases.map((item) => <li className="resource-catalog-item" key={item.id}><div className="resource-catalog-title"><span>历史案例 · {item.id}</span><strong>{item.title}</strong></div>{CASE_ART[item.id] && <figure className="v6-case-art"><img src={CASE_ART[item.id].garment} alt={`${item.title}成衣示意`} loading="lazy" /><img src={CASE_ART[item.id].wear} alt={`${item.title}上身示意`} loading="lazy" /><figcaption>AI 生成示意图，非上服实物，仅供参考</figcaption></figure>}<p>客户口述：{item.text}</p>{item.existing_spec && <details><summary>查看案例方案</summary>{Object.entries(item.existing_spec).map(([field, value]) => <p key={field}>{field}：{value || "未记录"}</p>)}</details>}</li>)}</ul>}</>}
    </main>
  </div>;
}
