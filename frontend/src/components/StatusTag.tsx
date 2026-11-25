import type { ReactNode } from "react";
import type { Field, Status, Strength } from "../api/types";
import { statusKey } from "../utils/display";

export function StatusTag({ status, size = "normal" }: { status: Status; size?: "normal" | "small" }) {
  return (
    <span className={`status-tag status-${statusKey(status)} ${size === "small" ? "status-tag-sm" : ""}`}>
      {status}
    </span>
  );
}

export function StrengthTag({ strength }: { strength: Strength }) {
  const cls =
    strength === "硬约束" ? "strength-hard" : strength === "软偏好" ? "strength-soft" : "strength-light";
  return <span className={`strength-tag ${cls}`}>{strength}</span>;
}

export function FieldTag({ field }: { field: Field }) {
  return <span className="field-tag">{field}</span>;
}

export function OriginTag({ origin }: { origin: string }) {
  const cls = origin === "无出处" ? "origin-none" : origin === "客户确认" ? "origin-confirm" : "origin-normal";
  return <span className={`origin-tag ${cls}`}>{origin === "无出处" ? "库中暂无 · 待找样" : origin}</span>;
}

/** 自绘小锁：用在“客户已确认”的提示上，不引入外部图标。 */
export function LockMark({ size = 12 }: { size?: number }) {
  return (
    <svg
      className="lock-mark"
      width={size}
      height={size}
      viewBox="0 0 12 12"
      aria-hidden="true"
      focusable="false"
    >
      <rect x="2.2" y="5.2" width="7.6" height="5.4" rx="1" fill="none" stroke="currentColor" strokeWidth="1.2" />
      <path d="M4.2 5.2V3.9a1.8 1.8 0 0 1 3.6 0V5.2" fill="none" stroke="currentColor" strokeWidth="1.2" />
    </svg>
  );
}

export function EmptyHint({ children }: { children: ReactNode }) {
  return <p className="empty-hint">{children}</p>;
}