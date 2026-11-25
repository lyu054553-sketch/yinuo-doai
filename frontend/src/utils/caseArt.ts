/**
 * 历史案例的示意图。全部是 AI 生成的示意（非上服实物），由 scripts 生成后放在 public/case-art/。
 * 上服提供的真实照片只用于测试，不在这里引用，也不进入公开部署。
 * 留出案例（c5）不提供任何图片。
 */
const BASE = import.meta.env.BASE_URL;

export const CASE_ART: Record<string, { garment: string; wear: string }> = {
  c1: { garment: `${BASE}case-art/c1-garment.png`, wear: `${BASE}case-art/c1-wear.png` },
  c2: { garment: `${BASE}case-art/c2-garment.png`, wear: `${BASE}case-art/c2-wear.png` },
  c3: { garment: `${BASE}case-art/c3-garment.png`, wear: `${BASE}case-art/c3-wear.png` },
  c4: { garment: `${BASE}case-art/c4-garment.png`, wear: `${BASE}case-art/c4-wear.png` },
  c6: { garment: `${BASE}case-art/c6-garment.png`, wear: `${BASE}case-art/c6-wear.png` },
};
