export type MascotPose = "main" | "thinking" | "happy" | "empty" | "avatar";

const ALT: Record<MascotPose, string> = {
  main: "衣诺吉祥物：盘扣小精灵",
  thinking: "衣诺正在分析",
  happy: "衣诺：都确认完了",
  empty: "衣诺：还没有记录",
  avatar: "衣诺",
};

/** 衣诺的 IP 形象：盘扣结做头、针插包做身子、软尺当围巾。纯装饰时传 decorative。 */
export default function Mascot({
  pose = "main",
  size = 96,
  decorative = false,
  className,
}: {
  pose?: MascotPose;
  size?: number;
  decorative?: boolean;
  className?: string;
}) {
  return (
    <img
      className={["v3-mascot", className].filter(Boolean).join(" ")}
      src={`${import.meta.env.BASE_URL}mascot/${pose}.webp`}
      width={size}
      height={size}
      alt={decorative ? "" : ALT[pose]}
      aria-hidden={decorative || undefined}
      draggable={false}
    />
  );
}
