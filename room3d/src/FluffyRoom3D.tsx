"use client";

import { Canvas } from "@react-three/fiber";
import { useEffect, useRef, useState } from "react";
import { describeChange } from "./announce";
import { Stage } from "./scene/Stage";
import type { RoomState } from "./state";
import type { RoomThemeName } from "./scene/theme";

export type RoomLabels = {
  canvas?: string;
};

export type FluffyRoom3DProps = {
  state: RoomState;
  size?: "sm" | "md" | "lg";
  theme?: RoomThemeName;
  labels?: RoomLabels;
  reducedMotion?: boolean;
  className?: string;
};

const HEIGHT = { sm: 220, md: 320, lg: 440 };

export function FluffyRoom3D({
  state,
  size = "sm",
  theme = "light",
  labels,
  reducedMotion,
  className,
}: FluffyRoom3DProps) {
  const reduced = useReducedMotion(reducedMotion);
  const line = useAnnouncement(state);
  const label =
    labels?.canvas ??
    "Cut-away living room. The lamp, door, speaker, and phone show what Fluffy is doing.";

  return (
    <div className={className} style={{ position: "relative", width: "100%", height: "100%", minHeight: HEIGHT[size] }}>
      <div role="img" aria-label={label} style={{ width: "100%", height: "100%" }}>
        <Canvas
          frameloop="demand"
          dpr={[1, 2]}
          gl={{ antialias: true, alpha: false }}
          style={{ width: "100%", height: "100%", display: "block" }}
        >
          <Stage state={state} theme={theme} reduced={reduced} />
        </Canvas>
      </div>
      <div aria-live="polite" aria-atomic="true" className="fluffy-sr">
        {line}
      </div>
      <style>{ROOM_CSS}</style>
    </div>
  );
}

function useAnnouncement(state: RoomState): string {
  const prev = useRef(state);
  const [line, setLine] = useState("");
  useEffect(() => {
    const said = describeChange(prev.current, state);
    prev.current = state;
    if (said) setLine(said);
  }, [state]);
  return line;
}

function useReducedMotion(forced: boolean | undefined): boolean {
  const [system, setSystem] = useState(false);
  useEffect(() => {
    const media = window.matchMedia("(prefers-reduced-motion: reduce)");
    const apply = () => setSystem(media.matches);
    apply();
    media.addEventListener("change", apply);
    return () => media.removeEventListener("change", apply);
  }, []);
  return forced ?? system;
}

const ROOM_CSS = `
.fluffy-sr {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}
@keyframes fluffy-note {
  from { transform: translateY(4px); opacity: 0.15; }
  to { transform: translateY(-18px); opacity: 0; }
}
@media (prefers-reduced-motion: reduce) {
  @keyframes fluffy-note { from, to { transform: none; opacity: 1; } }
}
`;
