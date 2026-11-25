import { useCallback, useEffect, useState } from "react";
import type { Question, ResourceCatalog, ResourceLibrary, Session } from "../api/types";
import { FIELDS } from "../api/types";
import { answerQuestion, getResourceCatalog, getResources, getSession } from "../api/client";
import { readableError } from "../api/errors";
import TopBar, { ModeBadge } from "../components/TopBar";
import OriginalWords from "../components/wb/OriginalWords";
import ReviewStep from "../components/wb/ReviewStep";
import ConfirmStep, { type ChangeNote } from "../components/wb/ConfirmStep";
import PlanStep from "../components/wb/PlanStep";
import HandoffStep from "../components/wb/HandoffStep";
import PlanPeek from "../components/wb/PlanPeek";

export type StepKey = "review" | "confirm" | "plan" | "handoff";

export const STEPS: { key: StepKey; label: string }[] = [
  { key: "review", label: "核对需求" },
  { key: "confirm", label: "和客户确认" },
  { key: "plan", label: "方案" },
  { key: "handoff", label: "交接" },
];

interface Props {
  sessionId: string;
  step: StepKey;
  onStep: (step: StepKey) => void;
  onHome: () => void;
}

export default function WorkbenchPage({ sessionId, step, onStep, onHome }: Props) {
  const [session, setSession] = useState<Session | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [answeringId, setAnsweringId] = useState<string | null>(null);
  const [lastChange, setLastChange] = useState<ChangeNote | null>(null);
  const [focusQuestionId, setFocusQuestionId] = useState<string | null>(null);
  const [visited, setVisited] = useState<Set<StepKey>>(new Set([step]));
  const [resources, setResources] = useState<ResourceLibrary | null>(null);
  const [catalog, setCatalog] = useState<ResourceCatalog | null>(null);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setError(null);
    getSession(sessionId)
      .then((data) => alive && setSession(data))
      .catch((err) => alive && setError(readableError(err)))
      .finally(() => alive && setLoading(false));
    getResources()
      .then((d) => alive && setResources(d))
      .catch(() => alive && setResources(null));
    getResourceCatalog()
      .then((d) => alive && setCatalog(d))
      .catch(() => alive && setCatalog(null));
    return () => {
      alive = false;
    };
  }, [sessionId]);

  useEffect(() => {
    setVisited((prev) => (prev.has(step) ? prev : new Set(prev).add(step)));
    window.scrollTo({ top: 0 });
    if (step !== "confirm") setFocusQuestionId(null);
  }, [step]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setActiveId(null);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const handleUpdated = useCallback((next: Session) => setSession(next), []);

  async function handleAnswer(question: Question, optionId: string) {
    if (!session) return;
    const before = session.spec_versions[session.spec_versions.length - 1];
    setAnsweringId(question.id);
    setError(null);
    try {
      const updated = await answerQuestion(session.id, { question_id: question.id, option_id: optionId });
      const after = updated.spec_versions[updated.spec_versions.length - 1];
      const option = question.options.find((o) => o.id === optionId);
      setLastChange({
        version: after.version,
        question: question.title,
        answer: option?.label ?? "",
        todo: option?.todo ?? null,
        changes: FIELDS.filter((f) => before.fields[f]?.value !== after.fields[f]?.value).map((f) => ({
          field: f,
          before: before.fields[f]?.value ?? "",
          after: after.fields[f]?.value ?? "",
        })),
      });
      setSession(updated);
    } catch (err) {
      setError(readableError(err));
    } finally {
      setAnsweringId(null);
    }
  }

  // 换一步时清掉上一步的选中句，免得右侧还高亮着不相关的栏位。
  function goStep(next: StepKey) {
    setActiveId(null);
    onStep(next);
  }

  function goAsk(questionId: string) {
    setFocusQuestionId(questionId);
    goStep("confirm");
  }

  if (!session) {
    return (
      <div className="v2-wb">
        <TopBar right={<button type="button" className="v2-link" onClick={onHome}>返回新建</button>} />
        <main className="v2-wb-body">
          {loading ? (
            <p className="v2-empty">正在打开洽谈…</p>
          ) : (
            <div className="v2-alert" role="alert">
              {error ?? "打不开这场洽谈。"}
            </div>
          )}
        </main>
      </div>
    );
  }

  const version = session.spec_versions[session.spec_versions.length - 1].version;
  const pending = session.questions.filter((q) => !q.answer).length;
  const done: Record<StepKey, boolean> = {
    review: visited.has("confirm") || visited.has("plan") || visited.has("handoff"),
    confirm: session.questions.length > 0 && pending === 0,
    plan: visited.has("handoff"),
    handoff: false,
  };

  return (
    <div className="v2-wb">
      <TopBar
        center={
          <>
            <div className="v2-session-title">
              <h1 title={session.title}>{session.title}</h1>
              <p>
                {session.input.existing_spec ? "核对已有方案" : "从客户原话生成方案"}
                {session.source === "mock" ? " · 示例数据" : ""}
              </p>
            </div>
            <nav aria-label="洽谈步骤">
              <ol className="v2-stepper">
                {STEPS.map((s, i) => (
                  <li key={s.key} style={{ display: "flex", alignItems: "center", gap: 4 }}>
                    {i > 0 && <span className="v2-stepper-sep" aria-hidden="true" />}
                    <button
                      type="button"
                      className="v2-step"
                      aria-current={step === s.key ? "step" : undefined}
                      data-done={done[s.key] && step !== s.key}
                      onClick={() => goStep(s.key)}
                    >
                      <span className="v2-step-num">{done[s.key] && step !== s.key ? "✓" : i + 1}</span>
                      <span className="v2-step-label">{s.label}</span>
                      {s.key === "confirm" && pending > 0 && <span className="v2-step-badge">{pending}</span>}
                    </button>
                  </li>
                ))}
              </ol>
            </nav>
          </>
        }
        right={
          <>
            <ModeBadge />
            <span className="v2-pill v2-pill-strong" title="当前方案版本">
              方案 v{version}
            </span>
          </>
        }
      />

      <main className="v2-wb-body" data-peek={step === "review" || step === "confirm"}>
        <OriginalWords
          session={session}
          activeId={activeId}
          onSelect={setActiveId}
          onSessionUpdated={handleUpdated}
          onAsk={goAsk}
        />
        <div className="v2-stage">
          {error && (
            <div className="v2-alert" role="alert">
              <span>{error}</span>
              <button type="button" className="v2-link" onClick={() => setError(null)}>
                知道了
              </button>
            </div>
          )}
          {step === "review" && (
            <ReviewStep
              session={session}
              activeId={activeId}
              onSelect={setActiveId}
              onGoConfirm={() => goStep("confirm")}
              onGoPlan={() => goStep("plan")}
            />
          )}
          {step === "confirm" && (
            <ConfirmStep
              session={session}
              focusQuestionId={focusQuestionId}
              answeringId={answeringId}
              lastChange={lastChange}
              onAnswer={handleAnswer}
              onSelect={setActiveId}
              onSessionUpdated={handleUpdated}
              onGoPlan={() => goStep("plan")}
            />
          )}
          {step === "plan" && (
            <PlanStep
              session={session}
              resources={resources}
              catalog={catalog}
              activeId={activeId}
              onSessionUpdated={handleUpdated}
              onGoHandoff={() => goStep("handoff")}
            />
          )}
          {step === "handoff" && <HandoffStep session={session} catalog={catalog} />}
        </div>
        {(step === "review" || step === "confirm") && (
          <PlanPeek session={session} resources={resources} activeId={activeId} onOpenPlan={() => goStep("plan")} />
        )}
      </main>
    </div>
  );
}
