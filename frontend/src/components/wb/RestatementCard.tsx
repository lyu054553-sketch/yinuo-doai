import { useState } from "react";
import type { Session } from "../../api/types";
import { answerRestatement, createRestatement, isMockMode } from "../../api/client";
import { readableError } from "../../api/errors";

interface Props {
  session: Session;
  onSessionUpdated: (session: Session) => void;
  onSelect: (id: string | null) => void;
}

/** 复述确认：把方案翻成客户听得懂的话，列出可能听错的几处，请客户逐条点“对 / 不对”。 */
export default function RestatementCard({ session, onSessionUpdated, onSelect }: Props) {
  const restatement = session.restatement ?? null;
  const version = session.spec_versions[session.spec_versions.length - 1].version;
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [wrongId, setWrongId] = useState<string | null>(null);
  const [note, setNote] = useState("");

  if (isMockMode) return null;

  async function run(key: string, fn: () => Promise<Session>) {
    setBusy(key);
    setError(null);
    try {
      onSessionUpdated(await fn());
      setWrongId(null);
      setNote("");
    } catch (err) {
      setError(readableError(err));
    } finally {
      setBusy(null);
    }
  }

  const stale = restatement && restatement.version !== version;
  const done = restatement ? restatement.points.filter((p) => p.answer).length : 0;

  return (
    <section className="v2-card v5-restate" aria-label="复述给客户听">
      <div className="v2-card-title">
        <h3>复述给客户听</h3>
        {restatement && (
          <span className="v5-hint">
            已确认 {done}/{restatement.points.length}
            {stale ? ` · 方案已更新到 v${version}` : ""}
          </span>
        )}
      </div>
      {!restatement || stale ? (
        <div className="v5-restate-empty">
          <p>
            把方案翻成客户听得懂的话念给客户听，再请客户确认“可能听错”的几处。还没裁布，这时候改成本最低。
          </p>
          <button
            type="button"
            className="v2-btn-primary"
            disabled={busy !== null}
            onClick={() => run("create", () => createRestatement(session.id))}
          >
            {busy === "create" ? "正在整理复述，约十几秒…" : stale ? "按新方案重新复述" : "生成复述"}
          </button>
        </div>
      ) : (
        <>
          <blockquote className="v5-restate-text">{restatement.text}</blockquote>
          {restatement.points.length > 0 && <p className="v5-restate-sub">我们可能听错的地方，请客户逐条确认：</p>}
          <ol className="v5-restate-points">
            {restatement.points.map((p) => (
              <li key={p.id} data-answer={p.answer ?? "待确认"}>
                <button type="button" className="v5-restate-q" onClick={() => onSelect(p.utterance_ids[0] ?? null)}>
                  {p.question}
                </button>
                {p.answer ? (
                  <span className="v5-restate-result">
                    客户：{p.answer}
                    {p.note ? `（${p.note}）` : ""}
                  </span>
                ) : wrongId === p.id ? (
                  <div className="v5-restate-note">
                    <input
                      className="v2-input"
                      value={note}
                      placeholder="客户的原意是…（会交给设计师）"
                      onChange={(e) => setNote(e.target.value)}
                      aria-label="客户原意"
                    />
                    <button
                      type="button"
                      className="v2-btn-primary"
                      disabled={busy !== null || !note.trim()}
                      onClick={() => run(p.id, () => answerRestatement(session.id, p.id, "不对", note))}
                    >
                      记下
                    </button>
                    <button type="button" className="v2-link" onClick={() => setWrongId(null)}>
                      取消
                    </button>
                  </div>
                ) : (
                  <div className="v5-restate-actions">
                    <button
                      type="button"
                      className="v2-btn-secondary"
                      disabled={busy !== null}
                      onClick={() => run(p.id, () => answerRestatement(session.id, p.id, "对"))}
                    >
                      对
                    </button>
                    <button type="button" className="v2-btn-secondary" disabled={busy !== null} onClick={() => setWrongId(p.id)}>
                      不对
                    </button>
                  </div>
                )}
              </li>
            ))}
          </ol>
          <p className="v5-hint">
            {restatement.source === "llm" ? "复述由大模型改写成口语，只复述方案已写的内容。" : "复述按方案模板生成。"}
            客户说“不对”的地方会交给设计师，并记入修改记录。
          </p>
        </>
      )}
      {error && (
        <p className="v3-ref-error" role="alert">
          {error}
        </p>
      )}
    </section>
  );
}
