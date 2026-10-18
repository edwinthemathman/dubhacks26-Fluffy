"use client";

import { Canvas } from "@react-three/fiber";
import { Component, useEffect, useState, type ReactNode } from "react";
import { Stage } from "./scene/Stage";
import { designRoom, type RoomState } from "./state";
import type { RoomThemeName } from "./scene/theme";

export type RoomDesignProps = {
  state?: RoomState;
  now?: number;
  theme?: RoomThemeName;
  reducedMotion?: boolean;
  className?: string;
  fallback?: ReactNode;
};

/** The living-room drawing. It takes a visual state and does not open a session. */
export function RoomDesign({ state = designRoom(), now = 0, theme = "light", reducedMotion, className, fallback }: RoomDesignProps) {
  const reduced = useReducedMotion(reducedMotion);
  const [supported] = useState(hasWebGL);

  return (
    <div className={className} style={{ position: "relative", width: "100%", height: "100%", minHeight: 320 }}>
      {supported ? (
        <WebGLBoundary fallback={fallback ?? <DesignFallback />}>
          <div className="h-full w-full [&_canvas]:block [&_canvas]:h-full [&_canvas]:w-full" role="img" aria-label="Living room drawing. Floor lamp, ceiling light, blinds, speaker, and phone.">
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
        fallback ?? <DesignFallback />
      )}
    </div>
  );
}

function DesignFallback() {
  return (
    <div className="flex h-full min-h-[320px] items-center justify-center bg-[#f7f7f5] px-6 text-center text-sm text-neutral-600">
      This browser could not draw the 3D room.
    </div>
  );
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
