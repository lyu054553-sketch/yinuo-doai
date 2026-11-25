import { useEffect, useRef, useState } from "react";
import type { Field, Sample, Session } from "../api/types";
import { FIELDS } from "../api/types";
import { createSession, getHealth, getSamples, isMockMode } from "../api/client";
import { readableError } from "../api/errors";
import TopBar, { ModeBadge } from "../components/TopBar";
import Mascot from "../components/Mascot";

interface Props {
  onCreated: (session: Session) => void;
}

const PROGRESS_STEPS = [
  "把客户的话拆成一句一句",
  "逐句对照方案和设计字段",
  "核对：原话能在原文里找到、资源编号在库里",
  "整理出要问客户的问题",
];
/** 真实模式下各步大致开始的秒数（大模型一次调用占大头，前两步最慢）。 */
const STEP_AT = [0, 8, 18, 26];

const EMPTY_SPEC = Object.fromEntries(FIELDS.map((f) => [f, ""])) as Record<Field, string>;

/** 示例案例标题形如“案例二：中式正式外套（窄领、内搭、梅花暗纹）”，按钮上只放编号和括号里的要点。 */
function sampleLabel(title: string): { name: string; hint: string } {
  const [name, rest = ""] = title.split("：");
  const hint = rest.match(/（(.+)）/)?.[1] ?? rest;
  return { name, hint };
}

