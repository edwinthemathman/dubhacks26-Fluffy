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
    const detail = await readDetail(response);
    return { ok: response.ok, status: response.status, missing: response.status === 404 || response.status === 405, detail };
  } catch (error) {
    return { ok: false, status: 0, missing: false, detail: error instanceof Error ? error.message : "Network error" };
  }
}

export async function getJson(url: string): Promise<HttpResult & { body: unknown }> {
  try {
    const response = await fetch(url, { headers: { accept: "application/json" } });
    const detail = await readDetail(response);
    let body: unknown = null;
    try {
      body = detail ? JSON.parse(detail) : null;
    } catch {
      body = null;
    }
    return { ok: response.ok, status: response.status, missing: response.status === 404, detail, body };
  } catch (error) {
    return { ok: false, status: 0, missing: false, detail: error instanceof Error ? error.message : "Network error", body: null };
  }
}

async function readDetail(response: Response): Promise<string> {
  try {
    return (await response.text()).slice(0, 400);
  } catch {
    return response.statusText;
  }
}

export function textInputUrl(config: FluffyPublicConfig): string {
  return `${config.httpBase}/api/input/text`;
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

export function toolsUrl(config: FluffyPublicConfig): string {
  return `${config.httpBase}/api/tools`;
}
