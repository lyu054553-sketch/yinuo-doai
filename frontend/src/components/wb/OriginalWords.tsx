import { useMemo } from "react";
import type { Session, Status, Utterance } from "../../api/types";
import { STATUS_ORDER } from "../../api/types";
import { STATUS_HINT, statusKey, statusLabel } from "../../utils/display";
import StatusBadge from "./StatusBadge";
import StrengthTag from "./StrengthTag";
import ReferencePanel from "./ReferencePanel";

interface Props {
  session: Session;
  activeId: string | null;
  onSelect: (id: string | null) => void;
  onSessionUpdated: (session: Session) => void;
  /** 这句话关联了一个还没问的客户问题时，给出“去确认”的入口。 */
  onAsk: (questionId: string) => void;
}

interface Segment {
  text: string;
  utterance: Utterance | null;
}

function buildSegments(text: string, utterances: Utterance[]): Segment[] {
  const sorted = [...utterances].sort((a, b) => a.start - b.start);
  const out: Segment[] = [];
  let cursor = 0;
  sorted.forEach((u) => {
    if (u.start < cursor || u.end > text.length || u.start >= u.end) return;
    if (u.start > cursor) out.push({ text: text.slice(cursor, u.start), utterance: null });
    out.push({ text: text.slice(u.start, u.end), utterance: u });
    cursor = u.end;
  });
  if (cursor < text.length) out.push({ text: text.slice(cursor), utterance: null });
  return out;
}

export default function OriginalWords({ session, activeId, onSelect, onSessionUpdated, onAsk }: Props) {
  const segments = useMemo(() => buildSegments(session.input.text, session.utterances), [session.input.text, session.utterances]);
  const present = useMemo(() => {
    const set = new Set<Status>(session.utterances.map((u) => u.status));
    return STATUS_ORDER.filter((s) => set.has(s));
  }, [session.utterances]);
  const active = session.utterances.find((u) => u.id === activeId) ?? null;
  const pendingQuestion = active
    ? session.questions.find((q) => !q.answer && q.utterance_ids.includes(active.id)) ?? null
    : null;
  const designerItem = active ? session.designer_items.find((d) => d.utterance_ids.includes(active.id)) ?? null : null;

  return (
    <aside className="v2-words" aria-label="客户原话">
      <div className="v2-section-title">
        <h2>客户原话</h2>
        <span>点一句看详情</span>
      </div>

      <p className="v2-words-text">
        {segments.map((seg, i) =>
          seg.utterance ? (
            <button
              key={`${seg.utterance.id}-${i}`}
              type="button"
              className="v2-seg"
              data-status={statusKey(seg.utterance.status)}
              aria-pressed={activeId === seg.utterance.id}
              title={`${statusLabel(seg.utterance.status)}：${STATUS_HINT[seg.utterance.status]}`}
              onClick={() => onSelect(activeId === seg.utterance!.id ? null : seg.utterance!.id)}
            >
              {seg.text}
            </button>
          ) : (
            <span key={`t-${i}`}>{seg.text}</span>
          ),
        )}
      </p>

      <div className="v2-legend" aria-label="标记说明">
        {present.map((s) => (
          <span key={s}>
            <i style={{ background: `var(--st-${statusKey(s)})` }} />
            {statusLabel(s)}
          </span>
        ))}
      </div>

      {active ? (
        <div className="v2-detail" aria-live="polite">
          <p className="v2-detail-quote">“{active.text}”</p>
          <div className="v2-detail-row">
            <StatusBadge status={active.status} />
            <StrengthTag strength={active.strength} />
            <span>{STATUS_HINT[active.status]}</span>
          </div>
          <div className="v2-detail-row">
            落在：
            {Array.from(new Set(active.mappings.map((m) => (m.garment ? `${m.garment}·${m.field}` : m.field)))).map((f) => (
              <span key={f} className="v2-field">
                {f}
              </span>
            ))}
          </div>
          {active.mappings
            .filter((m) => m.note)
            .map((m, i) => (
              <p key={i} className="v2-detail-note">
                {m.note}
              </p>
            ))}
          {pendingQuestion && (
            <button type="button" className="v2-btn-secondary" onClick={() => onAsk(pendingQuestion.id)}>
              去和客户确认这一点 →
            </button>
          )}
          {designerItem && !pendingQuestion && <p className="v2-detail-hint">这一点已转给设计师判断，会写进交接单。</p>}
        </div>
      ) : (
        <p className="v2-detail-hint">颜色是 AI 的初步判断，点一句可以看依据，再由你核对。</p>
      )}

      <ReferencePanel session={session} onSelect={onSelect} onSessionUpdated={onSessionUpdated} />
    </aside>
  );
}
