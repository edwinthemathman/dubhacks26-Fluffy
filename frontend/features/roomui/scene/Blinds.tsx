import { useRef } from "react";
import { useFrame, useThree } from "@react-three/fiber";
import type { MeshBasicMaterial } from "three";
import type { PendingState } from "../state";
import type { ThemeColors } from "./theme";

const WINDOW = { x: -2.07, y: 1.2, z: -0.2, height: 0.62, width: 0.78 };

/** Position 0 is closed and 100 is open. Null waits instead of pretending. */
export function Blinds({
  position,
  pending,
  colors,
  reduced,
}: {
  position: number | null;
  pending?: PendingState;
  colors: ThemeColors;
  reduced: boolean;
}) {
  const glow = useRef<MeshBasicMaterial>(null);
  const { invalidate } = useThree();
  const cover = position == null ? 0 : (100 - position) / 100;
  const height = Math.max(0.02, WINDOW.height * (position == null ? 0.15 : cover));
  const y = WINDOW.y + WINDOW.height / 2 - height / 2;

  useFrame(() => {
    if (!glow.current) return;
    const show = pending === "pending" || pending === "asking" || pending === "failed";
    const opacity = !show ? 0 : pending === "failed" ? 0.55 : reduced ? 0.35 : 0.22;
    if (Math.abs(glow.current.opacity - opacity) > 0.01) {
      glow.current.opacity = opacity;
      glow.current.color.set(pending === "failed" ? colors.fail : colors.outline);
      invalidate();
    }
  });

  return (
    <group>
      <mesh position={[WINDOW.x, y, WINDOW.z]}>
        <boxGeometry args={[0.03, height, WINDOW.width]} />
        <meshStandardMaterial color={position == null ? colors.trim : colors.metal} />
      </mesh>
      <mesh position={[WINDOW.x + 0.01, WINDOW.y, WINDOW.z]}>
        <boxGeometry args={[0.01, WINDOW.height, WINDOW.width]} />
        <meshBasicMaterial ref={glow} transparent opacity={0} depthWrite={false} color={colors.outline} />
      </mesh>
    </group>
  );
}
