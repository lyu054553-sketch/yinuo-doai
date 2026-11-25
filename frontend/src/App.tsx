import { Component, useEffect, useState } from "react";
import type { ReactNode } from "react";
import type { Session } from "./api/types";
import HomePage from "./pages/HomePage";
import WorkbenchPage, { STEPS, type StepKey } from "./pages/WorkbenchPage";
import ResourcesPage from "./pages/ResourcesPage";
import AppShell from "./components/AppShell";

type View = { name: "home" } | { name: "resources" } | { name: "session"; id: string; step: StepKey };

/** 地址栏形如 #/s/<会话id>/<步骤>，刷新后还停在同一场洽谈的同一步。 */
function parseHash(hash: string): View {
  const parts = hash.replace(/^#\/?/, "").split("/").filter(Boolean);
  if (parts[0] === "resources") return { name: "resources" };
  if (parts[0] === "s" && parts[1]) {
    const step = STEPS.some((s) => s.key === parts[2]) ? (parts[2] as StepKey) : "review";
    return { name: "session", id: decodeURIComponent(parts[1]), step };
  }
  return { name: "home" };
}

function toHash(view: View): string {
  if (view.name === "resources") return "#/resources";
  if (view.name === "session") return `#/s/${encodeURIComponent(view.id)}/${view.step}`;
  return "#/";
}

class ErrorBoundary extends Component<{ children: ReactNode }, { hasError: boolean }> {
  constructor(props: { children: ReactNode }) {
    super(props);
    this.state = { hasError: false };
  }

  static getDerivedStateFromError() {
    return { hasError: true };
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="v2-home">
          <main className="v2-home-main">
            <div className="v2-alert" role="alert">
              页面出了点问题，请刷新重试。已经记录的内容不会丢。
            </div>
            <button type="button" className="v2-btn-primary" onClick={() => window.location.reload()}>
              刷新页面
            </button>
          </main>
        </div>
      );
    }
    return this.props.children;
  }
}

export default function App() {
  const [view, setView] = useState<View>(() => parseHash(window.location.hash));

  useEffect(() => {
    const onHash = () => setView(parseHash(window.location.hash));
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  function go(next: View) {
    const hash = toHash(next);
    if (window.location.hash !== hash) window.location.hash = hash;
    setView(next);
  }

  const home = () => go({ name: "home" });

  const resources = () => go({ name: "resources" });
  const open = (id: string) => go({ name: "session", id, step: "review" });

  return (
    <ErrorBoundary>
      <AppShell
        place={view.name}
        sessionId={view.name === "session" ? view.id : undefined}
        onHome={home}
        onResources={resources}
        onOpen={open}
      >
        {view.name === "home" && (
          <HomePage onCreated={(s: Session) => go({ name: "session", id: s.id, step: "review" })} />
        )}
        {view.name === "resources" && <ResourcesPage onBack={home} />}
        {view.name === "session" && (
          <WorkbenchPage
            key={view.id}
            sessionId={view.id}
            step={view.step}
            onStep={(step) => go({ ...view, step })}
            onHome={home}
          />
        )}
      </AppShell>
    </ErrorBoundary>
  );
}
