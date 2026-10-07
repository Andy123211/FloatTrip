import type {
  ItineraryEnvelope,
  RunCreateRequest,
  RunPublic,
  RuntimeEvent,
} from "./types";

const API = "/api";

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

function detailMessage(detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (detail && typeof detail === "object") {
    const value = detail as Record<string, unknown>;
    if (typeof value.message === "string") return value.message;
    if (Array.isArray(value.missing_fields))
      return `规划信息不完整：${value.missing_fields.join("、")}`;
    if (typeof value.code === "string") return value.code;
  }
  return "请求没有完成，请检查服务状态后重试。";
}

async function request<T>(
  path: string,
  token?: string,
  init: RequestInit = {},
): Promise<T> {
  const headers = new Headers(init.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (init.body && !headers.has("Content-Type"))
    headers.set("Content-Type", "application/json");
  let response: Response;
  try {
    response = await fetch(`${API}${path}`, { ...init, headers });
  } catch {
    throw new ApiError(
      0,
      "无法连接规划服务。请确认 FastAPI 已在 8765 端口启动。",
    );
  }
  const text = await response.text();
  let data: unknown = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = text;
  }
  if (!response.ok) {
    const detail =
      data && typeof data === "object"
        ? (data as { detail?: unknown }).detail
        : data;
    throw new ApiError(response.status, detailMessage(detail));
  }
  return data as T;
}

export async function authenticate(
  mode: "login" | "register",
  username: string,
  password: string,
) {
  return request<{ user_id: string; username: string; token: string }>(
    `/auth/${mode}`,
    undefined,
    {
      method: "POST",
      body: JSON.stringify({ username, password }),
    },
  );
}

export async function checkHealth() {
  return request<{ status: string }>("/health");
}

export async function createPlanningRun(
  token: string,
  payload: RunCreateRequest,
) {
  return request<RunPublic>("/runs", token, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function getRun(token: string, runId: string) {
  return request<RunPublic>(`/runs/${encodeURIComponent(runId)}`, token);
}

export async function getRunEvents(
  token: string,
  runId: string,
  afterSequence: number,
) {
  return request<RuntimeEvent[]>(
    `/runs/${encodeURIComponent(runId)}/events?after_seq=${afterSequence}`,
    token,
  );
}

export async function getItinerary(token: string, itineraryId: string) {
  return request<ItineraryEnvelope>(
    `/history/${encodeURIComponent(itineraryId)}`,
    token,
  );
}
