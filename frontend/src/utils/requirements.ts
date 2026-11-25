import type { RequirementsAnalysis, Session } from "../api/types";

export const AESTHETIC_TAGS = ["新中式", "中式", "优雅", "端庄", "气质", "简约", "内敛", "低调", "明快", "有活力", "松弛", "正式", "有质感"];
export const SCENE_TAGS = ["正式场合", "户外舞台", "演出", "婚礼", "宴会", "通勤", "发布会", "会议"];
export const AUDIENCE_TAGS = ["员工", "团队", "团体", "女士", "男士", "儿童", "老人"];

const OFFICIAL_DIMENSIONS = ["风格调性", "廓形要求", "面料质感", "工艺细节", "色彩体系", "场景适配度"];
const EXTRA_DIMENSIONS = ["情绪与审美", "使用场景", "使用人群"];

export function directTags(session: Session, vocabulary: string[]): string[] {
  const text = session.utterances.map((item) => item.text).join(" ");
  return vocabulary.filter((tag) => text.includes(tag));
}

export function structuredValue(title: string, session: Session, notes: Record<string, string>): string {
  const current = session.spec_versions[session.spec_versions.length - 1];
  if (title === "风格调性") return notes["风格调性"] || directTags(session, AESTHETIC_TAGS).join("、") || "未明确";
  if (title === "廓形要求") {
    return [notes["廓形要求"], current.fields["版型结构"]?.value, current.fields["松弛余量"]?.value]
      .filter(Boolean)
      .join("；") || "未明确";
  }
  if (title === "面料质感") return notes["面料质感"] || current.fields["面料"]?.value || "未明确";
  if (title === "工艺细节") return notes["工艺细节"] || "未识别到客户明确工艺要求";
  if (title === "色彩体系") return notes["色彩体系"] || current.fields["色彩"]?.value || "未明确";
  if (title === "场景适配度") return notes["场景适配度"] || notes["使用场景"] || directTags(session, SCENE_TAGS).join("、") || "未明确";
  if (title === "情绪与审美") return notes["情绪与审美"] || directTags(session, AESTHETIC_TAGS).join("、") || "未明确";
  if (title === "使用场景") return notes["使用场景"] || directTags(session, SCENE_TAGS).join("、") || "未明确";
  if (title === "使用人群") return notes["使用人群"] || directTags(session, AUDIENCE_TAGS).join("、") || "未明确";
  return notes[title] || "未明确";
}

export function buildRequirementReport(
  session: Session,
  notes: Record<string, string>,
  analysis: RequirementsAnalysis | null,
): string {
  const current = session.spec_versions[session.spec_versions.length - 1];
  return [
    `# 客户需求结构化报告 · 方案 v${current.version}`,
    "",
    `- 会话：${session.title}（${session.id}）`,
    `- 语义归类：${analysis ? `${analysis.model}，${analysis.review?.status || "待人工核对"}` : "尚未运行模型，当前为规则线索"}`,
    "",
    "## 官方六维",
    ...OFFICIAL_DIMENSIONS.flatMap((title) => {
      const category = analysis?.categories.find((item) => item.title === title);
      const quotes = session.utterances.filter((item) => category?.utterance_ids.includes(item.id)).map((item) => `“${item.text}”`);
      return [
        `### ${title}`,
        `- 结构化参数：${structuredValue(title, session, notes)}`,
        `- 原话依据：${quotes.join("；") || "未识别到相关原话，需人工确认"}`,
        `- 人工说明：${notes[title] || "未填写"}`,
        "",
      ];
    }),
    "## 情绪审美与使用信息",
    ...EXTRA_DIMENSIONS.map((title) => {
      const category = analysis?.categories.find((item) => item.title === title);
      const quotes = session.utterances.filter((item) => category?.utterance_ids.includes(item.id)).map((item) => `“${item.text}”`);
      return `- ${title}：${structuredValue(title, session, notes)}；依据：${quotes.join("；") || "未明确"}`;
    }),
    "",
    "## 边界",
    "- 模型归类与标签是待人工核对的结构化线索，不替代设计师判断。",
    "- 未明确项保持空白，不根据案例反推客户审美或使用人群。",
    "",
  ].join("\n");
}
