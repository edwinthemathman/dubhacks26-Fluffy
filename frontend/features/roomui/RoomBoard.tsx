"use client";

import { actionsForRoom, projectHomeToRoom } from "@/features/room/projectHomeToRoom";
import { dedupeTurns } from "@/lib/fluffy/reducer";
import { useSession } from "@/providers/FluffySessionProvider";
import { RoomDesign } from "./RoomDesign";
import { designRoom, type RoomState } from "./state";

const START = designRoom();

/** The drawing, plus the confirmed state of each part. Pending work does not change a number. */
export function RoomBoard() {
  const session = useSession();
  const state = dedupeTurns(session.state);
  const actions = actionsForRoom(state, session.now);
  const executed = projectHomeToRoom({
    home: state.home,
    actions,
    question: state.question,
    now: session.now,
  });
  const room = executed.known ? executed : START;
  const waiting = !executed.known;

  return (
    <div className="flex h-[calc(100dvh-3.5rem)] flex-col bg-[#f7f7f5] md:h-screen md:flex-row">
      <div className="min-h-[320px] min-w-0 flex-1">
        <RoomDesign state={room} now={session.now} className="h-full" />
      </div>
      <aside className="w-full shrink-0 border-t border-neutral-200 bg-white px-5 py-4 md:w-64 md:border-l md:border-t-0">
        <h1 className="text-sm font-semibold tracking-tight text-neutral-900">Room</h1>
        <p className="mt-1 text-xs leading-relaxed text-neutral-500">
          {waiting ? "Waiting for a decision." : room.caption || "The room as it is."}
        </p>
        <dl className="mt-4 flex flex-col gap-2 text-sm">
          <Fact label="Floor lamp" value={lightLine(room.lamp.level, room.lamp.warmth, room.pending.lamp)} />
          <Fact label="Ceiling" value={lightLine(room.ceiling.level, room.ceiling.warmth, room.pending.ceiling)} />
          <Fact label="Blinds" value={blindsLine(room.blinds.position, room.pending.blinds)} />
          <Fact label="Door" value={doorLine(room.door.open, room.pending.door)} />
          <Fact label="Speaker" value={speakerLine(room)} />
          <Fact label="Phone" value={phoneLine(room)} />
        </dl>
      </aside>
    </div>
  );
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline justify-between gap-3 border-b border-neutral-100 pb-2">
      <dt className="text-neutral-400">{label}</dt>
      <dd className="text-right text-neutral-800">{value}</dd>
    </div>
  );
}

function lightLine(level: number | null, warmth: string | null, pending?: string): string {
  if (level == null) return mark("Unknown", pending);
  const warmthText = warmth ? ` ${warmth}` : "";
  return mark(`${Math.round(level)}%${warmthText}`, pending);
}

function blindsLine(position: number | null, pending?: string): string {
  if (position == null) return mark("Unknown", pending);
  if (position <= 0) return mark("Closed", pending);
  if (position >= 100) return mark("Open", pending);
  return mark(`${Math.round(position)}% open`, pending);
}

function doorLine(open: boolean | null, pending?: string): string {
  if (open == null) return mark("Unknown", pending);
  return mark(open ? "Open" : "Closed", pending);
}

function speakerLine(room: RoomState): string {
  if (room.speaker.pending) return "Waiting";
  if (room.pending.speaker) return mark(room.speaker.playing ? "Playing" : "Quiet", room.pending.speaker);
  if (!room.speaker.playing) return "Quiet";
  const name = room.speaker.track ?? "Playing";
  const volume = room.speaker.volume == null ? "" : ` ${Math.round(room.speaker.volume)}%`;
  return `${name}${volume}`;
}

function phoneLine(room: RoomState): string {
  if (room.phone.mode === "idle") return room.phone.delivery === "delivered" ? "Delivered" : "Idle";
  if (room.phone.mode === "incoming") return room.phone.to ? `Call ${room.phone.to}` : "Call";
  return room.phone.to ? `Text ${room.phone.to}` : "Text";
}

function mark(value: string, pending?: string): string {
  if (pending === "failed") return `${value} · failed`;
  if (pending) return `${value} · waiting`;
  return value;
}
