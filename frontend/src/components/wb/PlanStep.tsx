import { useEffect, useRef, useState } from "react";
import type { Field, ResourceCatalog, ResourceLibrary, Session } from "../../api/types";
import { FIELDS } from "../../api/types";
import { patchSpec } from "../../api/client";
import { readableError } from "../../api/errors";
import { lastChangedFields } from "../../utils/display";
import { findResource, originLabel, sourceText, stockFor } from "../../utils/resources";
import ProposalTab from "../ProposalTab";
import SimilarCases from "./SimilarCases";
import FabricSwitcher from "./FabricSwitcher";
import ImagesTab from "../ImagesTab";

type View = "spec" | "draft" | "images";

interface Props {
  session: Session;
  resources: ResourceLibrary | null;
  catalog: ResourceCatalog | null;
  activeId: string | null;
  onSessionUpdated: (session: Session) => void;
  onGoHandoff: () => void;
}

/** 第三步：方案像一份文档；改过的地方标出来，出处点开才看。 */
export default function PlanStep({ session, resources, catalog, activeId, onSessionUpdated, onGoHandoff }: Props) {
  const [view, setView] = useState<View>("spec");
  const versions = session.spec_versions;
  const current = versions[versions.length - 1];
  const first = versions[0];
  const latestChanged = new Set(lastChangedFields(session));
  const active = session.utterances.find((u) => u.id === activeId) ?? null;
  const linked = new Set(active?.mappings.map((m) => m.field) ?? []);
  const changedSinceFirst = FIELDS.filter((f) => (first.fields[f]?.value ?? "") !== (current.fields[f]?.value ?? ""));
  const noSource = FIELDS.filter((f) => f !== "品类" && current.fields[f]?.origin === "无出处");
  const stock = stockFor(
    resources,
    (["面料", "装饰纹样", "色彩"] as Field[]).flatMap((f) => current.fields[f]?.resource_ids ?? []),
  ).slice(0, 2);

  // 生成后校验，在浏览器里再核一遍：原话能否在客户原文里原样找到、资源编号是否在资源库里。
  const located = session.utterances.filter((u) => session.input.text.slice(u.start, u.end) === u.text).length;
  const allIds = Array.from(new Set(FIELDS.flatMap((f) => current.fields[f]?.resource_ids ?? [])));
  const idsInLibrary = resources ? allIds.filter((id) => findResource(resources, id).found).length : null;

  // 版本号一变，就让本版改动的栏位闪一下。
  const seenVersion = useRef(current.version);
  const [flash, setFlash] = useState<Set<Field>>(new Set());
  useEffect(() => {
    if (current.version !== seenVersion.current) {
      seenVersion.current = current.version;
      setFlash(new Set(lastChangedFields(session)));
      const t = window.setTimeout(() => setFlash(new Set()), 1900);
      return () => window.clearTimeout(t);
    }
    return undefined;
  }, [current.version, session]);

  const [editing, setEditing] = useState<Field | null>(null);
  const [draft, setDraft] = useState("");
  const [reason, setReason] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function startEdit(field: Field) {
    setEditing(field);
    setDraft(current.fields[field].value);
    setReason("");
    setError(null);
  }

  async function save() {
    if (!editing) return;
    if (!draft.trim()) return setError("这一栏不能留空。");
    if (!reason.trim()) return setError("写一句为什么改，会记进修改记录。");
    setSaving(true);
    setError(null);
    try {
      onSessionUpdated(await patchSpec(session.id, { field: editing, value: draft, reason }));
      setEditing(null);
    } catch (err) {
      setError(readableError(err));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="v2-stage">
      <header className="v2-stage-head">
        <div>
          <h2>方案</h2>
          <p>
            当前第 {current.version} 版
            {changedSinceFirst.length > 0 && `，比第 1 版改了 ${changedSinceFirst.length} 处`}
            {noSource.length > 0 && `；有 ${noSource.length} 栏库中暂无对应做法，待人工找样`}。
          </p>
        </div>
        <div className="v2-seg-control" role="tablist" aria-label="方案视图">
          {(
            [
              ["spec", "方案要点"],
              ["draft", "完整初稿"],
              ["images", "效果图"],
            ] as [View, string][]
          ).map(([key, label]) => (
            <button key={key} type="button" role="tab" aria-selected={view === key} onClick={() => setView(key)}>
              {label}
            </button>
          ))}
        </div>
      </header>

      {view === "spec" && <SimilarCases session={session} onSessionUpdated={onSessionUpdated} />}

      {view === "spec" && (
        <section className="v2-doc" aria-label="方案要点">
          <div className="v2-doc-head">
            <div>
              <h3>{session.title}</h3>
              <p>做法来自上服案例或模拟库，仓库为模拟数据；最终定稿、量体和报价由人完成。</p>
            </div>
            <span className="v2-pill v2-pill-strong">v{current.version}</span>
          </div>
          <ul className="v2-checks" aria-label="生成后校验">
            <li data-ok={located === session.utterances.length}>
              <b>原话溯源</b> {located}/{session.utterances.length} 句能在客户原文里原样找到
            </li>
            <li data-ok={idsInLibrary === null ? undefined : idsInLibrary === allIds.length}>
              <b>资源编号</b>{" "}
              {idsInLibrary === null ? `引用了 ${allIds.length} 个，资源库未加载，暂无法核对` : `引用了 ${allIds.length} 个，${idsInLibrary} 个在资源库里查得到`}
            </li>
            <li data-ok={noSource.length === 0}>
              <b>出处</b> {noSource.length === 0 ? "每一栏都能在资源库里查到做法，或直接取自客户原话" : `${noSource.length} 栏库中暂无，已标出，待人工找样`}
            </li>
          </ul>
          {FIELDS.map((field) => {
            const item = current.fields[field];
            const before = first.fields[field]?.value ?? "";
            const changed = before !== item.value;
            return (
              <div
                key={field}
                className="v2-row"
                data-linked={linked.has(field)}
                data-changed={latestChanged.has(field)}
                data-flash={flash.has(field)}
              >
                <span className="v2-row-name">{field}</span>
                <div className="v2-row-main">
                  {editing === field ? null : <span className="v2-row-value">{item.value || "（空）"}</span>}
                  {changed && editing !== field && (
                    <span className="v2-row-prev">
                      第 1 版：<del>{before || "（空）"}</del>
                    </span>
                  )}
                  {editing !== field && (
                    <div className="v2-row-tags">
                      {latestChanged.has(field) && <span className="v2-tag v2-tag-ink">本版更新</span>}
                      {item.confirmed && <span className="v2-tag v2-tag-confirm">客户已确认</span>}
                      {originLabel(field, item.origin) && (
                        <span className={field === "品类" ? "v2-tag" : "v2-tag v2-tag-none"}>{originLabel(field, item.origin)}</span>
                      )}
                      {item.origin === "人工修改" && <span className="v2-tag">人工修改</span>}
                      {item.resource_ids.slice(0, 2).map((id) => {
                        const r = findResource(resources, id);
                        const src = sourceText(r.sources);
                        return (
                          <span key={id} className="v2-source-chip" data-found={r.found} title={src ? `来自${src}` : undefined}>
                            <code>{id}</code> {r.name ?? "资源库里查不到"}
                            {src ? ` · ${src}` : ""}
                          </span>
                        );
                      })}
                      {item.resource_ids.length > 2 && (
                        <details className="v2-sources">
                          <summary>还有 {item.resource_ids.length - 2} 项出处</summary>
                          <ul>
                            {item.resource_ids.slice(2).map((id) => {
                              const r = findResource(resources, id);
                              const src = sourceText(r.sources);
                              return (
                                <li key={id}>
                                  <code>{id}</code> {r.name ?? "资源库里查不到"}
                                  {src ? `（来自${src}）` : ""}
                                </li>
                              );
                            })}
                          </ul>
                        </details>
                      )}
                    </div>
                  )}
                  {editing !== field && field === "面料" && stock.length > 0 && (
                    <ul className="v4-stock" aria-label="仓库现货（模拟）">
                      {stock.map((entry) => (
                        <li key={entry.sku} data-status={entry.status}>
                          <span className="v4-stock-tag">仓库 · 模拟</span>
                          <code>{entry.sku}</code>
                          <span>
                            {entry.name} · {entry.colorway}
                          </span>
                          <b>
                            {entry.status}
                            {entry.stock_m > 0 ? ` ${entry.stock_m} 米` : ""}
                            {entry.lead_days > 0 ? ` · 约 ${entry.lead_days} 天到货` : ""}
                          </b>
                        </li>
                      ))}
                    </ul>
                  )}
                  {editing !== field && field === "面料" && (
                    <FabricSwitcher session={session} onSessionUpdated={onSessionUpdated} />
                  )}
                </div>
                {editing !== field && (
                  <div className="v2-row-actions">
                    <button type="button" className="v2-link" onClick={() => startEdit(field)}>
                      修改
                    </button>
                  </div>
                )}
                {editing === field && (
                  <div className="v2-row-edit">
                    <textarea className="v2-input" rows={3} value={draft} onChange={(e) => setDraft(e.target.value)} aria-label={`修改${field}`} />
                    <input
                      className="v2-input"
                      value={reason}
                      placeholder="为什么改（必填，会记进修改记录）"
                      onChange={(e) => setReason(e.target.value)}
                      aria-label="修改原因"
                    />
                    {error && <div className="v2-alert">{error}</div>}
                    <div className="v2-row-edit-actions">
                      <button type="button" className="v2-btn-primary" disabled={saving} onClick={save}>
                        {saving ? "保存中…" : "保存为新版本"}
                      </button>
                      <button type="button" className="v2-btn-secondary" onClick={() => setEditing(null)}>
                        取消
                      </button>
                    </div>
                  </div>
                )}
              </div>
            );
          })}
        </section>
      )}

      {view === "draft" && (
        <div className="v2-embed">
          <ProposalTab session={session} catalog={catalog} />
        </div>
      )}

      {view === "images" && (
        <div className="v2-embed">
          <ImagesTab session={session} onSessionUpdated={onSessionUpdated} />
        </div>
      )}

      <div className="v2-cta">
        <p>和设计师一起过一遍方案、出处和待确认事项，再整理成交接单。</p>
        <button type="button" className="v2-btn-primary" onClick={onGoHandoff}>
          去交接
        </button>
      </div>
    </div>
  );
}
