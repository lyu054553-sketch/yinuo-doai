import { useEffect, useState } from "react";
import type { ReuseVerdict, Session, SimilarCase } from "../../api/types";
import { getSimilarCases, isMockMode, reuseCaseField } from "../../api/client";
import { readableError } from "../../api/errors";

interface Props {
  session: Session;
  onSessionUpdated: (session: Session) => void;
}

const TONE: Record<ReuseVerdict, string> = {
  和本单一样: "same",
  可直接用: "ok",
  要按本单改: "adapt",
  不能照搬: "no",
  本单没提: "ref",
  旧单没要求: "none",
};

const REUSABLE: ReuseVerdict[] = ["可直接用", "要按本单改", "本单没提"];

/** 相似旧单：按客户原话找最像的旧单，逐栏用本单原话核对“能不能用”，点“引用”写进本单（升版、留痕）。 */
export default function SimilarCases({ session, onSessionUpdated }: Props) {
  const version = session.spec_versions[session.spec_versions.length - 1].version;
  const [cases, setCases] = useState<SimilarCase[] | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (isMockMode) return;
    let alive = true;
    getSimilarCases(session.id)
      .then((data) => {
        if (!alive) return;
        setCases(data);
        setOpen((prev) => prev ?? data[0]?.case_id ?? null);
      })
      .catch((err) => alive && setError(readableError(err)));
    return () => {
      alive = false;
    };
  }, [session.id, version]);

  if (isMockMode || (cases && cases.length === 0 && !error)) return null;

  async function reuse(caseId: string, field: string) {
    setBusy(`${caseId}-${field}`);
    setError(null);
    try {
      onSessionUpdated(await reuseCaseField(session.id, caseId, field));
    } catch (err) {
      setError(readableError(err));
    } finally {
      setBusy(null);
    }
  }

  return (
    <section className="v2-card v5-similar" aria-label="相似旧单">
      <div className="v2-card-title">
        <h3>
          相似旧单 {cases && <span className="v2-count">{cases.length}</span>}
        </h3>
        <span className="v5-hint">用本单客户的原话逐栏核对旧单做法，能用的一键引用</span>
      </div>
      {!cases && !error && <p className="v5-hint">正在找相似旧单…</p>}
      {cases && (
        <div className="v5-similar-tabs" role="tablist" aria-label="相似旧单">
          {cases.map((c) => (
            <button
              key={c.case_id}
              type="button"
              role="tab"
              aria-selected={open === c.case_id}
              onClick={() => setOpen(c.case_id)}
            >
              <b>{c.label}</b>
              <span>{c.title.replace(/^案例.：/, "")}</span>
              <small>可直接用 {c.reusable} 栏</small>
            </button>
          ))}
        </div>
      )}
      {cases
        ?.filter((c) => c.case_id === open)
        .map((c) => (
          <div key={c.case_id} className="v5-similar-body">
            <p className="v5-similar-why">
              和本单都提到：
              {c.shared.map((word) => (
                <span key={word} className="v2-pill">
                  {word}
                </span>
              ))}
            </p>
            <ul className="v5-similar-rows">
              {c.fields.map((f) => {
                const key = `${c.case_id}-${f.field}`;
                const canReuse = REUSABLE.includes(f.verdict);
                return (
                  <li key={f.field} data-tone={TONE[f.verdict]}>
                    <span className="v5-similar-field">{f.field}</span>
                    <div className="v5-similar-main">
                      <span className="v5-similar-value">{f.value || "（空）"}</span>
                      <small>{f.reason}</small>
                    </div>
                    <span className="v5-verdict" data-tone={TONE[f.verdict]}>
                      {f.verdict}
                    </span>
                    {canReuse ? (
                      <button
                        type="button"
                        className="v2-btn-secondary v5-reuse"
                        disabled={busy !== null}
                        onClick={() => reuse(c.case_id, f.field)}
                      >
                        {busy === key ? "引用中…" : "引用"}
                      </button>
                    ) : (
                      <span className="v5-reuse-placeholder" />
                    )}
                  </li>
                );
              })}
            </ul>
            <p className="v5-hint">引用后方案升一版并记入修改记录；相关原话交给设计师复核，不算客户已确认。</p>
          </div>
        ))}
      {error && (
        <p className="v3-ref-error" role="alert">
          {error}
        </p>
      )}
    </section>
  );
}