export default function HomePage({ onCreated }: Props) {
  const [text, setText] = useState("");
  const [spec, setSpec] = useState<Record<Field, string>>(EMPTY_SPEC);
  const [showSpec, setShowSpec] = useState(false);
  const [samples, setSamples] = useState<Sample[]>([]);
  const [selectedSample, setSelectedSample] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [step, setStep] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [elapsed, setElapsed] = useState(0);
  // 后端是示例模式时只能打开预置案例；前端先拦住，免得用户写了一段话才看到报错。
  const [backendMock, setBackendMock] = useState(false);
  const timer = useRef<number | null>(null);

  useEffect(() => {
    let alive = true;
    getSamples()
      .then((data) => alive && setSamples(data.filter((s) => !s.holdout)))
      .catch(() => alive && setSamples([]));
    getHealth()
      .then((h) => alive && setBackendMock(Boolean(h.mock_mode)))
      .catch(() => undefined);
    return () => {
      alive = false;
      if (timer.current) window.clearInterval(timer.current);
    };
  }, []);

  const filledSpec = FIELDS.some((f) => spec[f].trim());

  function pickSample(sample: Sample) {
    if (selectedSample === sample.id) {
      setSelectedSample(null);
      setText("");
      setSpec(EMPTY_SPEC);
      setShowSpec(false);
      return;
    }
    setSelectedSample(sample.id);
    setText(sample.text);
    if (sample.existing_spec) {
      setSpec({ ...EMPTY_SPEC, ...sample.existing_spec });
      setShowSpec(true);
    } else {
      setSpec(EMPTY_SPEC);
      setShowSpec(false);
    }
    setError(null);
  }

  async function submit() {
    if (text.trim().length < 10) {
      setError("先把客户的话贴进来，至少十个字。");
      return;
    }
    if ((isMockMode || backendMock) && !selectedSample) {
      setError("现在是示例模式，只能打开下面的预置案例。要分析你自己写的话，需要切换到真实模式。");
      return;
    }
    setError(null);
    setLoading(true);
    setStep(0);
    setElapsed(0);
    const started = Date.now();
    timer.current = window.setInterval(() => {
      const sec = (Date.now() - started) / 1000;
      setElapsed(Math.floor(sec));
      if (isMockMode) setStep((s) => Math.min(s + 1, PROGRESS_STEPS.length - 1));
      else setStep(STEP_AT.filter((t) => sec >= t).length - 1);
    }, isMockMode ? 350 : 500);
    try {
      const session = await createSession({
        text: text.trim(),
        existing_spec: filledSpec ? spec : null,
        sample_id: selectedSample,
      });
      onCreated(session);
    } catch (err) {
      setError(readableError(err));
      setLoading(false);
    } finally {
      if (timer.current) window.clearInterval(timer.current);
    }
  }

  return (
    <div className="v2-home">
      <TopBar
        center={
          <div className="v2-session-title">
            <h1>新建洽谈</h1>
            <p>贴进客户原话，四步走完：核对需求 → 和客户确认 → 方案 → 交接</p>
          </div>
        }
        right={<ModeBadge />}
      />
      <main className="v2-home-main">
        <section className="v2-hero">
          <Mascot pose="main" size={132} className="v3-hero-mascot" decorative />
          <span className="v3-eyebrow">海派定制 · AI 接单工作台</span>
          <h1>客户说了什么？</h1>
          <p>把客户的原话贴进来。衣诺会逐句核对方案有没有落实、找出需要问客户的点，最后整理成方案初稿和交接单，供你和设计师复核。</p>
          <ol className="v2-flow" aria-label="使用流程">
            <li><b>1</b>核对需求</li>
            <li><b>2</b>和客户确认</li>
            <li><b>3</b>方案</li>
            <li><b>4</b>交接</li>
          </ol>
        </section>

        <section className="v2-composer" aria-label="新建洽谈">
          {loading ? (
            <div className="v2-progress" aria-live="polite">
              <div className="v3-progress-head">
                <Mascot pose="thinking" size={88} decorative />
                <div>
                  <h2>正在分析客户的话…</h2>
                  <p className="v2-mode-line">衣诺在一句一句地对照，拿不准的会标出来。</p>
                </div>
              </div>
              <p className="v2-quote-preview">{text.trim()}</p>
              <ol className="v2-progress-steps">
                {PROGRESS_STEPS.map((label, i) => (
                  <li key={label} data-state={i < step ? "done" : i === step ? "active" : "todo"}>
                    <span className="v2-dot">{i < step ? "✓" : ""}</span>
                    {label}
                  </li>
                ))}
              </ol>
              <p className="v2-mode-line">
                {isMockMode || backendMock
                  ? "正在打开预置案例，马上就好。"
                  : "AI 正在分析，通常需要 20–40 秒。请保持本页打开，不用重复点。"}
              </p>
              {!isMockMode && !backendMock && (
                <p className="v2-elapsed">
                  已等待 {elapsed} 秒
                  {elapsed >= 35 ? " · 还在逐句核对，稍等一下" : ""}
                </p>
              )}
            </div>
          ) : (
            <>
              <label className="v2-label" htmlFor="customer-text">
                客户原话
              </label>
              <textarea
                id="customer-text"
                className="v2-textarea"
                value={text}
                placeholder="例如：想要定制一件外套，风格偏中式一些，适合正式场合穿着，领子不要太高……"
                onChange={(e) => {
                  setText(e.target.value);
                  setSelectedSample(null);
                }}
              />

              {samples.length > 0 && (
                <div className="v2-samples">
                  <span className="v2-samples-label">{isMockMode || backendMock ? "选一个预置案例：" : "试试示例："}</span>
                  {samples.map((s) => {
                    const { name, hint } = sampleLabel(s.title);
                    return (
                      <button
                        key={s.id}
                        type="button"
                        className="v2-chip"
                        aria-pressed={selectedSample === s.id}
                        title={s.title}
                        onClick={() => pickSample(s)}
                      >
                        {name}
                        {hint ? ` · ${hint}` : ""}
                      </button>
                    );
                  })}
                </div>
              )}

              <button type="button" className="v2-toggle" onClick={() => setShowSpec((v) => !v)} aria-expanded={showSpec}>
                {showSpec ? "收起已有方案" : "＋ 附上已有方案（可选）"}
              </button>
              {showSpec && (
                <div className="v2-spec-form">
                  {FIELDS.map((field) => (
                    <FieldInput
                      key={field}
                      field={field}
                      value={spec[field]}
                      onChange={(value) => setSpec((prev) => ({ ...prev, [field]: value }))}
                    />
                  ))}
                </div>
              )}

              {error && (
                <div className="v2-alert" role="alert">
                  {error}
                </div>
              )}

              <div className="v2-composer-foot">
                <p className="v2-mode-line">
                  {filledSpec ? (
                    <>
                      已附方案：衣诺会<strong>逐句检查这份方案有没有落实客户的话</strong>。
                    </>
                  ) : (
                    <>
                      没有附方案：衣诺会<strong>从案例资源里配出第一版方案</strong>，再逐句核对。
                    </>
                  )}
                </p>
                <button type="button" className="v2-btn-primary" onClick={submit}>
                  开始分析
                </button>
              </div>
            </>
          )}
        </section>

        <aside className="v3-guide" aria-label="衣诺会做什么">
          <h2>衣诺会帮你</h2>
          <ol>
            <li>
              <b>逐句核对</b>
              <span>每句客户原话都标出落实情况：已落实、被改了、前后矛盾、交给设计师。</span>
            </li>
            <li>
              <b>只问关键问题</b>
              <span>只挑会改变方案的点去问客户，答一题方案就更新一版。</span>
            </li>
            <li>
              <b>出方案和交接单</b>
              <span>每一栏都带原话和资源编号；没有出处的会写明，交给人工找样。</span>
            </li>
          </ol>
          <p className="v3-guide-note">AI 只出初稿，最终由你和设计师复核。</p>
        </aside>
      </main>
    </div>
  );
}

function FieldInput({ field, value, onChange }: { field: Field; value: string; onChange: (v: string) => void }) {
  const id = `spec-${field}`;
  return (
    <>
      <label htmlFor={id}>{field}</label>
      <input id={id} className="v2-input" value={value} placeholder="没有就留空" onChange={(e) => onChange(e.target.value)} />
    </>
  );
}
