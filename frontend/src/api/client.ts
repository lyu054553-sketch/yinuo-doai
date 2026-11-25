import type {
  AnswerRequest,
  CreateSessionRequest,
  Handoff,
  HealthInfo,
  ImageKind,
  PatchSpecRequest,
  PromptsResult,
  RequirementsAnalysis,
  ResourceCatalog,
  ResourceLibrary,
  Sample,
  Session,
  SessionSummary,
  FabricOption,
  SimilarCase,
} from "./types";
import { ApiError, OFFLINE_MESSAGE } from "./errors";
import * as mock from "./mockClient";

const USE_MOCK = import.meta.env.VITE_USE_MOCK === "1";

export const isMockMode = USE_MOCK;

interface RequestOptions {
  method?: "GET" | "POST" | "PATCH";
  body?: unknown;
  /** 后端用 JSON null 表示“还没有”时，返回 null 而不是报错。 */
  allowNull?: boolean;
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const init: RequestInit = { method: options.method ?? "GET" };
  if (options.body !== undefined) {
    init.headers = { "Content-Type": "application/json" };
    init.body = JSON.stringify(options.body);
  }
  let response: Response;
  try {
    response = await fetch(`/api${path}`, init);
  } catch {
    throw new ApiError(OFFLINE_MESSAGE, 0);
  }
  return parseResponse<T>(response, options.allowNull ?? false);
}

async function parseResponse<T>(response: Response, allowNull = false): Promise<T> {
  const text = await response.text();
  let payload: unknown = null;
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      payload = null;
    }
  }
  if (!response.ok) {
    const message =
      payload && typeof payload === "object" && "error" in payload
        ? String((payload as { error: unknown }).error)
        : `操作没有成功（${response.status}）`; // readableError 会换成通俗说法
    throw new ApiError(message, response.status);
  }
  if (payload === null) {
    if (allowNull && text.trim() === "null") return null as T;
    throw new ApiError("后端返回的内容看不懂，请稍后重试。", response.status);
  }
  return payload as T;
}

async function upload<T>(path: string, form: FormData): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`/api${path}`, { method: "POST", body: form });
  } catch {
    throw new ApiError(OFFLINE_MESSAGE, 0);
  }
  return parseResponse<T>(response);
}

export async function getHealth(): Promise<HealthInfo> {
  if (USE_MOCK) return mock.getHealth();
  return request<HealthInfo>("/health");
}

export async function getSamples(): Promise<Sample[]> {
  if (USE_MOCK) return mock.getSamples();
  return request<Sample[]>("/samples");
}

export async function getResources(): Promise<ResourceLibrary> {
  if (USE_MOCK) return mock.getResources();
  return request<ResourceLibrary>("/resources");
}

export async function getResourceCatalog(): Promise<ResourceCatalog> {
  if (USE_MOCK) return mock.getResourceCatalog();
  return request<ResourceCatalog>("/resource-catalog");
}

export async function getNotes(id: string): Promise<Record<string, string>> {
  if (USE_MOCK) return mock.getNotes(id);
  return request(`/sessions/${encodeURIComponent(id)}/notes`);
}

export async function getRequirementsAnalysis(id: string): Promise<RequirementsAnalysis | null> {
  if (USE_MOCK) return null;
  return request(`/sessions/${encodeURIComponent(id)}/requirements-analysis`, { allowNull: true });
}

export async function runRequirementsAnalysis(id: string): Promise<RequirementsAnalysis> {
  if (USE_MOCK) throw new ApiError("示例模式不调用模型。", 400);
  return request(`/sessions/${encodeURIComponent(id)}/requirements-analysis`, { method: "POST" });
}

export async function reviewRequirementsAnalysis(
  id: string,
  categories: RequirementsAnalysis["categories"],
  note: string,
): Promise<RequirementsAnalysis> {
  if (USE_MOCK) throw new ApiError("示例模式没有模型分类可核对。", 400);
  return request(`/sessions/${encodeURIComponent(id)}/requirements-analysis`, {
    method: "PATCH",
    body: { categories, note },
  });
}

export async function updateNote(id: string, title: string, value: string): Promise<Record<string, string>> {
  if (USE_MOCK) return mock.updateNote(id, title, value);
  return request(`/sessions/${encodeURIComponent(id)}/notes`, { method: "PATCH", body: { title, value } });
}

export async function createSession(payload: CreateSessionRequest): Promise<Session> {
  if (USE_MOCK) return mock.createSession(payload);
  return request<Session>("/sessions", { method: "POST", body: payload });
}

