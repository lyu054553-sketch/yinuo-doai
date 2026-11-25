import type { ResourceLibrary, Session } from "../../api/types";
import { FIELDS } from "../../api/types";
import { lastChangedFields } from "../../utils/display";
import { findResource, originLabel } from "../../utils/resources";

interface Props {
  session: Session;
  resources: ResourceLibrary | null;
  activeId: string | null;
  onOpenPlan: () => void;
}

/** 右侧方案速览：核对和问客户时，随时看到方案八栏的当前值；刚改的栏标金色，选中句关联的栏描边。 */
export default function PlanPeek({ session, resources, activeId, onOpenPlan }: Props) {
  const current = session.spec_versions[session.spec_versions.length - 1];
  const changed = new Set(lastChangedFields(session));
  const active = session.utterances.find((u) => u.id === activeId) ?? null;
  const linked = new Set(active?.mappings.map((m) => m.field) ?? []);

  const located = session.utterances.filter((u) => session.input.text.slice(u.start, u.end) === u.text).length;
  const allIds = Array.from(new Set(FIELDS.flatMap((f) => current.fields[f]?.resource_ids ?? [])));
  const inLib = resources ? allIds.filter((id) => findResource(resources, id).found).length : null;

  return (
    <aside className="v3-peek" aria-label="方案速览">
      <header className="v3-peek-head">
        <h2>方案速览</h2>
        <span className="v2-pill v2-pill-strong">v{current.version}</span>
      </header>
      <p className="v3-peek-checks">
        <span data-ok={located === session.utterances.length}>
          原话 {located}/{session.utterances.length} 可溯源
        </span>
        {inLib !== null && (
          <span data-ok={inLib === allIds.length}>
            编号 {inLib}/{allIds.length} 在库
          </span>
        )}
      </p>
      <ul className="v3-peek-list">
        {FIELDS.map((field) => {
          const item = current.fields[field];
          return (
            <li
              key={field}
              className="v3-peek-row"
              data-changed={changed.has(field) && current.version > 1}
              data-linked={linked.has(field)}
            >
              <span className="v3-peek-name">{field}</span>
              <span className="v3-peek-value" title={item?.value}>
                {item?.value || "（空）"}
              </span>
              <span className="v3-peek-ids">
                {item?.origin === "无出处" ? (
                  <i className={field === "品类" ? "v3-peek-ok" : "v3-peek-none"}>{originLabel(field, item.origin)}</i>
                ) : (
                  item?.resource_ids.slice(0, 3).map((id) => <code key={id}>{id}</code>)
                )}
                {item?.confirmed && <i className="v3-peek-ok">客户已确认</i>}
              </span>
            </li>
          );
        })}
      </ul>
      <button type="button" className="v2-btn-secondary v3-peek-open" onClick={onOpenPlan}>
        看完整方案
      </button>
    </aside>
  );
}
