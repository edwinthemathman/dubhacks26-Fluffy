"use client";

import { useEffect, useState } from "react";

const SHIMMER_MS = 2400;
const PASSES = 2;
const FADE_MS = 200;
const DONE_HOLD_MS = 700;

/**
 * rotating status. each phrase gets exactly two shimmer passes, then fade,
 * swap, fade in. when `done` flips true it swaps to the done label, holds, then fades.
 */
export default function ThinkingLabel({
  phrases,
  done = false,
  doneLabel = "Done",
  className = "",
}: {
  phrases: string[];
  done?: boolean;
  doneLabel?: string;
  className?: string;
}) {
  const [i, setI] = useState(0);
  const [visible, setVisible] = useState(true);
  const [showDone, setShowDone] = useState(false);

  useEffect(() => {
    if (done) return;
    let swap = 0;
    const tick = window.setInterval(() => {
      setVisible(false);
      swap = window.setTimeout(() => {
        setI((n) => (n + 1) % phrases.length);
        setVisible(true);
      }, FADE_MS);
    }, SHIMMER_MS * PASSES + FADE_MS);
    return () => {
      window.clearInterval(tick);
      window.clearTimeout(swap);
    };
  }, [done, phrases.length]);

  useEffect(() => {
    if (!done) return;
    const t0 = window.setTimeout(() => setVisible(false), 0);
    const a = window.setTimeout(() => {
      setShowDone(true);
      setVisible(true);
    }, FADE_MS);
    const b = window.setTimeout(() => setVisible(false), FADE_MS + DONE_HOLD_MS);
    return () => {
      window.clearTimeout(t0);
      window.clearTimeout(a);
      window.clearTimeout(b);
    };
  }, [done]);

  return (
    <span
      role="status"
      aria-live="polite"
      className={
        "inline-block scale-125 bg-white/85 px-3 tracking-tighter backdrop-blur-sm transition-opacity " +
        (visible ? "opacity-100" : "opacity-0") +
        " " +
        className
      }
      style={{ transitionDuration: `${FADE_MS}ms` }}
    >
      {showDone ? (
        <span className="text-xs font-medium tracking-tight text-neutral-700">
          {doneLabel}
        </span>
      ) : (
        <span
          key={i}
          className="shimmer-text text-xs font-medium tracking-tight"
          style={{ ["--shimmer-ms" as string]: `${SHIMMER_MS}ms` }}
        >
          {phrases[i % phrases.length]}
        </span>
      )}
    </span>
  );
}
