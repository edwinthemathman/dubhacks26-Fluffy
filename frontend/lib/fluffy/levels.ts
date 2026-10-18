import type { Warmth } from "./types";

/** Execution contract scale is 0–100. A value of 1 is 1%, not full brightness. */
export function asLevel(value: unknown): number | null {
  if (typeof value !== "number" || !Number.isFinite(value)) return null;
  if (value < 0 || value > 100) return null;
  return value;
}

export function asWarmth(value: unknown): Warmth | null {
  if (value === "warm" || value === "neutral" || value === "cool") return value;
  return null;
}

export function finiteNumber(value: unknown): number | null {
  if (typeof value !== "number" || !Number.isFinite(value)) return null;
  return value;
}

export function asRecord(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  return value as Record<string, unknown>;
}

export function asString(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  return trimmed ? trimmed : null;
}

export function percentLabel(value: number): string {
  const rounded = Math.round(value * 10) / 10;
  return Number.isInteger(rounded) ? `${rounded}%` : `${rounded.toFixed(1)}%`;
}

const NAMED_COLORS: Record<string, string> = {
  blue: "#3b82f6",
  red: "#ef4444",
  amber: "#f5b942",
  white: "#f4f4f5",
  green: "#22c55e",
};

export function cssColor(input: unknown, fallback = "#3b82f6"): string {
  const text = asString(input);
  if (!text) return fallback;
  const named = NAMED_COLORS[text.toLowerCase()];
  if (named) return named;
  if (/^#([0-9a-f]{3}|[0-9a-f]{6})$/i.test(text)) return text;
  return fallback;
}

export function pulsesFor(pattern: unknown, explicit?: unknown): number {
  const count = finiteNumber(explicit);
  if (count != null && count > 0 && count <= 12) return Math.round(count);
  if (pattern === "pulse1") return 1;
  if (pattern === "pulse2") return 2;
  if (pattern === "pulse3") return 3;
  if (pattern === "strobe") return 6;
  return 1;
}

export function parseTime(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value !== "string" || !value.trim()) return null;
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? parsed : null;
}

export const LAMP_ID = "lamp.living_floor";
export const CEILING_ID = "light.living_ceiling";
export const BLINDS_ID = "blinds.living_window";
export const FRONT_DOOR_ID = "front_door";
export const LIVING_ROOM_ID = "living_room";

export function lightLabel(id: string): string {
  if (id === LAMP_ID) return "Floor lamp";
  if (id === CEILING_ID) return "Ceiling light";
  return id.replaceAll(".", " ").replaceAll("_", " ");
}
