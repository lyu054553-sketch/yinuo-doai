import type { ReactNode } from "react";
import { isMockMode } from "../api/client";

/** 只在示例数据模式下提示一次；连接真实后端时不打扰业务员。 */
export function ModeBadge() {
  if (!isMockMode) return null;
  return <span className="v2-pill">示例数据</span>;
}

interface Props {
  onHome?: () => void;
  center?: ReactNode;
  right?: ReactNode;
}

/** 顶栏：品牌已移到左侧导航栏，这里只放当前页的标题、进度和操作。 */
export default function TopBar({ center, right }: Props) {
  return (
    <header className="v2-topbar">
      <div className="v2-topbar-center">{center}</div>
      <div className="v2-topbar-right">{right}</div>
    </header>
  );
}
