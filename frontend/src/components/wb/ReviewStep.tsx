import type { Session } from "../../api/types";
import { STATUS_ORDER } from "../../api/types";
import { needsAction } from "../../utils/display";
import RequirementsTab from "../RequirementsTab";
import StatusBadge from "./StatusBadge";
import StrengthTag from "./StrengthTag";
import type { Utterance } from "../../api/types";

/** 取和这句话状态一致的那条理由；多条映射时，不要拿“已落实”的理由去解释“前后矛盾”。 */
function noteOf(u: Utterance): string {
  return (u.mappings.find((m) => m.status === u.status && m.note) ?? u.mappings.find((m) => m.note))?.note ?? "";
}

interface Props {
  session: Session;
  activeId: string | null;
  onSelect: (id: string | null) => void;
  onGoConfirm: () => void;
  onGoPlan: () => void;
}

/** 第一步：先给结论，再列要处理的句子；落实了的、报告类内容默认收起。 */
export default function ReviewStep({ session, activeId, onSelect, onGoConfirm, onGoPlan }: Props) {
  const total = session.utterances.length;
  const pendingQuestions = session.questions.filter((q) => !q.answer);
  const issues = session.utterances
    .filter((u) => needsAction(u.status))
    .sort((a, b) => STATUS_ORDER.indexOf(a.status) - STATUS_ORDER.indexOf(b.status) || a.start - b.start);
  const settled = session.utterances.filter((u) => !needsAction(u.status));

  return (
    <div className="v2-stage">
      <header className="v2-stage-head">
        <div>
          <h2>核对需求</h2>
          <p>
            客户一共说了 {total} 句，{settled.length} 句方案里能对上，{issues.length} 句需要处理。
          </p>
        </div>
      </header>

      <div className="v2-summary">
        <div className="v2-stat" data-tone={pendingQuestions.length ? "alert" : "ok"}>
          <strong>{pendingQuestions.length}</strong>
          <span>个问题还没问客户</span>
        </div>
        <div className="v2-stat" data-tone="designer">
          <strong>{session.designer_items.length}</strong>
          <span>件事交给设计师判断</span>
        </div>
        <div className="v2-stat" data-tone="ok">
          <strong>{settled.length}</strong>
          <span>句已落实或间接满足</span>
        </div>
      </div>

      {pendingQuestions.length > 0 ? (
        <div className="v2-cta">
          <p>
            先把这 {pendingQuestions.length} 个问题和客户当面问清楚，方案会跟着自动更新。
          </p>
          <button type="button" className="v2-btn-primary" onClick={onGoConfirm}>
            开始和客户确认
          </button>
        </div>
      ) : (
        <div className="v2-cta">
          <p>需要客户回答的都已经确认，可以看方案了。</p>
          <button type="button" className="v2-btn-primary" onClick={onGoPlan}>
            查看方案
          </button>
        </div>
      )}

      <section className="v2-card" aria-label="需要处理的句子">
        <div className="v2-card-title">
          <h3>
            需要处理的句子 <span className="v2-count">{issues.length}</span>
          </h3>
        </div>
        {issues.length === 0 ? (
          <p className="v2-empty">没有需要处理的句子。</p>
        ) : (
          <ul className="v2-issue-list">
            {issues.map((u) => (
              <li key={u.id}>
                <button
                  type="button"
                  className="v2-issue"
                  aria-pressed={activeId === u.id}
                  onClick={() => onSelect(activeId === u.id ? null : u.id)}
                >
                  <StatusBadge status={u.status} />
                  <span className="v2-issue-quote">
                    “{u.text}” <StrengthTag strength={u.strength} />
                  </span>
                  <span className="v2-issue-note">{noteOf(u)}</span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>

      <details className="v2-more">
        <summary>
          已经落实的 {settled.length} 句
        </summary>
        <div className="v2-more-body">
          <ul className="v2-issue-list">
            {settled.map((u) => (
              <li key={u.id}>
                <button
                  type="button"
                  className="v2-issue"
                  aria-pressed={activeId === u.id}
                  onClick={() => onSelect(activeId === u.id ? null : u.id)}
                >
                  <StatusBadge status={u.status} />
                  <span className="v2-issue-quote">
                    “{u.text}” <StrengthTag strength={u.strength} />
                  </span>
                  <span className="v2-issue-note">{noteOf(u)}</span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      </details>

      {session.skipped.length > 0 && (
        <details className="v2-more">
          <summary>不用问客户的 {session.skipped.length} 项</summary>
          <div className="v2-more-body">
            <ul className="v2-skip">
              {session.skipped.map((item, i) => (
                <li key={`${item.field}-${i}`}>
                  <b>{item.field}</b>
                  <span>{item.reason}</span>
                </li>
              ))}
            </ul>
          </div>
        </details>
      )}

      <details className="v2-more">
        <summary>需求维度报告（风格、廓形、面料等九个维度）</summary>
        <div className="v2-more-body">
          <RequirementsTab session={session} />
        </div>
      </details>
    </div>
  );
}
