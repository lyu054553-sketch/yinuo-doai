import type { Field, ResourceCatalog, Session } from "../api/types";
import { FIELDS } from "../api/types";
import { getHandoff, getNotes, getPrompts, getRequirementsAnalysis, isMockMode } from "../api/client";
import { buildProposalBrief, proposalMarkdown } from "./proposal";
import { buildRequirementReport } from "./requirements";

/** 把 Markdown 的标题整体降几级，嵌进交付包的某一节里。 */
function demote(markdown: string, levels: number): string {
  const extra = "#".repeat(levels);
  return markdown
    .split("\n")
    .map((line) => (/^#{1,5} /.test(line) ? `${extra}${line}` : line))
    .join("\n");
}

function cell(text: string): string {
  return (text || "").replace(/\|/g, "／").replace(/\n/g, " ");
}

/**
 * 项目交付包：一个 Markdown 文件，交接单是目录和出口。
 * 五部分各对应命题的一类输出物，并写明给谁用。所有内容都取自当前会话和已有接口，不另外生成。
 */
export async function buildProjectPackage(session: Session, catalog: ResourceCatalog | null): Promise<string> {
  const [notes, analysis, handoff, prompts] = await Promise.all([
    getNotes(session.id).catch(() => ({} as Record<string, string>)),
    getRequirementsAnalysis(session.id).catch(() => null),
    getHandoff(session.id).catch(() => null),
    isMockMode ? Promise.resolve(null) : getPrompts(session.id).catch(() => null),
  ]);
  const current = session.spec_versions[session.spec_versions.length - 1];
  const brief = buildProposalBrief(session, notes, catalog, analysis);
  const fieldOf = (id: string) =>
    session.utterances.find((u) => u.id === id)?.mappings.map((m) => m.field).join("、") ?? "";

  const lines: string[] = [
    `# 衣诺 · 项目交付包`,
    "",
    `- 项目：${session.title}（${session.id}）`,
    `- 模式：${session.mode}；当前方案第 ${current.version} 版`,
    `- 导出时间：${new Date().toLocaleString("zh-CN", { hour12: false })}`,
    "- 性质：需求拆解、参考方案、视觉辅助和台账草稿。定制终审、报价、版型定稿、生产下单由业务人员完成。",
    "",
    "## 目录：谁用哪一部分",
    "",
    "| 部分 | 给谁 | 用来做什么 | 对应命题输出物 |",
    "| --- | --- | --- | --- |",
    "| 一、需求确认单 | 业务员、客户 | 当面核对客户说的每句话是否都落进方案、哪些已经确认 | 定制需求结构化参数清单、客户情绪审美标签报告 |",
    "| 二、方案初稿 | 设计师、业务员 | 风格定位、面料推荐（三条依据）、版型、工艺、场景、亮点、可行性 | 海派定制整体方案初稿、资源匹配适配说明、方案可行性提示 |",
    "| 三、视觉需求包 | 设计师 | 成衣、细节工艺、上身氛围三类提示词和核对项 | 成套标准化 AI 视觉提示词、定制视觉效果需求包 |",
    "| 四、交接单 | 设计师、制版师、打样 | 设计师待判断事项、待办、打样核对项、领料建议 | （交接出口，汇总以上成果） |",
    "| 五、修改记录 | 业务员、管理者 | 每一步谁改了什么，归档复盘 | 可编辑、可上报的定制业务专题台账 |",
    "",
    "---",
    "",
    "## 一、需求确认单",
    "",
    "### 客户原话",
    "",
    `> ${session.input.text}`,
    "",
    "### 逐句核对",
    "",
    "| 编号 | 原话 | 状态 | 强度 | 落在方案哪一栏 |",
    "| --- | --- | --- | --- | --- |",
    ...session.utterances.map(
      (u) => `| ${u.id} | ${cell(u.text)} | ${u.status} | ${u.strength} | ${cell(fieldOf(u.id))} |`,
    ),
    "",
    "### 和客户确认的结果",
    "",
  ];
  const answered = session.questions.filter((q) => q.answer);
  if (answered.length === 0) lines.push("还没有和客户确认的事项。");
  answered.forEach((q, index) => {
    const option = q.options.find((o) => o.id === q.answer);
    lines.push(`${index + 1}. 【${q.field}】${q.title} —— 客户选择：${option?.label ?? q.answer}`);
  });
  if (session.restatement) {
    lines.push("", "### 复述确认", "", `> ${session.restatement.text}`, "");
    session.restatement.points.forEach((p) => {
      lines.push(`- ${p.question} —— ${p.answer ? `客户：${p.answer}${p.note ? `（客户原意：${p.note}）` : ""}` : "待确认"}`);
    });
  }
  const open = session.questions.filter((q) => !q.answer);
  if (open.length) {
    lines.push("", "还没问的问题：");
    open.forEach((q) => lines.push(`- 【${q.field}】${q.title}`));
  }
  lines.push("", demote(buildRequirementReport(session, notes, analysis), 2), "", "---", "");

  lines.push("## 二、方案初稿", "", "### 八栏方案", "", "| 栏位 | 方案 | 资源编号 | 客户已确认 |", "| --- | --- | --- | --- |");
  FIELDS.forEach((field: Field) => {
    const item = current.fields[field];
    const empty = !item.value || item.value === "无特殊需求";
    const source = item.resource_ids.length
      ? item.resource_ids.join("、")
      : empty
        ? "—"
        : item.origin === "无出处"
          ? field === "品类" ? "取自客户原话" : "库中暂无 · 待找样"
          : item.origin;
    lines.push(`| ${field} | ${cell(item.value || "未写")} | ${source} | ${item.confirmed ? "是" : "否"} |`);
  });
  lines.push("", demote(proposalMarkdown(session, brief), 2), "---", "");

  lines.push("## 三、视觉需求包", "");
  if (prompts) {
    lines.push(`依据方案第 ${prompts.version} 版生成，可直接复制到外部 AI 生图工具。生成的图可以传回衣诺，逐项核对与方案是否一致。`, "");
    prompts.items.forEach((item) => {
      lines.push(`### ${item.kind}`, "", "```", item.text, "```", "");
    });
    if (prompts.checks.length) {
      lines.push("### 出图后逐项核对", "");
      prompts.checks.forEach((check) => lines.push(`- 【${check.field}】${check.expect}`));
      lines.push("");
    }
  } else {
    lines.push("示例模式不提供提示词；请在真实模式下导出。", "");
  }
  if (session.reference) {
    lines.push("### 客户参考图比对", "");
    session.reference.conflicts.forEach((c) => lines.push(`- 图和话对不上 · ${c.field}：${c.message}`));
    if (session.reference.conflicts.length === 0) lines.push("- 看得出的几项和客户原话没有冲突。");
    lines.push("");
  }
  lines.push("---", "");

  lines.push("## 四、交接单", "", handoff ? demote(handoff.markdown, 2) : "交接单暂时没有取到，请在「交接」页重试。", "", "---", "");

  lines.push("## 五、修改记录", "", "| 版本 | 时间 | 动作 | 摘要 |", "| --- | --- | --- | --- |");
  session.ledger.forEach((entry) => {
    lines.push(`| v${entry.version} | ${new Date(entry.at).toLocaleString("zh-CN", { hour12: false }).slice(0, -3)} | ${entry.action} | ${cell(entry.summary)} |`);
  });
  lines.push("", "完整台账（含每条明细）可在「交接 → 修改记录」导出 CSV / JSON。", "");
  return lines.join("\n");
}

export function downloadText(filename: string, content: string, type = "text/markdown;charset=utf-8"): void {
  const url = URL.createObjectURL(new Blob([content], { type }));
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
