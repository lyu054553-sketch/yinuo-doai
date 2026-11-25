import type { Strength } from "../../api/types";
import { STRENGTH_HINT } from "../../utils/display";

/** 客户这句话有多硬：硬约束必须满足，软偏好可以协商。只给这两类上标签，其余不打扰。 */
export default function StrengthTag({ strength }: { strength: Strength | undefined }) {
  if (strength !== "硬约束" && strength !== "软偏好") return null;
  return (
    <span
      className="v2-strength"
      data-kind={strength === "硬约束" ? "hard" : "soft"}
      title={strength === "硬约束" ? "硬约束：用来筛掉不符合的资源" : "软偏好：只用来给候选排序，可以和客户协商"}
    >
      {strength} · {STRENGTH_HINT[strength]}
    </span>
  );
}
