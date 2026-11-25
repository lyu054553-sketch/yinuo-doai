import type { Status } from "../../api/types";
import { STATUS_HINT, statusKey, statusLabel } from "../../utils/display";

/** 大白话状态徽标；悬停时给一句解释。 */
export default function StatusBadge({ status }: { status: Status }) {
  return (
    <span className="v2-status" data-status={statusKey(status)} title={STATUS_HINT[status]}>
      {statusLabel(status)}
    </span>
  );
}
