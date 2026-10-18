"use client";

import { useEffect, useState, type CSSProperties } from "react";
import { MASCOT_MS, mascotCue, spriteFor, type MascotActivity } from "@/lib/fluffy/mascot";
import type { CapturePhase } from "@/lib/media/capture";
import ThinkingLabel from "@/components/ThinkingLabel";
import { useSession } from "@/providers/FluffySessionProvider";

type FluffyMascotProps = {
  activity?: MascotActivity;
  /** Changes when a new confirmed completion should play the success sprite once. */
  playKey?: string;
  /** Historical cards, the sidebar, and error states stay on the PNG. */
  still?: boolean;
  size?: number;
  className?: string;
};

const playedSuccess = new Set<string>();

export function FluffyMascot({ activity = "idle", playKey = "", still = false, size = 44, className }: FluffyMascotProps) {
  const holdStill = still || activity === "static";
  const [releasedKey, setReleasedKey] = useState<string | null>(null);
  const released = activity === "success" && !holdStill && (playedSuccess.has(playKey) || releasedKey === playKey);

  useEffect(() => {
    if (holdStill || activity !== "success" || playedSuccess.has(playKey)) return;
    const timer = window.setTimeout(() => {
      playedSuccess.add(playKey);
      setReleasedKey(playKey);
    }, MASCOT_MS.happy);
    return () => window.clearTimeout(timer);
  }, [activity, holdStill, playKey]);

  const shown = released ? "idle" : spriteFor(activity);
  const png = `/mascot/fluffy-${shown}.png`;
  const gif = `/mascot/fluffy-${shown}.gif`;
  const style: CSSProperties = {
    width: size,
    height: size,
    objectFit: "contain",
    imageRendering: "pixelated",
  };

  return (
    <span aria-hidden className={`inline-flex shrink-0 items-center justify-center ${className ?? ""}`} style={{ width: size, height: size }}>
      {holdStill ? (
        // GIF loops cannot be held on a single frame. Still slots use the PNG.
        // eslint-disable-next-line @next/next/no-img-element
        <img src={png} alt="" width={size} height={size} draggable={false} style={style} />
      ) : (
        <picture>
          <source media="(prefers-reduced-motion: reduce)" srcSet={png} />
          <img key={gif} src={gif} alt="" width={size} height={size} draggable={false} style={style} />
        </picture>
      )}
    </span>
  );
}

export function FluffyLiveMascot() {
  const session = useSession();
  const cue = mascotCue(session.state, session.sensingFacts, {
    greet: session.greeting,
    fault: session.speech.error ?? sensingFault(session.sensing),
  });

  return (
    <div data-bg-clear className="relative z-10 flex min-h-12 items-center justify-center gap-3">
      <FluffyMascot activity={cue.activity} playKey={cue.playKey} size={44} />
      {cue.activity === "working" ? (
        <ThinkingLabel label={cue.status} />
      ) : (
        <p role="status" aria-live="polite" className="max-w-[36rem] text-sm leading-snug text-neutral-600">
          {cue.status}
        </p>
      )}
    </div>
  );
}

function sensingFault(sensing: CapturePhase): string | null {
  if (sensing.camera === "denied") return "Camera permission denied";
  if (sensing.camera === "absent") return "No camera found";
  if (sensing.camera === "error") return sensing.error ?? "Camera unavailable";
  if (sensing.transport === "offline") return "Media transport offline";
  return null;
}
