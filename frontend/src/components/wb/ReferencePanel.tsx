import { useEffect, useRef, useState } from "react";
import type { Session } from "../../api/types";
import { getHealth, isMockMode, uploadReference } from "../../api/client";
import { isApiError, readableError } from "../../api/errors";
import Mascot from "../Mascot";

interface Props {
  session: Session;
  onSelect: (id: string | null) => void;
  onSessionUpdated: (session: Session) => void;
}

const MAX_BYTES = 20 * 1024 * 1024;
const ACCEPT = ["image/jpeg", "image/png", "image/webp"];

function refValue(value: string | boolean): string {
  if (typeof value === "boolean") return value ? "看得到" : "看不到";
  return value;
}

/** 看图服务的报错带启动命令，换成业务员能看懂的话。 */
function visionError(err: unknown): string {
  if (isApiError(err)) {
    if (err.status === 503) return "看图服务这会儿没开，文字核对不受影响。请负责演示的同事启动看图服务后再传。";
    if (err.status === 504) return "这张图看了太久没看完。换一张更清楚、只拍衣服正面的图再试。";
    if (err.status === 400 || err.status === 413) return err.message || "这张图打不开，请换一张 JPG / PNG 图片。";
    if (err.status === 502) return "看图服务这次给的结果不完整，请再传一次。";
  }
  return readableError(err);
}

/**
 * 参考图：客户带来的照片 → 本机视觉模型看领型、门襟、纽扣、花纹 → 和客户原话比对。
 * 只看得准的几项才采用，而且最多算“疑似”；衣长、廓形、颜色一律交给人看。
 */
export default function ReferencePanel({ session, onSelect, onSessionUpdated }: Props) {
  // null = 还在问后端；看图服务没开时直接说明，不让人传了再失败。
  const [visionOk, setVisionOk] = useState<boolean | null>(isMockMode ? false : null);
  const [uploading, setUploading] = useState(false);
  const [preview, setPreview] = useState<string | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [dragOver, setDragOver] = useState(false);
  const [replacing, setReplacing] = useState(false);
  const fileRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    if (isMockMode) return;
    let alive = true;
    getHealth()
      .then((h) => alive && setVisionOk(Boolean(h.vision_ok)))
      .catch(() => alive && setVisionOk(false));
    return () => {
      alive = false;
    };
  }, []);

  useEffect(() => {
    if (!uploading) return;
    const started = Date.now();
    setElapsed(0);
    const t = window.setInterval(() => setElapsed(Math.floor((Date.now() - started) / 1000)), 1000);
    return () => window.clearInterval(t);
  }, [uploading]);

  useEffect(() => () => {
    if (preview) URL.revokeObjectURL(preview);
  }, [preview]);

  async function handleFile(file: File | undefined) {
    if (!file || uploading) return;
    if (!ACCEPT.includes(file.type)) return setError("只支持 JPG、PNG、WebP 图片。");
    if (file.size > MAX_BYTES) return setError("图片超过 20MB，请压缩后再传。");
    setError(null);
    setPreview(URL.createObjectURL(file));
    setUploading(true);
    try {
      onSessionUpdated(await uploadReference(session.id, file));
      setReplacing(false);
      setPreview(null);
    } catch (err) {
      setError(visionError(err));
    } finally {
      setUploading(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  }

  const ref = session.reference;
  const used = ref ? Object.entries(ref.fields).filter(([, f]) => f.used) : [];
  const manual = ref ? Object.entries(ref.fields).filter(([, f]) => !f.used).map(([k]) => k) : [];
  const showUploader = !ref || replacing;

  const picker = (
    <input
      ref={fileRef}
      type="file"
      accept={ACCEPT.join(",")}
      className="visually-hidden"
      onChange={(e) => handleFile(e.target.files?.[0])}
    />
  );

  return (
    <div className="v2-ref">
      <div className="v2-section-title">
        <h2>参考图</h2>
        {ref && !replacing && !uploading && visionOk && (
          <button type="button" className="v2-link v3-ref-replace" onClick={() => setReplacing(true)}>
            换一张
          </button>
        )}
      </div>

      {uploading && preview && (
        <div className="v3-ref-busy" aria-live="polite">
          <img className="v2-ref-img" src={preview} alt="正在识别的参考图" />
          <div className="v3-ref-busy-note">
            <Mascot pose="thinking" size={48} decorative />
            <div>
              <b>正在看图… {elapsed} 秒</b>
              <span>本机视觉模型逐项看领型、门襟、纽扣、花纹。第一次要加载模型，约 1 分钟；之后通常十几秒。可以先接着核对文字。</span>
            </div>
          </div>
        </div>
      )}

      {!uploading && ref && !replacing && (
        <>
          <a href={ref.image_url} target="_blank" rel="noreferrer" title="打开原图">
            <img className="v2-ref-img" src={ref.image_url} alt="客户提供的参考图" />
          </a>
          {ref.conflicts.length > 0 ? (
            ref.conflicts.map((c, i) => (
              <button
                key={i}
                type="button"
                className="v2-ref-conflict"
                onClick={() => c.utterance_ids[0] && onSelect(c.utterance_ids[0])}
              >
                <b>图和话对不上 · {c.field}</b>
                <span>{c.message}</span>
                <small>点一下看是哪句原话 →</small>
              </button>
            ))
          ) : (
            <p className="v3-ref-ok">✓ 看得出的几项和客户原话没有冲突。</p>
          )}
          <ul className="v2-ref-fields" aria-label="图里看得出的">
            {used.map(([key, item]) => (
              <li key={key}>
                <span className="k">{key}</span>
                <span>{refValue(item.value)}</span>
                <span className="c" data-conf={item.confidence}>
                  {item.confidence}
                </span>
              </li>
            ))}
          </ul>
          {manual.length > 0 && (
            <p className="v3-ref-manual">
              <b>请人看：</b>
              {manual.join("、")}。照片角度和光线会影响判断，模型在这几项上不可靠，不采用。
            </p>
          )}
          <p className="v3-ref-model">本机识别，结果最高只算“疑似”；已记进修改记录。</p>
        </>
      )}

      {!uploading && showUploader && (
        <>
          {visionOk === false ? (
            <p className="v3-ref-off">
              {isMockMode ? "示例模式不提供看图。" : "看图服务本次未启用，文字核对不受影响。"}
            </p>
          ) : (
            <div
              className="v3-ref-drop"
              data-over={dragOver}
              onDragOver={(e) => {
                e.preventDefault();
                setDragOver(true);
              }}
              onDragLeave={() => setDragOver(false)}
              onDrop={(e) => {
                e.preventDefault();
                setDragOver(false);
                handleFile(e.dataTransfer.files?.[0]);
              }}
            >
              {picker}
              <span>
                客户带了参考图？拖到这里或点按钮上传，衣诺会看领型、门襟、纽扣、花纹，再和客户的话比对。
              </span>
              <div className="v3-ref-actions">
                <button
                  type="button"
                  className="v2-btn-secondary"
                  disabled={visionOk === null}
                  onClick={() => fileRef.current?.click()}
                >
                  {visionOk === null ? "检查看图服务…" : "选择图片"}
                </button>
                {replacing && (
                  <button type="button" className="v2-link" onClick={() => setReplacing(false)}>
                    取消
                  </button>
                )}
              </div>
            </div>
          )}
        </>
      )}

      {error && (
        <p className="v3-ref-error" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}
