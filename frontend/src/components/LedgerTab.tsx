import type { ResourceCatalog, Session } from "../api/types";
import { useEffect, useState } from "react";
import { getNotes, getPrompts, getRequirementsAnalysis, updateNote, isMockMode } from "../api/client";
import { readableError } from "../api/errors";
import { formatTime } from "../utils/display";
import { buildProposalBrief } from "../utils/proposal";
import { buildRequirementReport } from "../utils/requirements";
import { caseLabel } from "../utils/resources";

export default function LedgerTab({ session, catalog }: { session: Session; catalog: ResourceCatalog | null }) {
  const entries = [...session.ledger].reverse();
  const [note, setNote] = useState("");
  const [ready, setReady] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  useEffect(() => {
    let active = true;
    getNotes(session.id).then((data) => { if (active) { setNote(data["业务台账备注"] || ""); setReady(true); } })
      .catch((error) => { if (active) setMessage(readableError(error)); });
    return () => { active = false; };
  }, [session.id]);
  async function save() {
    setBusy(true);
    try { await updateNote(session.id, "业务台账备注", note); setMessage(isMockMode ? "已保存到本次示例会话，刷新后清空。" : "台账备注已保存。"); }
    catch (error) { setMessage(readableError(error)); }
    finally { setBusy(false); }
  }
  async function download(format: "json" | "csv") {
    setBusy(true);
    try {
      const [notes, prompts, analysis] = await Promise.all([getNotes(session.id), isMockMode ? Promise.resolve(null) : getPrompts(session.id), getRequirementsAnalysis(session.id)]);
      const proposal = buildProposalBrief(session, notes, catalog, analysis);
      const requirementReport = buildRequirementReport(session, notes, analysis);
      const archive = {
        格式: "衣诺业务专题台账 v2",
        导出时间: new Date().toISOString(),
        会话: session,
        人工补充说明: notes,
        模型语义归类: analysis,
        结构化需求报告: requirementReport,
        方案初稿: proposal,
        视觉需求包: prompts ? {
          依据方案版本: prompts.version,
          用途: "成衣挂拍、面料细节与上身效果视觉生成或外部生成图核对",
          提示词: prompts.items,
          核对项: prompts.checks,
          边界: "提示词不是成衣质量保证；视觉判断需人工复核。",
        } : null,
      };
      let content = JSON.stringify(archive, null, 2);
      if (format === "csv") {
        const rows: string[][] = [["类别", "项目", "内容", "来源或时间", "状态或备注"]];
        rows.push(["项目信息", "会话", session.title, session.created_at, `${session.mode} · ${session.source}`]);
        rows.push(["需求", "客户原话", session.input.text, "客户口述", ""]);
        session.utterances.forEach((u) => rows.push(["需求解构", u.type, u.text, u.id, `${u.strength} · ${u.status}`]));
        analysis?.categories.forEach((category) => {
          const text = session.utterances.filter((u) => category.utterance_ids.includes(u.id)).map((u) => u.text).join("；");
          rows.push(["模型语义归类", category.title, text || "未识别到原话", category.utterance_ids.join("、"), `模型 ${analysis.model}，需人工确认`]);
        });
        rows.push(["结构化需求报告", "全文", requirementReport, `方案 v${session.spec_versions[session.spec_versions.length - 1].version}`, "含官方六维、原话依据和人工说明"]);
        rows.push(["方案初稿", "风格定位", proposal.style_positioning, "需求原话与人工说明", "待业务确认"]);
        rows.push(["方案初稿", "面料推荐", proposal.fabric, session.spec_versions[session.spec_versions.length - 1].fields["面料"].resource_ids.join("、"), "案例参考，非库存"]);
        rows.push(["方案初稿", "版型选择", proposal.silhouette, session.spec_versions[session.spec_versions.length - 1].fields["版型结构"].resource_ids.join("、"), "案例参考"]);
        rows.push(["方案初稿", "工艺组合", proposal.craft_composition, "资源库或人工说明", "需专业人员确认"]);
        rows.push(["方案初稿", "适配场景", proposal.scene_fit, "需求原话或人工说明", "待业务确认"]);
        rows.push(["方案初稿", "设计亮点", proposal.design_highlights.join("；"), "当前方案与资源特性", "非最终设计评价"]);
        proposal.resource_matches.forEach((item) => rows.push(["资源适配", `${item.field} ${item.id}`, item.name, item.sources.map(caseLabel).join("、") || "未记录", item.usage_guidance.join("；")]));
        proposal.feasibility.forEach((item) => rows.push(["可行性", item.level, item.text, "系统提示", "需人工复核"]));
        const current = session.spec_versions[session.spec_versions.length - 1];
        Object.entries(current.fields).forEach(([field, item]) => rows.push(["方案", field, item.value, item.resource_ids.join("、"), `${item.origin} · v${current.version}`]));
        Object.entries(notes).forEach(([title, value]) => rows.push(["人工补充", title, value, "业务人员保存", ""]));
        session.ledger.forEach((entry) => rows.push(["台账", entry.action, entry.summary, entry.at, entry.detail]));
        prompts?.items.forEach((item) => rows.push(["视觉需求包", item.kind, item.text, `方案 v${prompts.version}`, "提示词，非成衣质量保证"]));
        const cell = (value: string) => {
          const safe = /^[\s\u0000-\u001f]*[=+\-@]/.test(value) ? `'${value}` : value;
          return `"${safe.replace(/"/g, '""')}"`;
        };
        content = `\ufeff${rows.map((row) => row.map(cell).join(",")).join("\r\n")}\r\n`;
      }
      const url = URL.createObjectURL(new Blob([content], { type: format === "csv" ? "text/csv;charset=utf-8" : "application/json;charset=utf-8" }));
      const link = document.createElement("a");
      link.href = url;
      link.download = `衣诺台账-${session.id}.${format}`;
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      setMessage(isMockMode ? "台账已导出；示例模式不提供视觉提示词。" : "台账已导出，包含已保存的说明和当前方案版本提示词。");
    } catch (error) { setMessage(`导出失败：${readableError(error)}`); }
    finally { setBusy(false); }
  }
  return (
    <div className="tab-panel" role="tabpanel">
      <div className="requirement-section">
        <label>业务台账备注<textarea rows={3} value={note} disabled={!ready || busy} onChange={(event) => setNote(event.target.value)} /></label>
        <button type="button" className="btn btn-quiet btn-sm" disabled={!ready || busy} onClick={save}>保存台账备注</button>
        <button type="button" className="btn btn-secondary btn-sm" disabled={!ready || busy} onClick={() => download("csv")}>导出可编辑表格（CSV）</button>
        <button type="button" className="btn btn-quiet btn-sm" disabled={!ready || busy} onClick={() => download("json")}>导出完整数据（JSON）</button>
        <p className="hint-line">请先保存修改；导出包含客户原话、方案、资源编号、视觉记录和已保存的备注，请谨慎保管。</p>
        <p className="meta-line" aria-live="polite">{message}</p>
      </div>
      {entries.length === 0 && <p className="empty-hint">台账还是空的。</p>}
      <ol className="ledger-list">
        {entries.map((entry, index) => (
          <li key={`${entry.version}-${entry.at}-${index}`} className={`ledger-item ledger-${entry.action}`}>
            <div className="ledger-line">
              <span className="ledger-version">v{entry.version}</span>
              <span className="ledger-action">{entry.action}</span>
              <span className="ledger-time">{formatTime(entry.at)}</span>
            </div>
            <p className="ledger-summary">{entry.summary}</p>
            <p className="ledger-detail">{entry.detail}</p>
            {entry.changed_fields.length > 0 && (
              <p className="ledger-fields">改动：{entry.changed_fields.join("、")}</p>
            )}
          </li>
        ))}
      </ol>
    </div>
  );
}
