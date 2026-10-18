import type { PhoneChannel, RoomObject, RoomState, Warmth } from "./state";

/** One row per tool name. Add an alias here when the executor settles a name. */
export type ToolAction =
  | "level"
  | "warmth"
  | "flash"
  | "open"
  | "close"
  | "set"
  | "play"
  | "stop"
  | "text"
  | "call"
  | "ask";

export type ToolBinding = { target: RoomObject | "ask"; action: ToolAction };

export const TOOL_MAP: Record<string, ToolBinding> = {
  dim_lights: { target: "light", action: "level" },
  set_brightness: { target: "light", action: "level" },
  set_light_warmth: { target: "light", action: "warmth" },
  flash_lights: { target: "light", action: "flash" },
  open_door: { target: "door", action: "open" },
  close_door: { target: "door", action: "close" },
  set_door: { target: "door", action: "set" },
  play_music: { target: "speaker", action: "play" },
  spotify_play: { target: "speaker", action: "play" },
  play_sound: { target: "speaker", action: "play" },
  stop_music: { target: "speaker", action: "stop" },
  spotify_pause: { target: "speaker", action: "stop" },
  stop_sound: { target: "speaker", action: "stop" },
  send_text: { target: "phone", action: "text" },
  photon_send: { target: "phone", action: "text" },
  send_message: { target: "phone", action: "text" },
  call_person: { target: "phone", action: "call" },
  ask_user: { target: "ask", action: "ask" },
  ask_clarification: { target: "ask", action: "ask" },
};

const NAMED_COLORS: Record<string, string> = {
  blue: "#3b82f6",
  red: "#ef4444",
  amber: "#f5b942",
  white: "#f4f8fd",
  green: "#22c55e",
};

export const TEXT_FOR_MS = 4000;
export const CALL_FOR_MS = 8000;

export function lookupTool(name: string): ToolBinding | null {
  return TOOL_MAP[name] ?? null;
}

export function cssColor(input: unknown, fallback = "#3b82f6"): string {
  if (typeof input !== "string") return fallback;
  const text = input.trim();
  const named = NAMED_COLORS[text.toLowerCase()];
  if (named) return named;
  if (/^#([0-9a-f]{3}|[0-9a-f]{6})$/i.test(text)) return text;
  return fallback;
}

export function asLevel(value: unknown): number | null {
  if (typeof value !== "number" || Number.isNaN(value)) return null;
  const scaled = value > 0 && value <= 1 ? value * 100 : value;
  return clamp(scaled, 0, 100);
}

export function asWarmth(value: unknown): Warmth | null {
  if (value === "warm" || value === "neutral" || value === "cool") return value;
  return null;
}

export function asChannel(value: unknown): PhoneChannel | undefined {
  if (value === "imessage" || value === "sms" || value === "whatsapp" || value === "other") return value;
  return undefined;
}

export function resultFailed(result: Record<string, unknown> | undefined): boolean {
  if (!result) return false;
  if (result.ok === false || result.failed === true) return true;
  if (result.status === "failed" || result.status === "error") return true;
  return false;
}

/** Apply a finished tool call. Null means the executor reported a failure. */
export function applyTool(
  state: RoomState,
  binding: ToolBinding,
  args: Record<string, unknown>,
  result: Record<string, unknown> | undefined,
  now: number,
): RoomState | null {
  if (resultFailed(result)) return null;
  if (binding.action === "level") {
    const level = asLevel(args.luminosity ?? args.level ?? args.brightness);
    if (level == null) return state;
    return { ...state, light: { ...state.light, level } };
  }
  if (binding.action === "warmth") {
    const warmth = asWarmth(args.warmth);
    if (!warmth) return state;
    return { ...state, light: { ...state.light, warmth } };
  }
  if (binding.action === "flash") {
    const seconds = typeof args.duration_s === "number" ? args.duration_s : 2;
    return {
      ...state,
      light: {
        ...state.light,
        flash: { color: cssColor(args.color), until: now + seconds * 1000 },
      },
    };
  }
  if (binding.action === "open") return { ...state, door: { open: true } };
  if (binding.action === "close") return { ...state, door: { open: false } };
  if (binding.action === "set") {
    const open = args.open === false || args.state === "closed" ? false : true;
    return { ...state, door: { open } };
  }
  if (binding.action === "stop") return { ...state, speaker: { playing: false } };
  if (binding.action === "play") {
    const track = text(result?.track) ?? text(args.track) ?? text(args.sound) ?? text(result?.sound);
    const artist = text(result?.artist) ?? text(args.artist);
    return { ...state, speaker: { playing: true, track, artist } };
  }
  if (binding.action === "text") {
    return {
      ...state,
      phone: {
        mode: "texting",
        to: text(result?.to) ?? text(args.to) ?? text(args.recipient) ?? "someone",
        preview: text(result?.preview) ?? text(result?.text) ?? text(args.preview) ?? text(args.text) ?? text(args.message) ?? "",
        channel: asChannel(result?.channel) ?? asChannel(args.channel) ?? "imessage",
        delivered: result?.delivered === true,
        until: now + TEXT_FOR_MS,
      },
    };
  }
  if (binding.action === "call") {
    if (result?.ended === true || result?.status === "ended") {
      return { ...state, phone: { mode: "idle" } };
    }
    return {
      ...state,
      phone: {
        mode: "calling",
        to: text(result?.to) ?? text(args.to) ?? text(args.name) ?? "someone",
        until: now + CALL_FOR_MS,
      },
    };
  }
  return {
    ...state,
    ask: {
      question: text(args.question) ?? "Can I help with that?",
      choices: stringList(args.choices),
    },
  };
}

export function text(value: unknown): string | undefined {
  if (typeof value !== "string") return undefined;
  const trimmed = value.trim();
  return trimmed ? trimmed : undefined;
}

function stringList(value: unknown): string[] | undefined {
  if (!Array.isArray(value)) return undefined;
  const items = value.filter((item): item is string => typeof item === "string" && item.trim().length > 0);
  return items.length ? items : undefined;
}

function clamp(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, value));
}
