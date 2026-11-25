import { useState } from "react";
import type { ResourceCatalog, Session } from "../../api/types";
import { readableError } from "../../api/errors";
import { buildProjectPackage, downloadText } from "../../utils/package";
import HandoffTab from "../HandoffTab";
import LedgerTab from "../LedgerTab";

type View = "handoff" | "ledger";

/** 第四步：交接单为主，修改记录（台账）放在旁边一个切换里。 */
export default function HandoffStep({ session, catalog }: { session: Session; catalog: ResourceCatalog | null }) {
  const [view, setView] = useState<View>("handoff");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const version = session.spec_versions[session.spec_versions.length - 1].version;

  async function exportPackage() {
    setBusy(true);
    setError(null);
    try {
      const markdown = await buildProjectPackage(session, catalog);
      downloadText(`衣诺项目交付包-${session.id}-v${version}.md`, markdown);
    } catch (err) {
      setError(readableError(err));
    } finally {
      setBusy(false);
    }
  }
  const designer = session.designer_items.length;
  const todos = session.questions
    .map((q) => q.options.find((o) => o.id === q.answer)?.todo)
    .filter(Boolean).length;

  return (
    <div className="v2-stage">
      <header className="v2-stage-head">
        <div>
          <h2>交接给设计师</h2>
          <p>
            包含当前方案、客户确认的结果、{designer} 件交给设计师判断的事
            {todos > 0 ? `、${todos} 项待办` : ""}，以及打样时要核对的点。
          </p>
        </div>
        <div className="v2-seg-control" role="tablist" aria-label="交接视图">
          <button type="button" role="tab" aria-selected={view === "handoff"} onClick={() => setView("handoff")}>
            交接单
          </button>
          <button type="button" role="tab" aria-selected={view === "ledger"} onClick={() => setView("ledger")}>
            修改记录 · {session.ledger.length}
          </button>
        </div>
      </header>
      <section className="v4-package" aria-label="项目交付包">
        <div className="v4-package-head">
          <div>
            <h3>项目交付包</h3>
            <p>一个文件交齐这单的全部成果，交接单是其中的目录和出口。</p>
          </div>
          <button type="button" className="v2-btn-primary" disabled={busy} onClick={exportPackage}>
            {busy ? "正在整理…" : "导出项目交付包"}
          </button>
        </div>
        <ol className="v4-package-list">
          <li><b>需求确认单</b><span>业务员、客户 · 逐句原话、确认结果、需求报告</span></li>
          <li><b>方案初稿</b><span>设计师 · 八栏方案、面料三条依据、资源适配、可行性</span></li>
          <li><b>视觉需求包</b><span>设计师 · 三类生图提示词和出图核对项</span></li>
          <li><b>交接单</b><span>设计师、制版师、打样 · 待判断、待办、打样核对、领料</span></li>
          <li><b>修改记录</b><span>业务员、管理者 · 全程留痕，可归档复盘</span></li>
        </ol>
        {error && <p className="v3-ref-error" role="alert">{error}</p>}
      </section>
      <div className="v2-embed">
        {view === "handoff" ? <HandoffTab session={session} /> : <LedgerTab session={session} catalog={catalog} />}
      </div>
    </div>
  );
}
