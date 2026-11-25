import type { Field } from "../api/types";
import devSamples from "./dev-samples.json";

// 示例数据模式下的示例案例。
// 直接用后端开发夹具 backend/storage/fixtures/dev-samples.json 的原样复制件，
// 不手工改写文案和方案取值，保证示例模式与真实后端看到的是同一份数据。
// 只包含五个开发案例；案例五是留出测试案例，这里不提供。
export interface MockSample {
  id: string;
  title: string;
  text: string;
  existing_spec: Record<Field, string> | null;
  holdout: boolean;
}

export const MOCK_SAMPLES = devSamples as unknown as MockSample[];