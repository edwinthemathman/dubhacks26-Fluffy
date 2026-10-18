export function clip(text: string, max = 48): string {
  const trimmed = text.trim();
  if (trimmed.length <= max) return trimmed;
  return `${trimmed.slice(0, max - 1)}…`;
}

const LIGHT_NAMES: Record<string, string> = {
  "lamp.living_floor": "floor lamp",
  "light.living_ceiling": "ceiling",
};

export function formatArgs(args: Record<string, unknown>): string {
  const parts: string[] = [];
  for (const [key, value] of Object.entries(args)) {
    if (value == null || key === "room" || key === "confidence" || key === "reason") continue;
    if (key === "luminosity" || key === "volume" || key === "position") {
      parts.push(`${value}%`);
      continue;
    }
    if (key === "light") {
      parts.push(LIGHT_NAMES[String(value)] ?? String(value));
      continue;
    }
    if (key === "text" || key === "message") {
      const raw = String(value);
      parts.push(raw.length > 28 ? `${raw.slice(0, 27)}…` : raw);
      continue;
    }
    parts.push(String(value));
  }
  return parts.join(" · ");
}

export function formatMs(ms: number): string {
  return `${(ms / 1000).toFixed(1)}s`;
}

export function profileLabel(profile: string | null): string {
  if (profile === "blind") return "Blind user";
  if (profile === "deaf") return "Deaf user";
  if (profile === "speech_cognitive") return "Speech / cognitive";
  return "No profile";
}
