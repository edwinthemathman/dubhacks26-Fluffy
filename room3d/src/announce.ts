import type { RoomObject, RoomState } from "./state";

const OBJECTS: RoomObject[] = ["light", "door", "speaker", "phone"];

const WAITING: Record<RoomObject, string> = {
  light: "Waiting for a yes before changing the lamp",
  door: "Waiting for a yes before moving the door",
  speaker: "Waiting for a yes before changing the music",
  phone: "Waiting for a yes before using the phone",
};

const STAYED: Record<RoomObject, string> = {
  light: "The lamp stayed the same",
  door: "The door stayed the same",
  speaker: "The music stayed the same",
  phone: "The phone stayed the same",
};

/** Words for a screen reader. One line per visible change. */
export function describeChange(prev: RoomState, next: RoomState): string | null {
  const lines: string[] = [];
  if (prev.light.level !== next.light.level) {
    const verb = next.light.level < prev.light.level ? "dimmed" : "brightened";
    lines.push(`Lamp ${verb} to ${Math.round(next.light.level)}%`);
  }
  if (prev.light.warmth !== next.light.warmth) {
    lines.push(`Lamp warmth set to ${next.light.warmth}`);
  }
  const flashWas = Boolean(prev.light.flash);
  const flashNow = Boolean(next.light.flash);
  if (!flashWas && flashNow && next.light.flash) {
    lines.push(`Lights flashing ${colorName(next.light.flash.color)}`);
  }
  if (prev.door.open !== next.door.open) {
    lines.push(next.door.open ? "Door opened" : "Door closed");
  }
  if (!prev.speaker.playing && next.speaker.playing) {
    lines.push(playingLine(next));
  } else if (prev.speaker.playing && !next.speaker.playing) {
    lines.push("Music stopped");
  } else if (next.speaker.playing && (prev.speaker.track !== next.speaker.track || prev.speaker.artist !== next.speaker.artist)) {
    lines.push(playingLine(next));
  }
  if (prev.phone.mode !== "texting" && next.phone.mode === "texting") {
    const who = next.phone.to ?? "someone";
    lines.push(next.phone.delivered ? `Text delivered to ${who}` : `Text sent to ${who}`);
  } else if (prev.phone.mode === "texting" && next.phone.mode === "texting" && !prev.phone.delivered && next.phone.delivered) {
    lines.push(`Text delivered to ${next.phone.to ?? "someone"}`);
  }
  if (prev.phone.mode !== "incoming" && next.phone.mode === "incoming") {
    const who = next.phone.to ?? "someone";
    lines.push(next.phone.preview ? `Reply from ${who}: ${next.phone.preview}` : `Reply from ${who}`);
  }
  if (prev.phone.mode !== "calling" && next.phone.mode === "calling") {
    lines.push(`Calling ${next.phone.to ?? "someone"}`);
  }
  const prevAsk = prev.ask?.question ?? "";
  const nextAsk = next.ask?.question ?? "";
  if (prevAsk !== nextAsk && nextAsk) lines.push(`Fluffy asks: ${nextAsk}`);
  for (const object of OBJECTS) {
    if (prev.pending[object] === next.pending[object]) continue;
    if (next.pending[object] === "asking") lines.push(WAITING[object]);
    if (next.pending[object] === "failed") lines.push(STAYED[object]);
  }
  return lines.length ? lines.join(". ") : null;
}

function playingLine(state: RoomState): string {
  const track = state.speaker.track ?? "music";
  return state.speaker.artist ? `Playing ${track}, ${state.speaker.artist}` : `Playing ${track}`;
}

function colorName(color: string): string {
  const named: Record<string, string> = {
    "#3b82f6": "blue",
    "#ef4444": "red",
    "#f5b942": "amber",
    "#f4f8fd": "white",
    "#22c55e": "green",
  };
  return named[color.toLowerCase()] ?? color;
}
