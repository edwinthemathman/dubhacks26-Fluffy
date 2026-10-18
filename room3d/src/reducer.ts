import type { RoomEvent, RoomObject, RoomState } from "./state";
import { applyTool, asLevel, asWarmth, cssColor, lookupTool, resultFailed, text, TEXT_FOR_MS } from "./toolMap";

const FAIL_MS = 700;
const OBJECTS: RoomObject[] = ["light", "door", "speaker", "phone"];

export function roomReducer(state: RoomState, event: RoomEvent): RoomState {
  if (event.type === "tool.call") {
    if (!event.tool) return state;
    return remember(state, event.call_id, event.tool, event.args ?? {});
  }
  if (event.type === "tool.status") {
    return onStatus(state, event);
  }
  if (event.type === "state") return onHomeState(state, event);
  if (event.type === "ask") {
    const question = event.question.trim();
    if (!question) return state;
    return { ...state, ask: { question, choices: event.choices } };
  }
  if (event.type === "ask.answer") {
    if (state.ask == null) return state;
    return { ...state, ask: null };
  }
  if (event.type === "message.received") {
    const preview = text(event.text) ?? text(event.preview) ?? "";
    const now = event.now ?? Date.now();
    return {
      ...state,
      phone: {
        mode: "incoming",
        to: text(event.from) ?? "someone",
        preview,
        channel: event.channel === "sms" || event.channel === "whatsapp" || event.channel === "imessage" || event.channel === "other"
          ? event.channel
          : "imessage",
        until: now + TEXT_FOR_MS,
      },
    };
  }
  if (event.type === "tick") return onTick(state, event.now);
  return state;
}

function onStatus(state: RoomState, event: Extract<RoomEvent, { type: "tool.status" }>): RoomState {
  const call = state.calls[event.call_id];
  if (!call) return state;
  const binding = lookupTool(call.tool);
  if (!binding) return state;
  const now = event.now ?? Date.now();
  const status = event.status;
  if (status === "proposed" || status === "approved") return state;
  if (status === "asking" || status === "repeated") {
    if (binding.target === "ask") return applyTool(state, binding, call.args, undefined, now) ?? state;
    return {
      ...state,
      pending: { ...state.pending, [binding.target]: "asking" },
    };
  }
  if (status === "rejected" || status === "declined" || status === "failed") {
    if (binding.target === "ask") return state.ask ? { ...state, ask: null } : state;
    return fail(state, binding.target, now);
  }
  if (status !== "done") return state;
  if (resultFailed(event.result)) {
    if (binding.target === "ask") return state;
    return fail(state, binding.target, now);
  }
  const next = applyTool(state, binding, call.args, event.result, now);
  if (next == null) {
    if (binding.target === "ask") return state;
    return fail(state, binding.target, now);
  }
  if (binding.target === "ask") return next;
  const pending = { ...next.pending };
  delete pending[binding.target];
  return { ...next, pending };
}

function onHomeState(state: RoomState, event: Extract<RoomEvent, { type: "state" }>): RoomState {
  let next = state;
  const now = event.now ?? Date.now();
  if (event.light || event.lamp || event.ceiling) {
    const level = asLevel(event.light?.level ?? event.lamp?.luminosity ?? event.ceiling?.luminosity);
    const warmth = asWarmth(event.light?.warmth ?? event.lamp?.warmth);
    next = {
      ...next,
      light: {
        ...next.light,
        ...(level == null ? {} : { level }),
        ...(warmth ? { warmth } : {}),
      },
    };
  }
  if (event.flash) {
    if (event.flash.active && event.flash.color) {
      const seconds = event.flash.duration_s ?? 3;
      next = {
        ...next,
        light: { ...next.light, flash: { color: cssColor(event.flash.color), until: now + seconds * 1000 } },
      };
    } else if (event.flash.active === false) {
      const light = { ...next.light };
      delete light.flash;
      next = { ...next, light };
    }
  }
  if (event.door && typeof event.door.open === "boolean") {
    next = { ...next, door: { open: event.door.open } };
  }
  if (event.speaker) {
    const sound = event.speaker.sound;
    const playing =
      event.speaker.playing ??
      (typeof sound === "string" && sound.length > 0 && (event.speaker.volume ?? 1) > 0);
    next = {
      ...next,
      speaker: playing
        ? {
            playing: true,
            track: event.speaker.track ?? (typeof sound === "string" ? sound : next.speaker.track),
            artist: event.speaker.artist ?? next.speaker.artist,
          }
        : { playing: false },
    };
  }
  if (event.phone) {
    next = { ...next, phone: { ...next.phone, ...event.phone } };
  }
  if (event.light || event.lamp || event.ceiling || event.flash) next = settle(next, "light");
  if (event.door) next = settle(next, "door");
  if (event.speaker) next = settle(next, "speaker");
  if (event.phone) next = settle(next, "phone");
  return next;
}

function settle(state: RoomState, object: RoomObject): RoomState {
  if (state.pending[object] == null && state.failedAt[object] == null) return state;
  const pending = { ...state.pending };
  const failedAt = { ...state.failedAt };
  delete pending[object];
  delete failedAt[object];
  return { ...state, pending, failedAt };
}

function onTick(state: RoomState, now: number): RoomState {
  let next = state;
  if (state.light.flash && state.light.flash.until <= now) {
    const light = { ...next.light };
    delete light.flash;
    next = { ...next, light };
  }
  if (state.phone.until != null && state.phone.until <= now && state.phone.mode !== "idle") {
    next = { ...next, phone: { mode: "idle" } };
  }
  let pending = next.pending;
  let failedAt = next.failedAt;
  for (const object of OBJECTS) {
    if (pending[object] !== "failed") continue;
    const at = failedAt[object] ?? 0;
    if (now - at < FAIL_MS) continue;
    pending = { ...pending };
    failedAt = { ...failedAt };
    delete pending[object];
    delete failedAt[object];
  }
  if (pending !== next.pending || failedAt !== next.failedAt) {
    next = { ...next, pending, failedAt };
  }
  return next;
}

function fail(state: RoomState, object: RoomObject, now: number): RoomState {
  return {
    ...state,
    pending: { ...state.pending, [object]: "failed" },
    failedAt: { ...state.failedAt, [object]: now },
  };
}

function remember(state: RoomState, callId: string, tool: string, args: Record<string, unknown>): RoomState {
  const calls = { ...state.calls, [callId]: { tool, args } };
  const ids = Object.keys(calls);
  while (ids.length > 40) {
    const oldest = ids.shift();
    if (oldest) delete calls[oldest];
  }
  return { ...state, calls };
}
