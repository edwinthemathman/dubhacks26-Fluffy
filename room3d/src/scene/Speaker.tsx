import { Html } from "@react-three/drei";
import { useRef } from "react";
import { useFrame, useThree } from "@react-three/fiber";
import type { Mesh } from "three";
import type { PendingState, RoomState } from "../state";
import { useAct } from "./motion";
import type { ThemeColors } from "./theme";

const BASE_X = 1.25;

export function Speaker({
  speaker,
  pending,
  colors,
  reduced,
}: {
  speaker: RoomState["speaker"];
  pending?: PendingState;
  colors: ThemeColors;
  reduced: boolean;
}) {
  const { group, glow } = useAct(BASE_X, pending, reduced, colors.fail, colors.outline);
  const ring = useRef<Mesh>(null);
  const { invalidate } = useThree();
  const label = speaker.artist ? `${speaker.track ?? "Music"}, ${speaker.artist}` : speaker.track ?? "Music";

  useFrame(() => {
    if (!speaker.playing || !ring.current) return;
    const scale = reduced ? 1 : 1 + Math.sin(performance.now() / 280) * 0.08;
    ring.current.scale.setScalar(scale);
    const material = ring.current.material;
    if (!Array.isArray(material) && "emissiveIntensity" in material) {
      material.emissiveIntensity = reduced ? 0.8 : 0.5 + Math.abs(Math.sin(performance.now() / 280)) * 0.8;
    }
    invalidate();
  });

  return (
    <group ref={group} position={[BASE_X, 0, 0.7]}>
      <mesh position={[0, 0.28, 0]}>
        <boxGeometry args={[0.46, 0.56, 0.28]} />
        <meshStandardMaterial color={colors.speaker} />
      </mesh>
      <mesh ref={ring} position={[0, 0.32, 0.15]} rotation={[Math.PI / 2, 0, 0]}>
        <torusGeometry args={[0.12, 0.018, 10, 28]} />
        <meshStandardMaterial color={colors.note} emissive={colors.note} emissiveIntensity={speaker.playing ? 0.8 : 0.05} />
      </mesh>
      <mesh position={[0, 0.12, 0.15]}>
        <circleGeometry args={[0.05, 16]} />
        <meshStandardMaterial color={colors.metal} />
      </mesh>
      <mesh position={[0, 0.3, 0.2]}>
        <boxGeometry args={[0.7, 0.8, 0.02]} />
        <meshBasicMaterial ref={glow} transparent opacity={0} depthWrite={false} color={colors.outline} />
      </mesh>
      {speaker.playing ? (
        <Html position={[0, 0.78, 0]} center zIndexRange={[12, 0]} style={{ pointerEvents: "none" }}>
          <div
            style={{
              fontFamily: "Nunito, Avenir Next, Segoe UI, sans-serif",
              fontSize: 12,
              fontWeight: 800,
              color: colors.ink,
              background: colors.bubble,
              borderRadius: 999,
              padding: "3px 8px",
              whiteSpace: "nowrap",
            }}
          >
            ♪ {label}
          </div>
        </Html>
      ) : null}
      {speaker.playing && !reduced ? <Note x={-0.22} delay="0s" color={colors.note} /> : null}
      {speaker.playing && !reduced ? <Note x={0.24} delay="0.45s" color={colors.note} /> : null}
    </group>
  );
}

function Note({ x, delay, color }: { x: number; delay: string; color: string }) {
  return (
    <Html position={[x, 0.7, 0]} center zIndexRange={[11, 0]} style={{ pointerEvents: "none" }}>
      <span
        style={{
          display: "block",
          color,
          fontSize: 18,
          animation: "fluffy-note 1.3s ease-in infinite",
          animationDelay: delay,
        }}
      >
        ♪
      </span>
    </Html>
  );
}
