import { useEffect, useMemo, useState } from "react";
import type { RequirementsAnalysis, ResourceCatalog, Session } from "../api/types";
import { getNotes, getRequirementsAnalysis } from "../api/client";
import { readableError } from "../api/errors";
import { buildProposalBrief, proposalMarkdown } from "../utils/proposal";
import { caseLabel } from "../utils/resources";

export default function ProposalTab({
  session,
  catalog,
}: {
  session: Session;
  catalog: ResourceCatalog | null;
}) {
  const [notes, setNotes] = useState<Record<string, string>>({});
  const [analysis, setAnalysis] = useState<RequirementsAnalysis | null>(null);
  const [error, setError] = useState("");
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    let active = true;
    Promise.all([getNotes(session.id), getRequirementsAnalysis(session.id)])
      .then(([noteData, analysisData]) => {
        if (active) {
          setNotes(noteData);
          setAnalysis(analysisData);
        }
      })
      .catch((reason) => { if (active) setError(readableError(reason)); });
    return () => { active = false; };
  }, [session.id]);

  const brief = useMemo(
    () => buildProposalBrief(session, notes, catalog, analysis),
    [session, notes, catalog, analysis],
  );

  async function copyProposal() {
    try {
      await navigator.clipboard.writeText(proposalMarkdown(session, brief));
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      setError("复制没有成功，请稍后重试或从台账导出完整数据。");
    }
  }

  function downloadProposal() {
    const markdown = proposalMarkdown(session, brief);
    const url = URL.createObjectURL(new Blob([markdown], { type: "text/markdown;charset=utf-8" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = `衣诺方案初稿-${session.id}-v${brief.version}.md`;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  return <div className="tab-panel" role="tabpanel">
    <p className="section-title">定制方案初稿 · v{brief.version}</p>
    <p className="hint-line">方案由当前设计字段、客户原话、已保存的业务说明和案例资源共同整理。资源不是库存；最终定稿、量体、制版、报价与下单由人完成。</p>
    <div className="proposal-actions">
      <button type="button" className="btn btn-secondary btn-sm" onClick={copyProposal}>{copied ? "已复制方案初稿" : "复制完整初稿"}</button>
      <button type="button" className="btn btn-quiet btn-sm" onClick={downloadProposal}>下载方案初稿（Markdown）</button>
    </div>
    {error && <div className="alert alert-error" role="alert">{error}</div>}

    <section className="requirement-section proposal-summary">
      <h3>风格、场景与人群</h3>
      <p><strong>风格定位：</strong>{brief.style_positioning}</p>
      <p><strong>适配场景：</strong>{brief.scene_fit}</p>
      <p><strong>使用人群：</strong>{brief.audience}</p>
      <p className="meta-line">未出现明确表达时保持“未明确”；系统不会按案例反推客户审美或使用人群。</p>
    </section>

    <section className="requirement-section">
      <h3>面料、版型、色彩与纹样</h3>
      <p><strong>面料推荐：</strong>{brief.fabric}</p>
      <ul className="v4-fabric-reasons" aria-label="面料判断依据">
        {brief.fabric_reasons.map((item) => (
          <li key={item.label} data-empty={item.quotes.length === 0}>
            <b>{item.label}</b>
            <span>{item.text}</span>
          </li>
        ))}
      </ul>
      <p><strong>版型选择：</strong>{brief.silhouette}</p>
      <p><strong>色彩体系：</strong>{brief.color}</p>
      <p><strong>装饰纹样：</strong>{brief.pattern}</p>
      <p><strong>松弛余量：</strong>{brief.ease}</p>
    </section>

    <section className="requirement-section">
      <h3>工艺组合与设计亮点</h3>
      <p><strong>工艺组合：</strong>{brief.craft_composition}</p>
      <ul>{brief.design_highlights.map((item) => <li key={item}>{item}</li>)}</ul>
      <p className="meta-line">设计亮点只摘录当前方案和已记录资源特点；不代表最终设计评价。</p>
    </section>

    <section className="requirement-section">
      <h3>资源匹配适配说明</h3>
      {brief.resource_matches.length === 0
        ? <p>当前没有可追溯的资源匹配，需人工找样或确认。</p>
        : <div className="proposal-resource-list">{brief.resource_matches.map((item) => <article className="proposal-resource" key={`${item.field}-${item.id}`}>
            <p><strong>{item.field} · {item.id} {item.name}</strong></p>
            <p className="meta-line">来源：{item.sources.map(caseLabel).join("、") || "未记录"}</p>
            {(item.style_tags.length > 0 || item.scene_tags.length > 0) && <p>案例标签：{[...item.style_tags, ...item.scene_tags].join("、")}</p>}
            <ul>{item.usage_guidance.map((text) => <li key={text}>{text}</li>)}</ul>
          </article>)}</div>}
    </section>

    <section className="requirement-section">
      <h3>可行性与待办</h3>
      <ul>{brief.feasibility.map((item, index) => <li key={`${item.text}-${index}`}><strong>{item.level}：</strong>{item.text}</li>)}</ul>
      {brief.gaps.length === 0
        ? <p>自动核对未发现额外的资料缺口；仍需按右侧事项人工复核。</p>
        : <p className="meta-line">以上“待补资源”只说明资料或出处缺失，不代表已经找到可采购物料。</p>}
    </section>
  </div>;
}