export async function getSessionList(): Promise<SessionSummary[]> {
  if (USE_MOCK) return mock.getSessionList();
  return request<SessionSummary[]>("/sessions");
}

export async function getSession(id: string): Promise<Session> {
  if (USE_MOCK) return mock.getSession(id);
  return request<Session>(`/sessions/${encodeURIComponent(id)}`);
}

export async function answerQuestion(
  sessionId: string,
  payload: AnswerRequest,
): Promise<Session> {
  if (USE_MOCK) return mock.answerQuestionRequest(sessionId, payload);
  return request<Session>(`/sessions/${encodeURIComponent(sessionId)}/answers`, {
    method: "POST",
    body: payload,
  });
}

export async function patchSpec(
  sessionId: string,
  payload: PatchSpecRequest,
): Promise<Session> {
  if (USE_MOCK) return mock.patchSpecRequest(sessionId, payload);
  return request<Session>(`/sessions/${encodeURIComponent(sessionId)}/spec`, {
    method: "PATCH",
    body: payload,
  });
}

export async function getHandoff(sessionId: string): Promise<Handoff> {
  if (USE_MOCK) return mock.getHandoff(sessionId);
  return request<Handoff>(`/sessions/${encodeURIComponent(sessionId)}/handoff`);
}

export async function getPrompts(sessionId: string): Promise<PromptsResult> {
  if (USE_MOCK) return mock.getPrompts(sessionId);
  return request<PromptsResult>(`/sessions/${encodeURIComponent(sessionId)}/prompts`);
}

export async function uploadReference(sessionId: string, file: File): Promise<Session> {
  if (USE_MOCK) return mock.uploadReference(sessionId, file);
  const form = new FormData();
  form.append("file", file);
  return upload<Session>(`/sessions/${encodeURIComponent(sessionId)}/reference`, form);
}

export async function generateImage(
  sessionId: string,
  kind: ImageKind,
): Promise<Session> {
  if (USE_MOCK) return mock.generateImage(sessionId, kind);
  return request<Session>(`/sessions/${encodeURIComponent(sessionId)}/images`, {
    method: "POST",
    body: { kind },
  });
}

export async function uploadCheckImage(
  sessionId: string,
  kind: ImageKind,
  file: File,
): Promise<Session> {
  if (USE_MOCK) return mock.generateImage(sessionId, kind, file);
  const form = new FormData();
  form.append("file", file);
  form.append("kind", kind);
  return upload<Session>(`/sessions/${encodeURIComponent(sessionId)}/images`, form);
}

// ---------------------------------------------------------------- 工作台辅助：相似旧单、面料备选、复述确认

const MOCK_UNSUPPORTED = "示例模式不提供这个功能，请在真实模式下使用。";
const sid = (id: string) => encodeURIComponent(id);

export async function getSimilarCases(sessionId: string): Promise<SimilarCase[]> {
  if (USE_MOCK) throw new ApiError(MOCK_UNSUPPORTED, 501);
  return request<SimilarCase[]>(`/sessions/${sid(sessionId)}/similar-cases`);
}

export async function reuseCaseField(sessionId: string, caseId: string, field: string): Promise<Session> {
  if (USE_MOCK) throw new ApiError(MOCK_UNSUPPORTED, 501);
  return request<Session>(`/sessions/${sid(sessionId)}/reuse`, { method: "POST", body: { case_id: caseId, field } });
}

export async function getFabricOptions(sessionId: string): Promise<FabricOption[]> {
  if (USE_MOCK) throw new ApiError(MOCK_UNSUPPORTED, 501);
  return request<FabricOption[]>(`/sessions/${sid(sessionId)}/fabric-options`);
}

export async function chooseFabric(sessionId: string, resourceId: string): Promise<Session> {
  if (USE_MOCK) throw new ApiError(MOCK_UNSUPPORTED, 501);
  return request<Session>(`/sessions/${sid(sessionId)}/fabric`, { method: "POST", body: { resource_id: resourceId } });
}

export async function createRestatement(sessionId: string): Promise<Session> {
  if (USE_MOCK) throw new ApiError(MOCK_UNSUPPORTED, 501);
  return request<Session>(`/sessions/${sid(sessionId)}/restatement`, { method: "POST" });
}

export async function answerRestatement(
  sessionId: string,
  pointId: string,
  answer: "对" | "不对",
  note = "",
): Promise<Session> {
  if (USE_MOCK) throw new ApiError(MOCK_UNSUPPORTED, 501);
  return request<Session>(`/sessions/${sid(sessionId)}/restatement/answer`, {
    method: "POST",
    body: { point_id: pointId, answer, note },
  });
}
