import { Bell, Eye, MessageCircle, Shield, Volume2 } from "lucide-react";
import { useEffect, useState } from "react";
import { TopBar } from "../../components/shared/TopBar";
import { FluffyAvatar } from "../../components/shared/FluffyAvatar";
import { ProfileBadge } from "../../components/shared/ProfileBadge";
import { StatusPill } from "../../components/shared/StatusPill";
import { deriveAvatar, useFluffyStream } from "../../stream/FluffyStream";
import type { Turn, TurnKind } from "../../stream/events";
import { clip, formatArgs, formatMs } from "../../stream/format";
import { SCENES } from "../../stream/timelines";
import styles from "./Mind.module.css";

const KIND: Record<TurnKind, { label: string; icon: typeof Eye }> = {
  spoken: { label: "Spoken", icon: MessageCircle },
  sound: { label: "Sound", icon: Bell },
  offer: { label: "Offer", icon: Eye },
  safety: { label: "Safety", icon: Shield },
};

const STEPS = [
  { id: "checkin1", label: "Check-in 1" },
  { id: "checkin2", label: "Check-in 2" },
  { id: "alerted", label: "Alerted" },
  { id: "acked", label: "Acknowledged" },
] as const;

export function Mind() {
  const { snapshot, scene, send, replay } = useFluffyStream();
  const [raw, setRaw] = useState(false);
  const [follow, setFollow] = useState(true);
  const avatar = deriveAvatar(snapshot);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (target && (target.tagName === "INPUT" || target.tagName === "TEXTAREA")) return;
      if (event.key === "r" || event.key === "R") setRaw((value) => !value);
      if (event.key === " ") {
        event.preventDefault();
        setFollow((value) => !value);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  useEffect(() => {
    if (follow) window.scrollTo({ top: 0 });
  }, [follow, snapshot.turns[0]?.id, snapshot.turns[0]?.lines.length]);

  const hear = snapshot.senses.sound
    ? snapshot.senses.sound
    : snapshot.senses.hear || "quiet";

  return (
    <>
      <TopBar />
      <main id="main" className={`page ${styles.mind}`}>
        <header className={`card ${styles.header}`}>
          <FluffyAvatar state={avatar} size={92} />
          <div>
            <div className={styles.titleRow}>
              <h1 className="screen-title">Fluffy’s mind</h1>
              <ProfileBadge profile={snapshot.profile} />
              <span className="pill quiet">{scene ? "Recorded run" : snapshot.link === "live" ? "Live" : "Waiting"}</span>
            </div>
            <p className={styles.senses}>
              <Eye size={16} aria-hidden="true" />
              {snapshot.senses.chips.length ? snapshot.senses.chips.join(" · ") : "watching the room"}
            </p>
            <p className={styles.senses}>
              <Bell size={16} aria-hidden="true" />
              {hear}
            </p>
            {snapshot.senses.tags.length ? (
              <p className={styles.senses}>Tagged: {snapshot.senses.tags.map((tag) => `${tag.name} · ${tag.where}`).join(" · ")}</p>
            ) : null}
          </div>
          <div className={styles.controls}>
            <div className="segment" role="group" aria-label="Scenes">
              {SCENES.map((item) => (
                <button
                  key={item.id}
                  type="button"
                  aria-pressed={scene === item.id}
                  onClick={() => send({ type: "mock.play", scene: item.id })}
                >
                  {item.label}
                </button>
              ))}
            </div>
            <button type="button" className="text-button" onClick={replay} disabled={!scene}>
              Replay
            </button>
          </div>
        </header>

        <div aria-live="polite">
          {snapshot.turns.length === 0 && (
            <section className={`card ${styles.empty}`}>
              <FluffyAvatar state="idle" size={72} />
              <p>Fluffy is quiet. Nothing needed yet.</p>
            </section>
          )}
          {snapshot.turns.map((turn) => (
            <TurnCard key={turn.id} turn={turn} raw={raw} safety={snapshot.safety} />
          ))}
        </div>
        <p className={styles.hint}>Space pauses follow. R shows raw notes. {follow ? "Following." : "Paused."}</p>
      </main>
    </>
  );
}

function TurnCard({ turn, raw, safety }: { turn: Turn; raw: boolean; safety: string }) {
  const kind = KIND[turn.kind];
  const Icon = kind.icon;
  const faded = turn.outcome === "nothing";
  return (
    <article className={`card ${styles.turn} ${turn.kind === "safety" ? styles.safety : ""} ${faded ? styles.faded : ""}`}>
      <header className={styles.turnTop}>
        <span className="pill">
          <Icon size={14} aria-hidden="true" />
          {kind.label}
        </span>
        <strong>{turn.trigger}</strong>
        <span className={styles.grow} />
        {turn.ms != null && <span className="pill quiet">{formatMs(turn.ms)}</span>}
        {turn.outcome && <span className="pill quiet">{turn.outcome === "nothing" ? "nothing needed" : turn.outcome}</span>}
      </header>
      <ul className={styles.lines}>
        {turn.lines.map((line, index) => {
          if (line.type === "heard") {
            return <li key={index}>heard · {clip(line.text, 80)}</li>;
          }
          if (line.type === "think") {
            return (
              <li key={index} className={styles.think} title={line.text}>
                think · {clip(line.text)}
              </li>
            );
          }
          if (line.type === "say") {
            return (
              <li key={index}>
                {line.voice ? <Volume2 size={14} aria-label="Spoken aloud" /> : null} say · {line.text}
              </li>
            );
          }
          if (line.type === "ask") {
            const answer = line.answer
              ? ` → ${line.answer.by}${line.answer.choice ? ` ${line.answer.choice}` : ""}${line.answer.by === "none" ? " ✗" : " ✓"}`
              : "";
            return (
              <li key={index}>
                ? “{line.question}”{answer}
              </li>
            );
          }
          const tool = turn.tools[line.callId];
          if (!tool) return null;
          return (
            <li key={line.callId} className={tool.kind === "meta" ? styles.meta : undefined}>
              <span className="mono">{tool.tool}</span>
              <span className="muted">{formatArgs(tool.args)}</span>
              <span className={styles.why} title={tool.why}>why: {clip(tool.why)}</span>
              <StatusPill status={tool.status} />
            </li>
          );
        })}
      </ul>
      {turn.kind === "safety" && (
        <ol className={styles.steps}>
          {STEPS.map((step) => (
            <li key={step.id} aria-current={safety === step.id ? "step" : undefined}>
              {step.label}
            </li>
          ))}
        </ol>
      )}
      {raw && turn.raw && <p className={styles.raw}>{turn.raw}</p>}
    </article>
  );
}
