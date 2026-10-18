"use client";

import { useEffect, useState } from "react";

const FADE_MS = 200;

/**
 * Shows the current known stage. The shimmer does not advance execution.
 * An empty label renders nothing, so a finished turn cannot leave a stale "Done".
 */
export default function ThinkingLabel({
  label,
  className = "",
}: {
  label: string;
  className?: string;
}) {
  const [shown, setShown] = useState(label);
  const [visible, setVisible] = useState(Boolean(label));

  useEffect(() => {
    if (label === shown) return;
    const hide = window.setTimeout(() => setVisible(false), 0);
    const swap = window.setTimeout(() => {
      setShown(label);
      setVisible(Boolean(label));
    }, FADE_MS);
    return () => {
      window.clearTimeout(hide);
      window.clearTimeout(swap);
    };
  }, [label, shown]);

  if (!shown) return null;

  return (
    <span
      role="status"
      aria-live="polite"
      className={
        "inline-block max-w-[36rem] bg-white/85 px-3 text-center tracking-tighter backdrop-blur-sm transition-opacity " +
        (visible ? "opacity-100" : "opacity-0") +
        " " +
        className
      }
      style={{ transitionDuration: `${FADE_MS}ms` }}
    >
      <span
        key={shown}
        className="shimmer-text text-xs font-medium tracking-tight"
        style={{ ["--shimmer-ms" as string]: "2400ms" }}
      >
        {shown}
      </span>
    </span>
  );
}
