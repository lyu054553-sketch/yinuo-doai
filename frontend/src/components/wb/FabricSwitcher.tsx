import { useEffect, useState } from "react";
import type { FabricOption, Session } from "../../api/types";
import { chooseFabric, getFabricOptions, isMockMode } from "../../api/client";
import { readableError } from "../../api/errors";

interface Props {
  session: Session;
  onSessionUpdated: (session: Session) => void;
}

/** 面料备选：当前面料 + 按本单原话排出的候选，每块带风格/体感/体型三条依据和（模拟）库存，选定后方案升版。 */
export default function FabricSwitcher({ session, onSessionUpdated }: Props) {
  const version = session.spec_versions[session.spec_versions.length - 1].version;
  const [open, setOpen] = useState(false);
  const [options, setOptions] = useState<FabricOption[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    let alive = true;
    setOptions(null);
    getFabricOptions(session.id)
      .then((data) => alive && setOptions(data))
      .catch((err) => alive && setError(readableError(err)));
    return () => {
      alive = false;
    };
  }, [open, session.id, version]);

  if (isMockMode) return null;

  async function choose(id: string) {
    setBusy(id);
    setError(null);
    try {
      onSessionUpdated(await chooseFabric(session.id, id));
    } catch (err) {
      setError(readableError(err));
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="v5-fabric">
      <button type="button" className="v2-link v5-fabric-toggle" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
        {open ? "收起面料备选" : "换一块面料 · 看备选"}
      </button>
      {open && (
        <>
          {!options && !error && <p className="v5-hint">正在按客户原话排候选面料…</p>}
          {options && (
            <ul className="v5-fabric-grid">
              {options.map((o) => (
                <li key={o.id} data-current={o.current}>
                  <header>
                    <code>{o.id}</code>
                    <b>{o.name}</b>
                    {o.simulated && <i className="v4-sim-badge">模拟库</i>}
                    {o.current && <i className="v5-current">当前</i>}
                  </header>
                  <small className="v5-hint">来源：{o.source}</small>
                  <ul className="v4-fabric-reasons">
                    {o.reasons.map((r) => (
                      <li key={r.label} data-empty={!r.matched}>
                        <b>{r.label}</b>
                        <span>{r.text}</span>
                      </li>
                    ))}
                  </ul>
                  <p className="v5-fabric-stock" data-status={o.stock?.status ?? "无"}>
                    {o.stock
                      ? `仓库 · 模拟：${o.stock.sku} ${o.stock.colorway} · ${o.stock.status}${o.stock.stock_m ? ` ${o.stock.stock_m} 米` : ""}${o.stock.lead_days ? ` · 约 ${o.stock.lead_days} 天到货` : ""}`
                      : "仓库 · 模拟：暂无和本方案花色对得上的货"}
                  </p>
                  {!o.current && (
                    <button type="button" className="v2-btn-secondary" disabled={busy !== null} onClick={() => choose(o.id)}>
                      {busy === o.id ? "换料中…" : "换成这块"}
                    </button>
                  )}
                </li>
              ))}
            </ul>
          )}
          {error && (
            <p className="v3-ref-error" role="alert">
              {error}
            </p>
          )}
        </>
      )}
    </div>
  );
}
