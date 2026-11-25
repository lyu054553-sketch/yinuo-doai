import { useEffect, useState } from "react";
import type { Field, RequirementsAnalysis, Session, Utterance } from "../api/types";
import { getNotes, updateNote, isMockMode, getRequirementsAnalysis, reviewRequirementsAnalysis, runRequirementsAnalysis } from "../api/client";
import { readableError } from "../api/errors";
import {
  AESTHETIC_TAGS,
  AUDIENCE_TAGS,
  SCENE_TAGS,
  buildRequirementReport,
  directTags,
  structuredValue,
} from "../utils/requirements";

type Dimension = { title: string; fields?: Field[]; types?: string[]; keywords?: RegExp };

// 候选仅用于人工核对，不把字段映射或关键词命中当作已确认的客户意图。
const dimensions: Dimension[] = [
  { title: "风格调性", types: ["风格", "审美", "气质"], keywords: /风格|气质|端庄|简约|中式|商务/ },
  { title: "廓形要求", fields: ["版型结构", "松弛余量", "长度比例"] },
  { title: "面料质感", fields: ["面料"] },
  { title: "工艺细节", types: ["工艺"], keywords: /工艺|缝线|刺绣|滚边|纽扣|门襟|开衩/ },
  { title: "色彩体系", fields: ["色彩"] },
  { title: "场景适配度", types: ["场景", "活动", "用途"], keywords: /场合|场景|婚礼|宴会|通勤|演出|职业服/ },
];
const tags: Dimension[] = [
  { title: "情绪与审美", types: ["风格", "审美", "气质", "情绪"], keywords: /喜欢|希望|气质|风格|端庄|简约|优雅/ },
  { title: "使用场景", types: ["场景", "活动", "用途"], keywords: /场合|场景|婚礼|宴会|通勤|演出|职业服/ },
  { title: "使用人群", types: ["人群"], keywords: /员工|团队|团体|女士|男士|儿童|老人/ },
];

function candidates(dimension: Dimension, utterances: Utterance[]): Utterance[] {
  return utterances.filter((u) =>
    dimension.fields?.some((field) => u.mappings.some((m) => m.field === field)) ||
    dimension.types?.some((type) => u.type.includes(type)) || dimension.keywords?.test(u.text));
}

