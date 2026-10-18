import type { RoomState } from "./state";

/** One concise line for the room page. Captions are included here so a second live region is unnecessary. */
export function describeChange(prev: RoomState, next: RoomState): string | null {
  if (!prev.known && !next.known) return null;
  const lines: string[] = [];
  if (prev.lamp.level !== next.lamp.level && next.lamp.level != null) {
    lines.push(`Floor lamp at ${Math.round(next.lamp.level)}%`);
  }
  if (prev.ceiling.level !== next.ceiling.level && next.ceiling.level != null) {
    lines.push(`Ceiling light at ${Math.round(next.ceiling.level)}%`);
  }
  if (prev.blinds.position !== next.blinds.position && next.blinds.position != null) {
    lines.push(`Blinds ${Math.round(next.blinds.position)}% open`);
  }
  const flashWas = prev.flash?.id;
  const flashNow = next.flash;
  if (flashNow && flashWas !== flashNow.id) {
    lines.push(`Lights flashing ${colorName(flashNow.color)}, ${flashNow.pulses} ${flashNow.pulses === 1 ? "pulse" : "pulses"}`);
  }
  if (!prev.speaker.playing && next.speaker.playing) {
    lines.push(next.speaker.track ? `Playing ${next.speaker.track}` : "Music is playing");
  } else if (prev.speaker.playing && !next.speaker.playing && prev.known) {
    lines.push("Music stopped");
  }
  if (prev.phone.delivery !== next.phone.delivery && next.phone.delivery) {
    const who = next.phone.to ? ` to ${next.phone.to}` : "";
    if (next.phone.delivery === "delivered") lines.push(`Message delivered${who}`);
    else if (next.phone.delivery === "accepted") lines.push(`Message submitted${who}`);
    else if (next.phone.delivery === "acknowledged") lines.push(`Message acknowledged${who}`);
    else if (next.phone.delivery === "failed") lines.push("Message failed");
  }
  if ((prev.caption ?? "") !== (next.caption ?? "") && next.caption) {
    lines.push(next.caption);
  }
  const prevAsk = prev.ask?.question ?? "";
  const nextAsk = next.ask?.question ?? "";
  if (prevAsk !== nextAsk && nextAsk) lines.push(nextAsk);
  return lines.length ? lines.join(". ") : null;
}

function colorName(color: string): string {
  const named: Record<string, string> = {
    "#3b82f6": "blue",
    "#ef4444": "red",
    "#f5b942": "amber",
    "#f4f4f5": "white",
    "#22c55e": "green",
  };
  return named[color.toLowerCase()] ?? "blue";
}
