import type { FluffyPublicConfig } from "./config";

export type HttpResult = {
  ok: boolean;
  status: number;
  missing: boolean;
  detail: string;
};

export async function postJson(url: string, body: unknown): Promise<HttpResult> {
  try {
    const response = await fetch(url, {
      method: "POST",
      headers: { "content-type": "application/json", accept: "application/json" },
      body: JSON.stringify(body),
    });
    const raw = await response.text();
    const detail = readableDetail(response.ok ? "" : raw.slice(0, 400), response.statusText);
    return { ok: response.ok, status: response.status, missing: response.status === 404 || response.status === 405, detail };
  } catch (error) {
    return { ok: false, status: 0, missing: false, detail: error instanceof Error ? error.message : "Network error" };
  }
}

export async function getJson(url: string): Promise<HttpResult & { body: unknown }> {
  try {
    const response = await fetch(url, { headers: { accept: "application/json" } });
    const raw = await response.text();
    const detail = readableDetail(response.ok ? "" : raw.slice(0, 400), response.statusText);
    let body: unknown = null;
    try {
      body = raw ? JSON.parse(raw) : null;
    } catch {
      body = null;
    }
    return { ok: response.ok, status: response.status, missing: response.status === 404, detail, body };
  } catch (error) {
    return { ok: false, status: 0, missing: false, detail: error instanceof Error ? error.message : "Network error", body: null };
  }
}

function readableDetail(raw: string, fallback: string): string {
  if (!raw) return fallback;
  try {
    const parsed = JSON.parse(raw) as { detail?: unknown; message?: unknown };
    if (typeof parsed.message === "string") return parsed.message;
    if (typeof parsed.detail === "string") return parsed.detail;
    if (parsed.detail && typeof parsed.detail === "object" && "message" in parsed.detail) {
      const message = (parsed.detail as { message?: unknown }).message;
      if (typeof message === "string") return message;
    }
  } catch {
    return raw;
  }
  return raw;
}

export function textInputUrl(config: FluffyPublicConfig): string {
  return `${config.httpBase}/api/input/text`;
}

export function transcriptUrl(config: FluffyPublicConfig): string {
  return `${config.httpBase}/api/input/transcript`;
}

export function questionUrl(config: FluffyPublicConfig, questionId: string): string {
  return `${config.httpBase}/api/questions/${encodeURIComponent(questionId)}/answer`;
}

export function stopUrl(config: FluffyPublicConfig): string {
  return `${config.httpBase}/api/session/stop`;
}

export function speechAckUrl(config: FluffyPublicConfig): string {
  return `${config.httpBase}/api/speech/status`;
}

export function mediaFrameUrl(config: FluffyPublicConfig): string {
  return `${config.httpBase}/api/media/frames`;
}

export function mediaAudioUrl(config: FluffyPublicConfig): string {
  return `${config.httpBase}/api/media/audio`;
}

export function healthUrl(config: FluffyPublicConfig): string {
  return `${config.httpBase}/api/health`;
}

export function sessionUrl(config: FluffyPublicConfig): string {
  return `${config.httpBase}/api/session`;
}

export function stateUrl(config: FluffyPublicConfig): string {
  return `${config.httpBase}/api/state`;
}

export function toolsUrl(config: FluffyPublicConfig): string {
  return `${config.httpBase}/api/tools`;
}
