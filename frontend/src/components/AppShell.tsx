import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import type { SessionSummary } from "../api/types";
import { getSessionList } from "../api/client";
import { formatTime } from "../utils/display";
import Mascot from "./Mascot";

export type ShellPlace = "home" | "resources" | "session";

interface Props {
  place: ShellPlace;
  sessionId?: string;
  onHome: () => void;
  onResources: () => void;
  onOpen: (id: string) => void;
  children: ReactNode;
}

function IconPlus() {
  return (
    <svg viewBox="0 0 20 20" width="18" height="18" aria-hidden="true">
      <path d="M10 4v12M4 10h12" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" fill="none" />
    </svg>
  );
}
function IconBook() {
  return (
    <svg viewBox="0 0 20 20" width="18" height="18" aria-hidden="true">
      <path
        d="M4 4.5A1.5 1.5 0 0 1 5.5 3H16v12H5.5A1.5 1.5 0 0 0 4 16.5zM4 16.5A1.5 1.5 0 0 0 5.5 18H16M7 7h6"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinejoin="round"
        strokeLinecap="round"
        fill="none"
      />
    </svg>
  );
}
function IconChat() {
  return (
    <svg viewBox="0 0 20 20" width="16" height="16" aria-hidden="true">
      <path
        d="M4 5.5A1.5 1.5 0 0 1 5.5 4h9A1.5 1.5 0 0 1 16 5.5v6a1.5 1.5 0 0 1-1.5 1.5H9l-3.5 3v-3h0A1.5 1.5 0 0 1 4 11.5z"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinejoin="round"
        fill="none"
      />
    </svg>
  );
}

/** 工作台外壳：左侧墨色导航栏（品牌、新建、资源库、最近洽谈、吉祥物），右侧是各页内容。 */
export default function AppShell({ place, sessionId, onHome, onResources, onOpen, children }: Props) {
  const [recent, setRecent] = useState<SessionSummary[] | null>(null);

  // 换页时刷新一次列表，新建的洽谈能马上出现在侧栏里。
  useEffect(() => {
    let alive = true;
    getSessionList()
      .then((data) => alive && setRecent(data))
      .catch(() => alive && setRecent([]));
    return () => {
      alive = false;
    };
  }, [place, sessionId]);

  return (
    <div className="v3-shell">
      <aside className="v3-rail" aria-label="主导航">
        <button type="button" className="v3-rail-brand" onClick={onHome} aria-label="衣诺，回到新建洽谈">
          <Mascot pose="avatar" size={40} decorative />
          <span className="v3-rail-brand-text">
            <b>衣诺</b>
            <small>海派定制接单工作台</small>
          </span>
        </button>

        <nav className="v3-rail-nav">
          <button
            type="button"
            className="v3-rail-item"
            aria-current={place === "home" ? "page" : undefined}
            onClick={onHome}
            title="新建洽谈"
          >
            <IconPlus />
            <span>新建洽谈</span>
          </button>
          <button
            type="button"
            className="v3-rail-item"
            aria-current={place === "resources" ? "page" : undefined}
            onClick={onResources}
            title="资源知识库"
          >
            <IconBook />
            <span>资源知识库</span>
          </button>
        </nav>

        <div className="v3-rail-section">
          <h2>最近洽谈</h2>
          {recent === null ? (
            <p className="v3-rail-muted">正在读取…</p>
          ) : recent.length === 0 ? (
            <div className="v3-rail-empty">
              <Mascot pose="empty" size={72} decorative />
              <p>还没有洽谈记录。贴进客户的话，或点一个示例开始。</p>
            </div>
          ) : (
            <ul className="v3-rail-list">
              {recent.slice(0, 12).map((item) => (
                <li key={item.id}>
                  <button
                    type="button"
                    className="v3-rail-session"
                    aria-current={place === "session" && sessionId === item.id ? "page" : undefined}
                    onClick={() => onOpen(item.id)}
                    title={item.title}
                  >
                    <IconChat />
                    <span className="v3-rail-session-text">
                      <span className="v3-rail-session-title">{item.title}</span>
                      <span className="v3-rail-session-meta">
                        {formatTime(item.created_at)} · v{item.version}
                      </span>
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>

        <div className="v3-rail-foot">
          <Mascot pose="main" size={56} decorative />
          <p>拿不准的地方我都会标出来，交给你和客户确认。</p>
        </div>
      </aside>
      <div className="v3-main">{children}</div>
    </div>
  );
}
