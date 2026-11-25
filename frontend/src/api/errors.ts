/** 后端或示例数据层返回的业务错误：message 是可以直接给业务员看的中文。 */
export class ApiError extends Error {
  status: number;

  constructor(message: string, status = 500) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

export function isApiError(error: unknown): error is ApiError {
  return error instanceof ApiError;
}

export const OFFLINE_MESSAGE = "暂时连不上分析服务，你填的内容还在。请稍后重试；一直不行就请负责演示的同事检查服务。";
const GENERIC_MESSAGE = "这次操作没有完成，你填的内容还在，请重试。";

/** 后端偶尔会带出配置项名（LLM_TIMEOUT、BASE_URL 等），换成业务员能看懂的话。 */
function friendly(message: string, status?: number): string {
  if (/超时|timeout/i.test(message)) return "AI 这次分析超时了，你填的内容还在，请再点一次试试。";
  if (/LLM_|BASE_URL|API_KEY|网关|gateway/i.test(message)) return "现在连不上 AI 服务，你填的内容还在。请稍后重试，或先打开示例案例看流程。";
  if (/^操作没有成功（\d+）$/.test(message) || (status !== undefined && status >= 500 && !/[\u4e00-\u9fa5]{4,}/.test(message))) {
    return GENERIC_MESSAGE;
  }
  return message;
}

/** 把任意异常转成一句中文提示，避免把技术栈错误贴到界面上。 */
export function readableError(error: unknown): string {
  if (error instanceof ApiError) return error.status === 0 ? OFFLINE_MESSAGE : friendly(error.message, error.status);
  if (error instanceof Error && error.message) {
    if (/Failed to fetch|NetworkError|Load failed/i.test(error.message)) return OFFLINE_MESSAGE;
    return GENERIC_MESSAGE;
  }
  return GENERIC_MESSAGE;
}