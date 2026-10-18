"use client";

import { Canvas } from "@react-three/fiber";
import { Component, useEffect, useRef, useState, type ReactNode } from "react";
import { describeChange } from "./announce";
import { Stage } from "./scene/Stage";
import type { RoomState } from "./state";
import type { RoomThemeName } from "./scene/theme";

export type RoomLabels = { canvas?: string };

export type FluffyRoom3DProps = {
  state: RoomState;
  now?: number;
  theme?: RoomThemeName;
  labels?: RoomLabels;
  reducedMotion?: boolean;
  className?: string;
  fallback?: ReactNode;
};

export function FluffyRoom3D({ state, now = 0, theme = "light", labels, reducedMotion, className, fallback }: FluffyRoom3DProps) {
  const reduced = useReducedMotion(reducedMotion);
  const line = useAnnouncement(state);
  const [supported] = useState(hasWebGL);
  const label = !state.known
    ? "Waiting for the home state."
    : labels?.canvas ?? "Living room. The floor lamp, ceiling light, blinds, speaker, and phone show the current home.";

  return (
    <div className={className} style={{ position: "relative", width: "100%", height: "100%", minHeight: 320 }}>
      {supported ? (
        <WebGLBoundary fallback={fallback ?? <RoomFallback state={state} />}>
          <div role="img" aria-label={label} style={{ width: "100%", height: "100%" }}>
            <Canvas
              frameloop="demand"
              dpr={[1, 1.5]}
              gl={{ antialias: true, alpha: false }}
              style={{ width: "100%", height: "100%", display: "block" }}
            >
              <Stage state={state} theme={theme} reduced={reduced} now={now} />
            </Canvas>
          </div>
        </WebGLBoundary>
      ) : (
        fallback ?? <RoomFallback state={state} />
      )}
      <div aria-live="polite" aria-atomic="true" className="fluffy-sr">
        {line}
      </div>
      <style>{ROOM_CSS}</style>
    </div>
  );
}

function RoomFallback({ state }: { state: RoomState }) {
  return (
    <div className="flex h-full min-h-[320px] items-center justify-center rounded-2xl border border-neutral-200 bg-neutral-50 px-6 text-center text-sm text-neutral-600">
      {state.known ? "This browser could not draw the 3D room. Device details stay in the status card." : "Waiting for the home state. The 3D view is unavailable in this browser."}
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

function hasWebGL(): boolean {
  try {
    const canvas = document.createElement("canvas");
    return Boolean(canvas.getContext("webgl2") || canvas.getContext("webgl"));
  } catch {
    return false;
  }
}

class WebGLBoundary extends Component<{ children: ReactNode; fallback: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError(): { failed: boolean } {
    return { failed: true };
  }
  render() {
    return this.state.failed ? this.props.fallback : this.props.children;
  }
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
