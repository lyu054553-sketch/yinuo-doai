import type {
  AnswerRequest,
  CreateSessionRequest,
  Handoff,
  HealthInfo,
  PatchSpecRequest,
  PromptsResult,
  ResourceCatalog,
  ResourceLibrary,
  Sample,
  Session,
  SessionSummary,
} from "./types";
import { ApiError } from "./errors";
import { buildCase2Session, answerQuestion, buildHandoff, patchSpec } from "../mocks/case2";
import { MOCK_SAMPLES } from "../mocks/samples";
import { MOCK_RESOURCES, buildMockResourceCatalog } from "../mocks/resources";
import { buildGenericSession } from "../mocks/generic";

// 示例数据模式：不连后端，全部在浏览器内完成，方便离线演示。
const store = new Map<string, Session>();
const notesStore = new Map<string, Record<string, string>>();
let sequence = 1;

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

export async function getHealth(): Promise<HealthInfo> {
  await delay(80);
  return {
    ok: true,
    mock_mode: true,
    llm_model: "示例数据（未调用模型）",
    image_provider: "none",
    vision_ok: false,
  };
}

export async function getSamples(): Promise<Sample[]> {
  await delay(80);
  return clone(MOCK_SAMPLES);
}

export async function getResources(): Promise<ResourceLibrary> {
  await delay(80);
  return clone(MOCK_RESOURCES);
}

export async function getResourceCatalog(): Promise<ResourceCatalog> {
  await delay(80);
  return clone(buildMockResourceCatalog(MOCK_RESOURCES));
}

export async function createSession(request: CreateSessionRequest): Promise<Session> {
  await delay(1200);
  const sample = request.sample_id
    ? MOCK_SAMPLES.find((s) => s.id === request.sample_id)
    : undefined;
  if (request.sample_id && !sample) {
    throw new ApiError("示例数据里没有这个案例。", 404);
  }
  const id = `mock-s${sequence++}`;
  const mode = request.existing_spec || sample ? "审计已有方案" : "从零生成方案";
  const title = request.title?.trim() || sample?.title || "新洽谈 · 从零生成";
  const session =
    sample?.id === "c2"
      ? buildCase2Session(id, title)
      : buildGenericSession({
          id,
          title,
          text: request.text,
          existingSpec: request.existing_spec ?? sample?.existing_spec ?? null,
          mode,
          sampleId: request.sample_id ?? null,
        });
  store.set(session.id, session);
  return clone(session);
}

export async function getSessionList(): Promise<SessionSummary[]> {
  await delay(120);
  return Array.from(store.values())
    .sort((a, b) => b.created_at.localeCompare(a.created_at))
    .map((s) => ({
      id: s.id,
      title: s.title,
      created_at: s.created_at,
      version: s.spec_versions[s.spec_versions.length - 1].version,
    }));
}

function requireSession(id: string): Session {
  const session = store.get(id);
  if (!session) throw new ApiError("找不到这个洽谈记录，可能还没创建。", 404);
  return session;
}

export async function getNotes(id: string): Promise<Record<string, string>> {
  requireSession(id);
  return clone(notesStore.get(id) ?? {});
}

export async function updateNote(id: string, title: string, value: string): Promise<Record<string, string>> {
  requireSession(id);
  const next = { ...notesStore.get(id), [title]: value };
  notesStore.set(id, next);
  return clone(next);
}

export async function getSession(id: string): Promise<Session> {
  await delay(120);
  return clone(requireSession(id));
}

export async function answerQuestionRequest(
  sessionId: string,
  request: AnswerRequest,
): Promise<Session> {
  await delay(420);
  const next = answerQuestion(requireSession(sessionId), request.question_id, request.option_id);
  store.set(next.id, next);
  return clone(next);
}

export async function patchSpecRequest(
  sessionId: string,
  request: PatchSpecRequest,
): Promise<Session> {
  await delay(320);
  const next = patchSpec(requireSession(sessionId), request.field, request.value, request.reason);
  store.set(next.id, next);
  return clone(next);
}

export async function getHandoff(id: string): Promise<Handoff> {
  await delay(120);
  return { markdown: buildHandoff(requireSession(id)) };
}

async function mediaNotReady(): Promise<never> {
  await delay(120);
  throw new ApiError("该功能开发中", 501);
}

export const getPrompts = async (_id: string): Promise<PromptsResult> => mediaNotReady();

export const uploadReference = async (_id: string, _file: File): Promise<Session> =>
  mediaNotReady();

export const generateImage = async (
  _id: string,
  _kind: string,
  _file?: File,
): Promise<Session> => mediaNotReady();
