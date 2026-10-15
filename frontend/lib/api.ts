// Typed client for the FastAPI backend (see backend/app/main.py).

export const API_URL = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000").replace(/\/$/, "");

export const STAGES = ["script", "assets", "voice", "captions", "music", "render"] as const;
export type Stage = (typeof STAGES)[number];
export type StageStatus = "pending" | "running" | "done" | "skipped" | "failed";
export type JobStatus = "queued" | "running" | "done" | "failed";
export type Aspect = "9:16" | "16:9";
export type Tone = "fun" | "informative" | "dramatic";
export type Duration = 30 | 60 | 90;

export interface JobRequest {
  topic: string;
  duration_sec: Duration;
  aspect: Aspect;
  tone: Tone;
  voice: string | null;
  captions: boolean;
  music: boolean;
}

export interface JobDetail {
  id: string;
  request: JobRequest;
  status: JobStatus;
  stage: Stage | null;
  progress: number;
  stages: Record<Stage, StageStatus>;
  log: string[];
  error: string | null;
  output_url: string | null;
  created_at: string;
  updated_at: string;
  queue_position: number | null;
  title: string | null;
}

export interface JobSummary {
  id: string;
  topic: string;
  title: string | null;
  status: JobStatus;
  progress: number;
  aspect: Aspect;
  duration: number | null;
  created_at: string;
  poster_url: string | null;
  video_url: string | null;
}

export interface SceneAsset {
  scene_id: number;
  file: string | null;
  kind: "video" | "image" | null;
  source: string | null;
  source_id: string | null;
  query: string | null;
  width: number | null;
  height: number | null;
  duration: number | null;
  est_duration: number;
  page_url: string | null;
  author: string | null;
}

export interface SceneView {
  id: number;
  narration: string;
  visual_description: string;
  search_queries: string[];
  media_type: "video" | "image";
  asset: SceneAsset | null;
  audio_duration: number | null;
  thumbnail_url: string | null;
}

export interface Voice {
  name: string;
  gender: string;
  locale: string;
}

export interface Health {
  ok: boolean;
  checks: Record<string, boolean>;
  details: Record<string, string>;
}

/** Absolute URL for a path returned by the API (e.g. "/jobs/x/video"). */
export const mediaUrl = (path: string) => `${API_URL}${path}`;

export class ApiError extends Error {
  constructor(message: string, public status: number) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_URL}${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", ...init?.headers },
      cache: "no-store",
    });
  } catch {
    throw new ApiError(`Can't reach the API at ${API_URL}. Is the backend running?`, 0);
  }
  if (!res.ok) {
    let message = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (typeof body.detail === "string") message = body.detail;
      else if (Array.isArray(body.detail)) message = body.detail.map((d: { msg: string }) => d.msg).join("; ");
    } catch {
      /* not JSON */
    }
    throw new ApiError(message, res.status);
  }
  return res.status === 204 ? (undefined as T) : res.json();
}

export const api = {
  health: () => request<Health>("/health"),
  voices: () => request<Voice[]>("/voices"),
  listJobs: (limit = 48) => request<JobSummary[]>(`/jobs?limit=${limit}`),
  getJob: (id: string) => request<JobDetail>(`/jobs/${id}`),
  createJob: (body: JobRequest) =>
    request<{ id: string; status: JobStatus; queue_position: number | null }>("/jobs", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  retryJob: (id: string, fromStage?: Stage) =>
    request<{ id: string }>(`/jobs/${id}/retry${fromStage ? `?from_stage=${fromStage}` : ""}`, { method: "POST" }),
  deleteJob: (id: string) => request<void>(`/jobs/${id}`, { method: "DELETE" }),
  scenes: (id: string) => request<SceneView[]>(`/jobs/${id}/scenes`),
  eventsUrl: (id: string) => `${API_URL}/jobs/${id}/events`,
};
