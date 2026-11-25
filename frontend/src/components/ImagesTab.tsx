import { useEffect, useState } from "react";
import type { ImageKind, PromptsResult, Session } from "../api/types";
import { generateImage, getHealth, getPrompts, uploadCheckImage } from "../api/client";
import { readableError, isApiError } from "../api/errors";
import { imageProviderLabel } from "../utils/display";
import Mascot from "./Mascot";

const KINDS: { kind: ImageKind; hint: string }[] = [
  { kind: "成衣挂拍", hint: "整件衣服正面，看廓形、领型、门襟" },
  { kind: "面料细节", hint: "近景看面料、纹样和光泽" },
  { kind: "上身效果", hint: "穿在人身上的氛围和比例" },
];

interface Props {
  session: Session;
  onSessionUpdated: (session: Session) => void;
}

export default function ImagesTab({ session, onSessionUpdated }: Props) {
  const [prompts, setPrompts] = useState<PromptsResult | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [working, setWorking] = useState<ImageKind | null>(null);
  const [copied, setCopied] = useState<string | null>(null);
  // 付费生图关着时（image_provider 为 none）不放“出图”按钮，免得点了只得到一条提示词。
  const [canGenerate, setCanGenerate] = useState(false);
  const [queue, setQueue] = useState<ImageKind[]>([]);
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    if (!working) return;
    const started = Date.now();
    setElapsed(0);
    const t = window.setInterval(() => setElapsed(Math.floor((Date.now() - started) / 1000)), 1000);
    return () => window.clearInterval(t);
  }, [working]);

  useEffect(() => {
    getHealth()
      .then((h) => setCanGenerate(Boolean(h.image_provider) && h.image_provider !== "none"))
      .catch(() => setCanGenerate(false));
  }, []);

  useEffect(() => {
    let alive = true;
    setPrompts(null);
    setNotice(null);
    getPrompts(session.id)
      .then((result) => {
        if (alive) setPrompts(result);
      })
      .catch((err) => {
        if (alive) {
          setNotice(isApiError(err) && err.status === 501 ? "这个演示环境不提供效果图提示词和图片核对。请用真实模式，或查看已验收案例。" : readableError(err));
        }
      });
    return () => {
      alive = false;
    };
  }, [session.id, session.spec_versions.length]);

  async function copy(text: string, key: string) {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(key);
      setTimeout(() => setCopied(null), 1500);
    } catch {
      setNotice("复制没有成功，请手动选中文字复制。");
    }
  }

  async function run(kind: ImageKind) {
    setWorking(kind);
    setNotice(null);
    try {
      const updated = await generateImage(session.id, kind);
      onSessionUpdated(updated);
    } catch (err) {
      setNotice(isApiError(err) && err.status === 501 ? "这个演示环境不提供效果图提示词和图片核对。请用真实模式，或查看已验收案例。" : readableError(err));
    } finally {
      setWorking(null);
    }
  }

  /** 三类图依次出（生图接口有并发限制，逐张更稳），出一张显示一张。 */
  async function runAll() {
    const kinds = KINDS.map((k) => k.kind);
    setQueue(kinds);
    for (const kind of kinds) {
      await run(kind);
      setQueue((q) => q.filter((k) => k !== kind));
    }
    setQueue([]);
  }

  async function uploadForCheck(kind: ImageKind, file: File | undefined) {
    if (!file) return;
    setWorking(kind);
    setNotice(null);
    try {
      const updated = await uploadCheckImage(session.id, kind, file);
      onSessionUpdated(updated);
    } catch (err) {
      setNotice(isApiError(err) && err.status === 501 ? "这个演示环境不提供效果图提示词和图片核对。请用真实模式，或查看已验收案例。" : readableError(err));
    } finally {
      setWorking(null);
    }
  }

  if (!prompts) {
    return (
      <div className="tab-panel" role="tabpanel">
        <div className="placeholder-card">
          <p className="placeholder-title">{notice ?? "正在取提示词…"}</p>
          <p className="hint-line">
            效果图提示词按当前方案生成；外部生成的效果图可以传回来，逐项核对和方案是否一致。
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="tab-panel" role="tabpanel">
      {notice && <div className="alert alert-info">{notice}</div>}
      {!canGenerate && (
        <p className="hint-line">
          付费生图当前关闭：衣诺按当前方案输出三类提示词，复制到外部生图工具使用；生成的图可以传回来核对。
        </p>
      )}
      {canGenerate && (
        <section className="v5-gallery" aria-label="效果图">
          <div className="v5-gallery-head">
            <div>
              <h3>效果图 · 按方案 v{prompts.version}</h3>
              <p>
                衣诺按当前方案直接出图，出完用本机视觉模型逐项核对领型、门襟、纽扣、纹样，对不上会自动重画一次。每张约 40 秒到 2 分钟。
              </p>
            </div>
            <button type="button" className="v2-btn-primary" disabled={working !== null} onClick={runAll}>
              {working ? `正在出「${working}」… ${elapsed} 秒` : "一键出三张效果图"}
            </button>
          </div>
          <ul className="v5-gallery-grid">
            {KINDS.map(({ kind, hint }) => {
              const latest = [...session.images].reverse().find((img) => img.kind === kind && img.url);
              const fresh = latest && latest.version === prompts.version;
              const busy = working === kind;
              const waiting = !busy && queue.includes(kind);
              return (
                <li key={kind} className="v5-gallery-card" data-busy={busy}>
                  <header>
                    <b>{kind}</b>
                    <small>{hint}</small>
                  </header>
                  <div className="v5-gallery-frame">
                    {busy ? (
                      <div className="v5-gallery-wait">
                        <Mascot pose="thinking" size={64} decorative />
                        <span>正在画… {elapsed} 秒</span>
                        <small>画完会自动核对，可以先去做别的</small>
                      </div>
                    ) : latest?.url ? (
                      <a href={latest.url} target="_blank" rel="noreferrer" title="打开大图">
                        <img src={latest.url} alt={`${kind}效果图`} />
                      </a>
                    ) : (
                      <div className="v5-gallery-wait">
                        <span>{waiting ? "排队中…" : "还没出图"}</span>
                      </div>
                    )}
                  </div>
                  {latest?.check && !busy && (
                    <ul className="v5-gallery-check" aria-label="自动核对">
                      {latest.check.items
                        .filter((item) => item.verdict !== "看不出")
                        .map((item, index) => (
                          <li key={`${item.field}-${index}`} data-verdict={item.verdict} title={`期望：${item.expect}；图里看到：${item.observed}`}>
                            <span>{item.field}</span>
                            <b>{item.verdict}</b>
                          </li>
                        ))}
                      {latest.check.items.some((item) => item.verdict === "看不出") && (
                        <li data-verdict="看不出">
                          <span>
                            {latest.check.items.filter((item) => item.verdict === "看不出").length} 项需人工看
                          </span>
                        </li>
                      )}
                    </ul>
                  )}
                  <div className="v5-gallery-actions">
                    {latest && !fresh && !busy && <small className="v5-stale">这张按 v{latest.version} 画的，方案已更新</small>}
                    <button
                      type="button"
                      className="v2-btn-secondary"
                      disabled={working !== null}
                      onClick={() => run(kind)}
                    >
                      {latest ? "按当前方案重画" : "出这一张"}
                    </button>
                  </div>
                </li>
              );
            })}
          </ul>
          <p className="hint-line">
            效果图是沟通参考，不是成衣承诺；视觉核对只给疑似线索，衣长、廓形、颜色请在实物和样衣上确认。
          </p>
        </section>
      )}

      <details className="v5-prompts" open={!canGenerate}>
        <summary>出图提示词 · 依据 v{prompts.version}{canGenerate ? "（也可复制到外部工具用）" : ""}</summary>
      <ul className="prompt-list">
        {prompts.items.map((item, index) => (
          <li key={`${item.kind}-${index}`} className="prompt-item">
            <div className="prompt-head">
              <strong>{item.kind}</strong>
              <button
                type="button"
                className="btn btn-quiet btn-sm"
                onClick={() => copy(item.text, `${item.kind}-${index}`)}
              >
                {copied === `${item.kind}-${index}` ? "已复制" : "复制"}
              </button>
            </div>
            <p className="prompt-text">{item.text}</p>
            <div className="prompt-actions">
              {canGenerate && (
                <button
                  type="button"
                  className="btn btn-ghost btn-sm"
                  onClick={() => run(item.kind)}
                  disabled={working !== null}
                >
                  {working === item.kind ? "正在出图…" : "按当前方案出图"}
                </button>
              )}
              <label className="upload-inline">
                上传外部效果图核对
                <input
                  type="file"
                  accept="image/*"
                  className="visually-hidden"
                  onChange={(e) => uploadForCheck(item.kind, e.target.files?.[0])}
                />
              </label>
            </div>
          </li>
        ))}
      </ul>

      </details>

      <p className="section-title">出图后要核对</p>
      <ul className="check-list">
        {prompts.checks.map((check, index) => (
          <li key={`${check.field}-${index}`}>
            <strong>{check.field}</strong>：{check.expect}
          </li>
        ))}
      </ul>
      <p className="hint-line">
        提示：视觉只提供疑似线索，“符合”也要人看一眼，“不符合”需人工复核；衣长、廓形、颜色请在实物和样衣上人工确认。
      </p>

      {session.images.length > 0 && (
        <>
          <p className="section-title">已生成的效果图</p>
          <ul className="image-list">
            {session.images.map((image) => (
              <li key={image.id} className="image-card">
                <p className="image-meta">
                  {image.kind} · 依据 v{image.version} · {imageProviderLabel(image.provider)}
                </p>
                {image.url ? (
                  <img className="generated-img" src={image.url} alt={`${image.kind}效果图`} />
                ) : (
                  <p className="hint-line">这一张只生成了提示词，没有出图。</p>
                )}
                {image.check && (
                  <>
                    <ul className="check-result">
                      {image.check.items.map((item, index) => (
                        <li key={`${image.id}-${item.field}-${index}`}>
                          <span className="check-field">{item.field}</span>
                          <span className={`check-verdict verdict-${item.verdict}`}>{item.verdict}</span>
                          <span className="check-confidence">把握程度：{item.confidence}</span>
                          <span className="hint-line">
                            期望：{item.expect}；图里看到：{item.observed}
                          </span>
                        </li>
                      ))}
                    </ul>
                    <p className="hint-line">
                      提示：视觉只提供疑似线索，“符合”也要人看一眼，“不符合”需人工复核；衣长、廓形、颜色请人工确认。
                    </p>
                  </>
                )}
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}