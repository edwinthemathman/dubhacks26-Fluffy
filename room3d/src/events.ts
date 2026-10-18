import type { PhoneChannel, RoomEvent, Warmth } from "./state";

/** Turn a harness WebSocket message into a room event. Unknown messages are ignored. */
export function toRoomEvent(raw: unknown): RoomEvent | null {
  if (!raw || typeof raw !== "object") return null;
  const event = raw as Record<string, unknown>;
  const type = event.type;
  if (type === "tool.call") {
    const callId = asString(event.call_id ?? event.callId);
    const tool = asString(event.tool);
    if (!callId || !tool) return null;
    return { type: "tool.call", call_id: callId, tool, args: asRecord(event.args) };
  }
  if (type === "tool.status") {
    const callId = asString(event.call_id ?? event.callId);
    const status = asString(event.status);
    if (!callId || !status) return null;
    const result = asRecord(event.result) ?? asRecord(event.detail);
    return {
      type: "tool.status",
      call_id: callId,
      status,
      detail: typeof event.detail === "string" ? event.detail : undefined,
      result: result && Object.keys(result).length ? result : undefined,
    };
  }
  if (type === "state") return homeState(event);
  if (type === "ask") {
    const question = asString(event.question);
    if (!question) return null;
    const choices = Array.isArray(event.choices)
      ? event.choices.filter((item): item is string => typeof item === "string")
      : undefined;
    return { type: "ask", question, choices };
  }
  if (type === "ask.answer") return { type: "ask.answer" };
  if (type === "message.received") {
    return {
      type: "message.received",
      from: asString(event.from),
      text: asString(event.text),
      preview: asString(event.preview),
      channel: asString(event.channel),
    };
  }
  return null;
}

function homeState(event: Record<string, unknown>): RoomEvent {
  const lamp = asRecord(event.lamp);
  const ceiling = asRecord(event.ceiling);
  const speaker = asRecord(event.speaker);
  const flash = asRecord(event.flash);
  const door = asRecord(event.door);
  const phone = asRecord(event.phone);
  const light = asRecord(event.light);
  return {
    type: "state",
    lamp: lamp
      ? { luminosity: asNumber(lamp.luminosity), warmth: asWarmth(lamp.warmth) }
      : undefined,
    ceiling: ceiling ? { luminosity: asNumber(ceiling.luminosity) } : undefined,
    speaker: speaker
      ? {
          sound: speaker.sound === null ? null : asString(speaker.sound),
          volume: asNumber(speaker.volume),
          playing: typeof speaker.playing === "boolean" ? speaker.playing : undefined,
          track: asString(speaker.track),
          artist: asString(speaker.artist),
        }
      : undefined,
    flash: flash
      ? {
          active: typeof flash.active === "boolean" ? flash.active : undefined,
          color: flash.color === null ? null : asString(flash.color),
          pattern: flash.pattern === null ? null : asString(flash.pattern),
          duration_s: asNumber(flash.duration_s),
        }
      : undefined,
    door: door && typeof door.open === "boolean" ? { open: door.open } : undefined,
    phone: phone
      ? {
          mode: asPhoneMode(phone.mode),
          to: asString(phone.to),
          preview: asString(phone.preview),
          channel: asChannel(phone.channel),
          delivered: typeof phone.delivered === "boolean" ? phone.delivered : undefined,
          until: asNumber(phone.until),
        }
      : undefined,
    light: light
      ? { level: asNumber(light.level), warmth: asWarmth(light.warmth) }
      : undefined,
  };
}

function asNumber(value: unknown): number | undefined {
  return typeof value === "number" && !Number.isNaN(value) ? value : undefined;
}

function asWarmth(value: unknown): Warmth | undefined {
  if (value === "warm" || value === "neutral" || value === "cool") return value;
  return undefined;
}

function asChannel(value: unknown): PhoneChannel | undefined {
  if (value === "imessage" || value === "sms" || value === "whatsapp" || value === "other") return value;
  return undefined;
}

function asPhoneMode(value: unknown): "idle" | "texting" | "calling" | "incoming" | undefined {
  if (value === "idle" || value === "texting" || value === "calling" || value === "incoming") return value;
  return undefined;
}

function asString(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() ? value : undefined;
}

function asRecord(value: unknown): Record<string, unknown> | undefined {
  if (!value || typeof value !== "object" || Array.isArray(value)) return undefined;
  return value as Record<string, unknown>;
}
