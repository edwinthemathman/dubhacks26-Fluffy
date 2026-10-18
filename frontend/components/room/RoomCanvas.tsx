"use client";

import { FluffyRoom3D } from "@/features/room/FluffyRoom3D";
import { projectHomeToRoom } from "@/features/room/projectHomeToRoom";
import { percentLabel } from "@/lib/fluffy/levels";
import type { ActionModel } from "@/lib/fluffy/types";
import { useSession } from "@/providers/FluffySessionProvider";

export default function RoomCanvas() {
  const session = useSession();
  const { state, now } = session;
  const turn = [...state.turns].reverse().find((item) => item.open) ?? state.turns[state.turns.length - 1];
  const actions = (turn?.actionIds ?? []).map((id) => state.actions[id]).filter((item): item is ActionModel => item != null);
  const room = projectHomeToRoom({ home: state.home, actions, question: state.question, now });
  const lights = Object.values(state.home.lights);
  const blinds = Object.values(state.home.blinds);

  return (
    <div className="mx-auto flex w-full max-w-5xl flex-col gap-4 px-6 py-8 md:px-8">
      <header>
        <h1 className="font-semibold tracking-tighter" style={{ fontSize: "1.5rem" }}>
          Room
        </h1>
        <p className="mt-1 text-sm text-neutral-500">The same session as the chat. This view does not open its own connection.</p>
      </header>
      <div className="grid items-start gap-4 lg:grid-cols-[minmax(0,1.4fr)_minmax(240px,0.8fr)]">
        <div className="h-[320px] overflow-hidden rounded-[22px] border border-neutral-200 bg-[#f7f7f5] md:h-[480px]">
          <FluffyRoom3D state={room} now={now} className="h-full" />
        </div>
        <section className="rounded-[22px] border border-neutral-200 bg-white p-4 text-sm leading-relaxed">
          <h2 className="text-[13px] font-semibold text-neutral-400">Home</h2>
          {!state.home.known ? <p className="mt-3 text-neutral-500">Waiting for a home snapshot.</p> : null}
          <dl className="mt-3 flex flex-col gap-2">
            {lights.length === 0 ? <Fact label="Lights" value="Unknown" /> : lights.map((light) => (
              <Fact key={light.id} label={labelFor(light.id)} value={light.luminosity == null ? "Unknown" : percentLabel(light.luminosity)} />
            ))}
            {blinds.length === 0 ? <Fact label="Blinds" value="Unknown" /> : blinds.map((blind) => (
              <Fact key={blind.id} label="Blinds" value={blind.position == null ? "Unknown" : `${percentLabel(blind.position)} open`} />
            ))}
            <Fact
              label="Speaker"
              value={speakerLine(state.home.speaker.playback, state.home.speaker.confirmed, state.home.speaker.sound, state.home.speaker.volume)}
            />
            <Fact label="Message" value={messageLine(state.home.caregiver)} />
            {state.home.caption ? <Fact label="Caption" value={state.home.caption.text} /> : null}
            {state.incident ? <Fact label="Incident" value={`${state.incident.status}: ${state.incident.summary}`} /> : null}
            {state.question ? <Fact label="Question" value={state.question.text} /> : null}
          </dl>
          {turn?.recorded ? <p className="mt-4 text-xs text-neutral-500">The latest chat result is a recorded scene. It is not shown as the live home.</p> : null}
          {state.notice ? <p className="mt-4 text-xs text-neutral-500">{state.notice}</p> : null}
        </section>
      </div>
    </div>
  );
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-start justify-between gap-3 border-b border-neutral-100 pb-2">
      <dt className="text-neutral-400">{label}</dt>
      <dd className="text-right text-neutral-800">{value}</dd>
    </div>
  );
}

function labelFor(id: string): string {
  if (id === "lamp.living_floor") return "Floor lamp";
  if (id === "light.living_ceiling") return "Ceiling light";
  return id;
}

function speakerLine(playback: string, confirmed: boolean, sound: string | null, volume: number | null): string {
  const volumeText = volume == null ? "" : volume === 0 ? ", muted" : `, volume ${volume}`;
  if (!confirmed || playback === "unknown") return sound ? `${sound} unverified` : "Unknown";
  if (playback === "playing") return `${sound ?? "Playing"}${volumeText}`;
  if (playback === "pending") return "Pending";
  if (playback === "failed") return "Could not verify playback";
  return "Stopped";
}

function messageLine(alert: { delivery: string; recipient: string | null; message: string } | null): string {
  if (!alert) return "None";
  const who = alert.recipient ? ` to ${alert.recipient}` : "";
  if (alert.delivery === "delivered") return `Delivered${who}`;
  if (alert.delivery === "accepted") return `Submitted${who}`;
  if (alert.delivery === "acknowledged") return `Acknowledged${who}`;
  if (alert.delivery === "failed") return "Failed";
  return alert.message || "Unknown";
}
