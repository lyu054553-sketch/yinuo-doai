import { useEffect, useState } from "react";
import RestatementCard from "./RestatementCard";
import type { Field, Question, Session } from "../../api/types";
import StrengthTag from "./StrengthTag";
import Mascot from "../Mascot";

export interface ChangeNote {
  version: number;
  question: string;
  answer: string;
  changes: { field: Field; before: string; after: string }[];
  todo: string | null;
}

interface Props {
  session: Session;
  focusQuestionId: string | null;
  answeringId: string | null;
  lastChange: ChangeNote | null;
  onAnswer: (question: Question, optionId: string) => void;
  onSelect: (id: string | null) => void;
  onSessionUpdated: (session: Session) => void;
  onGoPlan: () => void;
}

function quoteOf(session: Session, q: Question): string {
  return q.utterance_ids
    .map((id) => session.utterances.find((u) => u.id === id)?.text)
    .filter(Boolean)
    .join("”“");
}

/** 第二步：一次只问一个问题，答完立刻告诉业务员方案改了什么。 */
export default function ConfirmStep({
  session,
  focusQuestionId,
  answeringId,
  lastChange,
  onAnswer,
  onSelect,
  onSessionUpdated,
  onGoPlan,
}: Props) {
  const questions = session.questions;
  const answered = questions.filter((q) => q.answer);
  const [reopenId, setReopenId] = useState<string | null>(null);

  // 从原话或总览点进来时，直接打开那一题。
  useEffect(() => {
    if (focusQuestionId) setReopenId(focusQuestionId);
  }, [focusQuestionId]);

  // 改选的题答完后自动回到下一道没答的题。
  useEffect(() => {
    if (reopenId && lastChange && questions.find((q) => q.id === reopenId)?.answer) setReopenId(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lastChange]);

  const current =
    (reopenId && questions.find((q) => q.id === reopenId)) || questions.find((q) => !q.answer) || null;
  const position = current ? questions.indexOf(current) + 1 : questions.length;
  const progress = questions.length ? (answered.length / questions.length) * 100 : 100;

  useEffect(() => {
    if (current && current.utterance_ids[0]) onSelect(current.utterance_ids[0]);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [current?.id]);

  return (
    <div className="v2-stage">
      <header className="v2-stage-head">
        <div>
          <h2>和客户确认</h2>
          <p>只问会改变方案的问题。念给客户听，按客户的真实回答点选；方案随之更新，并记进修改记录。</p>
        </div>
      </header>

      {questions.length > 0 && (
        <div className="v2-qprogress" aria-label="确认进度">
          <span>
            已确认 {answered.length} / {questions.length}
          </span>
          <span className="v2-qbar">
            <i style={{ width: `${progress}%` }} />
          </span>
        </div>
      )}

      {lastChange && (
        <div className="v2-change" role="status">
          <span>
            <strong>方案已更新到 v{lastChange.version}</strong> · 客户选了“{lastChange.answer}”
          </span>
          {lastChange.changes.length === 0 ? (
            <span>方案内容不变，记录了客户的确认。</span>
          ) : (
            lastChange.changes.map((c) => (
              <span key={c.field}>
                【{c.field}】<del>{c.before}</del> → {c.after}
              </span>
            ))
          )}
          {lastChange.todo && <span>新增待办：{lastChange.todo}</span>}
        </div>
      )}

      {current ? (
        <article className="v2-question" aria-label={`问题 ${position}`}>
          <div className="v2-question-tags">
            <span className="v2-pill">
              第 {position} 题 · 关于【{current.field}】
            </span>
            <StrengthTag strength={session.utterances.find((u) => u.id === current.utterance_ids[0])?.strength} />
          </div>
          <h3>{current.title}</h3>
          <p className="v2-said">
            {current.context || (
              <>
                客户原话：<b>“{quoteOf(session, current)}”</b>
              </>
            )}
          </p>
          <p className="v2-why">为什么要问：{current.why}</p>
          <div className="v2-options">
            {current.options.map((option) => (
              <button
                key={option.id}
                type="button"
                className="v2-option"
                aria-pressed={current.answer === option.id}
                disabled={answeringId !== null}
                onClick={() => onAnswer(current, option.id)}
              >
                <span className="v2-option-label">{option.label}</span>
                {option.detail && <span className="v2-option-detail">{option.detail}</span>}
                {option.todo && <span className="v2-option-todo">选这个会多一项待办：{option.todo}</span>}
              </button>
            ))}
          </div>
          {answeringId === current.id && <p className="v2-why">正在记录客户的选择…</p>}
          {reopenId && current.answer && (
            <button type="button" className="v2-toggle" onClick={() => setReopenId(null)}>
              不改了，回到下一题
            </button>
          )}
        </article>
      ) : (
        <div className="v2-done-banner">
          <Mascot pose="happy" size={72} decorative />
          <div>
            <h3>{questions.length ? "客户的问题都确认完了" : "这次没有需要问客户的问题"}</h3>
            <p>方案已按客户的选择更新，接下来看看完整方案。</p>
          </div>
          <button type="button" className="v2-btn-primary" onClick={onGoPlan}>
            查看方案
          </button>
        </div>
      )}

      <RestatementCard session={session} onSessionUpdated={onSessionUpdated} onSelect={onSelect} />

      {answered.length > 0 && (
        <section className="v2-card" aria-label="已确认">
          <div className="v2-card-title">
            <h3>
              已确认 <span className="v2-count">{answered.length}</span>
            </h3>
          </div>
          <ul className="v2-answered">
            {answered.map((q) => {
              const option = q.options.find((o) => o.id === q.answer);
              return (
                <li key={q.id} className="v2-answered-row">
                  <span className="v2-check" aria-hidden="true">
                    ✓
                  </span>
                  <span>
                    {q.title} <strong>{option?.label ?? "已确认"}</strong>
                    {option?.todo && <small>待办：{option.todo}</small>}
                  </span>
                  <button type="button" className="v2-link" onClick={() => setReopenId(q.id)}>
                    改选
                  </button>
                </li>
              );
            })}
          </ul>
        </section>
      )}

      {session.designer_items.length > 0 && (
        <section className="v2-card" aria-label="交给设计师">
          <div className="v2-card-title">
            <h3>
              不用问客户，交给设计师 <span className="v2-count">{session.designer_items.length}</span>
            </h3>
          </div>
          <ul className="v2-designer">
            {session.designer_items.map((d) => (
              <li key={d.id}>
                <span className="v2-field">{d.field}</span>
                <span>{d.text}</span>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}
