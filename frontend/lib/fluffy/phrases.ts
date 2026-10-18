import { lightLabel, percentLabel } from "./levels";
import type { ActionModel, ActionStatus, HomeModel, SessionState } from "./types";

export type SensingFacts = {
  started: boolean;
  paused: boolean;
  preview: boolean;
  cameraOn: boolean;
  transport: "idle" | "connecting" | "reaching" | "offline" | "unsupported";
  inferenceAvailable: boolean;
};

const MUSIC = new Set(["play_sound", "stop_sound", "play_music", "stop_music", "spotify_play", "spotify_pause"]);
const MESSAGE = new Set(["alert_caregiver", "photon_send", "send_message", "send_text"]);

export function isMusicTool(tool: string): boolean {
  return MUSIC.has(tool) || tool.includes("spotify") || tool.includes("music");
}

export function isMessageTool(tool: string): boolean {
  return MESSAGE.has(tool);
}

export function isLightTool(tool: string): boolean {
  return tool === "dim_lights" || tool === "set_brightness" || tool === "set_light_warmth" || tool === "flash_lights";
}

export function startedPhrase(tool: string): string {
  if (isLightTool(tool)) return tool === "flash_lights" ? "Flashing the lights" : "Adjusting the lights";
  if (isMusicTool(tool)) return "Starting your music";
  if (tool === "set_blinds") return "Adjusting the blinds";
  if (isMessageTool(tool)) return "Sending a message";
  if (tool === "show_caption") return "Showing a caption";
  if (tool === "announce") return "Speaking";
  return "Starting that action";
}

export function verifiedPhrase(action: ActionModel, home: HomeModel): string {
  if (action.tool === "dim_lights" || action.tool === "set_brightness") {
    const targeted = typeof action.args.light === "string" ? home.lights[action.args.light] : null;
    if (targeted?.luminosity != null) return `Lights set to ${percentLabel(targeted.luminosity)}`;
    const levels = Object.values(home.lights).map((light) => light.luminosity).filter((value): value is number => value != null);
    if (levels.length === 1) return `Lights set to ${percentLabel(levels[0])}`;
    if (levels.length > 1) return `Lights set to ${levels.map((value) => percentLabel(value)).join(" and ")}`;
    return "Lights updated";
  }
  if (action.tool === "set_light_warmth") return "Light warmth updated";
  if (action.tool === "set_blinds") {
    const blind = Object.values(home.blinds)[0];
    return blind?.position != null ? `Blinds set to ${percentLabel(blind.position)} open` : "Blinds updated";
  }
  if (action.tool === "play_sound" || action.tool === "stop_sound") {
    if (home.speaker.playback === "stopped" || action.tool === "stop_sound") return "Sound stopped";
    if (home.speaker.sound) return `Sound set to ${home.speaker.sound}`;
    return "Sound updated";
  }
  if (isMusicTool(action.tool) && home.speaker.playback === "playing" && home.speaker.confirmed) return "Music is playing";
  if (action.tool === "show_caption") return home.caption?.text ? "Caption shown" : "Caption updated";
  if (action.status === "delivered") return "Message delivered";
  if (action.status === "accepted") return "Message submitted";
  return "Home updated";
}

export function statusPhrase(action: ActionModel, home: HomeModel): string {
  if (action.detail && (action.status === "failed" || action.status === "unknown" || action.status === "rejected")) {
    return action.detail;
  }
  if (action.status === "proposed" || action.status === "approved") return "Proposed action";
  if (action.status === "started") return startedPhrase(action.tool);
  if (action.status === "verified") return verifiedPhrase(action, home);
  if (action.status === "accepted") return "Message submitted";
  if (action.status === "delivered") return "Message delivered";
  if (action.status === "failed") return action.detail ?? "Could not complete that";
  if (action.status === "rejected") return action.detail ?? "Left unchanged";
  if (isMusicTool(action.tool)) return "Could not verify playback";
  return action.detail ?? "Could not verify that action";
}

export function thinkingLabel(state: SessionState, sensing: SensingFacts): string {
  if (state.question && !state.question.answer) return state.question.text;
  const action = currentAction(state);
  if (action && action.status !== "proposed" && action.status !== "approved") {
    return action.summary ?? statusPhrase(action, state.home);
  }
  if (action && (action.status === "proposed" || action.status === "approved")) return "Proposed action";
  if (state.inference === "running" && state.stage) return state.stage;
  if (state.inference === "running") return "Considering your request";
  if (state.observationPending) return "Checking the room";
  if (sensing.started && !sensing.paused && sensing.transport === "reaching" && sensing.inferenceAvailable) {
    return "Watching and listening";
  }
  if (sensing.preview && sensing.cameraOn && !sensing.inferenceAvailable) return "Camera preview only";
  if (sensing.preview && sensing.cameraOn && sensing.transport !== "reaching") return "Camera preview only";
  return "";
}

export function currentAction(state: SessionState): ActionModel | null {
  const turn = [...state.turns].reverse().find((item) => item.open) ?? state.turns[state.turns.length - 1];
  if (!turn) return null;
  const rows = turn.actionIds.map((id) => state.actions[id]).filter((item): item is ActionModel => item != null);
  if (!rows.length) return null;
  return rows.reduce((best, row) => {
    if (rank(row.status) > rank(best.status)) return row;
    if (rank(row.status) === rank(best.status) && row.updatedAt >= best.updatedAt) return row;
    return best;
  });
}

function rank(status: ActionStatus): number {
  if (status === "verified" || status === "delivered" || status === "failed") return 5;
  if (status === "accepted" || status === "unknown") return 4;
  if (status === "started") return 3;
  if (status === "rejected") return 2;
  if (status === "approved") return 1;
  return 0;
}

export function toolTitle(tool: string): string {
  return tool.replaceAll("_", " ");
}

export function deviceName(id: string): string {
  return lightLabel(id);
}