export default function RequirementsTab({ session }: { session: Session }) {
  const [notes, setNotes] = useState<Record<string, string>>({});
  const [message, setMessage] = useState("");
  const [ready, setReady] = useState(false);
  const [saving, setSaving] = useState(false);
  const [localDraft, setLocalDraft] = useState<Record<string, string>>({});
  const [analysis, setAnalysis] = useState<RequirementsAnalysis | null>(null);
  const [analyzing, setAnalyzing] = useState(false);
  const [analysisReady, setAnalysisReady] = useState(false);
  const [analysisMessage, setAnalysisMessage] = useState("");
  const [editingTitle, setEditingTitle] = useState<string | null>(null);
  const [draftIds, setDraftIds] = useState<Record<string, string[]>>({});
  const [reviewing, setReviewing] = useState(false);
  const [copiedReport, setCopiedReport] = useState(false);

  useEffect(() => {
    let active = true;
    try {
      const previous = JSON.parse(localStorage.getItem(`yinuo-requirements:${session.id}`) || "{}");
      if (previous && typeof previous === "object" && !Array.isArray(previous)) {
        setLocalDraft(Object.fromEntries(Object.entries(previous).filter((entry): entry is [string, string] => typeof entry[1] === "string" && entry[1].length > 0)));
      }
    } catch { /* 旧浏览器草稿无法读取时，不影响服务端记录。 */ }
    getNotes(session.id).then((data) => { if (active) { setNotes(data); setReady(true); } })
      .catch((err) => { if (active) setMessage(readableError(err)); });
    getRequirementsAnalysis(session.id).then((data) => { if (active) { setAnalysis(data); setAnalysisReady(true); } })
      .catch((err) => { if (active) { setAnalysisMessage(readableError(err)); setAnalysisReady(true); } });
    return () => { active = false; };
  }, [session.id]);

  async function save(title: string) {
    setSaving(true);
    try { await updateNote(session.id, title, notes[title] || ""); setMessage(isMockMode ? "已保存到本次示例会话，刷新后清空。" : "已保存到洽谈记录。"); }
    catch (err) { setMessage(readableError(err)); }
    finally { setSaving(false); }
  }

  async function runAnalysis() {
    setAnalyzing(true);
    setAnalysisMessage("");
    try { setAnalysis(await runRequirementsAnalysis(session.id)); setAnalysisMessage("模型分类已保存，请逐项人工核对。"); }
    catch (err) { setAnalysisMessage(readableError(err)); }
    finally { setAnalyzing(false); }
  }

  async function saveCategory(title: string) {
    if (!analysis) return;
    setReviewing(true);
    setAnalysisMessage("");
    try {
      const categories = analysis.categories.map((category) =>
        category.title === title ? { ...category, utterance_ids: draftIds[title] ?? category.utterance_ids } : category,
      );
      const next = await reviewRequirementsAnalysis(
        session.id,
        categories,
        `人工核对并修正“${title}”的原话归类。`,
      );
      setAnalysis(next);
      setEditingTitle(null);
      setAnalysisMessage(`“${title}”已由人工核对并保存，核对动作已进入台账。`);
    } catch (err) {
      setAnalysisMessage(readableError(err));
    } finally {
      setReviewing(false);
    }
  }

  async function copyReport() {
    const report = buildRequirementReport(session, notes, analysis);
    try {
      await navigator.clipboard.writeText(report);
      setCopiedReport(true);
      setTimeout(() => setCopiedReport(false), 1500);
    } catch {
      setAnalysisMessage("复制没有成功，请稍后从台账导出完整数据。");
    }
  }

  function renderGroup(items: Dimension[]) {
    return items.map((dimension) => {
      const category = analysis?.categories.find((item) => item.title === dimension.title);
      const source = category ? session.utterances.filter((u) => category.utterance_ids.includes(u.id)) : candidates(dimension, session.utterances);
      const isEditing = editingTitle === dimension.title;
      const selected = draftIds[dimension.title] ?? category?.utterance_ids ?? [];
      return <section className="requirement-section" key={dimension.title}>
        <h3>{dimension.title}</h3>
        <p className="requirement-parameter"><strong>结构化参数：</strong>{structuredValue(dimension.title, session, notes)}</p>
        <p className="meta-line">核对状态：{notes[dimension.title] ? "业务人员已补充说明" : source.length || !["风格调性", "场景适配度"].includes(dimension.title) ? "自动识别，待人工确认" : "未明确"}</p>
        {source.length ? <ul>{source.map((u) => <li key={u.id}><span className="meta-line">{u.id} · {u.strength}</span> “{u.text}”</li>)}</ul> : <p className="meta-line">未识别到相关原话，请人工确认是否未明确。</p>}
        <label>业务人员补充说明<textarea disabled={!ready || saving} value={notes[dimension.title] || ""} onChange={(e) => setNotes({ ...notes, [dimension.title]: e.target.value })} placeholder="可填写已确认内容" rows={2} /></label>
        <button type="button" className="btn btn-quiet btn-sm" disabled={!ready || saving} onClick={() => save(dimension.title)}>保存{dimension.title}说明</button>
        {analysis && <details className="requirement-editor" open={isEditing}>
          <summary onClick={(event) => {
            event.preventDefault();
            if (isEditing) {
              setEditingTitle(null);
            } else {
              setEditingTitle(dimension.title);
              setDraftIds({ ...draftIds, [dimension.title]: category?.utterance_ids ?? [] });
            }
          }}>{isEditing ? "收起人工修正" : "人工核对或修正归类"}</summary>
          {isEditing && <>
            <div className="requirement-options">{session.utterances.map((utterance) => <label className="requirement-option" key={utterance.id}>
              <input
                type="checkbox"
                checked={selected.includes(utterance.id)}
                onChange={(event) => setDraftIds({
                  ...draftIds,
                  [dimension.title]: event.target.checked
                    ? [...selected, utterance.id]
                    : selected.filter((id) => id !== utterance.id),
                })}
              />
              <span>{utterance.id} · {utterance.text}</span>
            </label>)}</div>
            <button type="button" className="btn btn-secondary btn-sm" disabled={reviewing} onClick={() => saveCategory(dimension.title)}>{reviewing ? "保存中…" : "保存人工核对结果"}</button>
          </>}
        </details>}
      </section>;
    });
  }
  return <div role="tabpanel" className="requirements-report">
    <p className="section-title">客户需求六维报告</p>
    <p className="hint-line">{analysis ? `下列原话由 ${analysis.model} 进行语义归类。` : "下列原话按已有字段与关键词归类。"}分类是待人工确认的线索，不等于最终判断。补充说明需要逐项保存；不会自动修改方案。</p>
    <div className="requirements-actions">
      {!isMockMode && <button type="button" className="btn btn-secondary btn-sm" disabled={analyzing || !analysisReady} onClick={runAnalysis}>{analyzing ? "正在进行语义归类…" : analysis ? "重新进行语义归类" : "用模型进行语义归类"}</button>}
      <button type="button" className="btn btn-quiet btn-sm" onClick={copyReport}>{copiedReport ? "已复制结构化报告" : "复制结构化需求报告"}</button>
    </div>
    {analysis && <p className="meta-line">人工核对状态：{analysis.review?.status || "待人工核对"}{analysis.review?.reviewed_at ? ` · ${analysis.review.reviewed_at}` : ""}</p>}
    <p className="requirement-tag-line"><strong>情绪审美标签：</strong>{directTags(session, AESTHETIC_TAGS).join("、") || "未明确"}</p>
    <p className="requirement-tag-line"><strong>场景与原话标签：</strong>{[...directTags(session, SCENE_TAGS), ...directTags(session, AUDIENCE_TAGS)].join("、") || "未明确"}</p>
    <p className="meta-line" role="status">{analysisMessage}</p>
    {Object.keys(localDraft).length > 0 && <div className="alert alert-info">发现这个浏览器里的旧版草稿。点击载入后请逐项检查并保存；已有服务端内容不会被自动覆盖。
      <button type="button" className="btn btn-quiet btn-sm" disabled={!ready || saving} onClick={() => { setNotes({ ...localDraft, ...notes }); setMessage("已载入旧草稿中尚未保存的项目，请逐项检查并保存。"); }}>载入旧草稿</button>
    </div>}
    {renderGroup(dimensions)}
    <p className="section-title">情绪审美与使用信息</p>
    {renderGroup(tags)}
    <p className="meta-line" aria-live="polite">{message}</p>
  </div>;
}
