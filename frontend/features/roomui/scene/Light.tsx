import { useRef } from "react";
import { useFrame, useThree } from "@react-three/fiber";
import type { MeshStandardMaterial, PointLight } from "three";
import type { PendingState, RoomState } from "../state";
import { approach, useAct } from "./motion";
import { warmthColor, type ThemeColors } from "./theme";

type LightNode = RoomState["lamp"];

export function RoomLight({
  kind,
  light,
  pending,
  colors,
  reduced,
}: {
  kind: "floor" | "ceiling";
  light: LightNode;
  pending?: PendingState;
  colors: ThemeColors;
  reduced: boolean;
}) {
  const baseX = kind === "floor" ? -1.2 : 0.45;
  const { group, glow } = useAct(baseX, pending, reduced, colors.fail, colors.outline);
  const level = useRef((light.level ?? 0) / 100);
  const bulb = useRef<MeshStandardMaterial>(null);
  const lamp = useRef<PointLight>(null);
  const { invalidate } = useThree();
  const unknown = light.level == null;

  useFrame((_, dt) => {
    const target = unknown ? 0 : (light.level ?? 0) / 100;
    const next = approach(level.current, target, dt, 4.5, reduced);
    const moved = Math.abs(next - level.current) > 0.001;
    level.current = next;
    const color = warmthColor(light.warmth);
    if (bulb.current) {
      bulb.current.emissive.set(color);
      bulb.current.emissiveIntensity = unknown ? 0.04 : 0.2 + next * 2;
    }
    if (lamp.current) {
      lamp.current.color.set(color);
      lamp.current.intensity = unknown ? 0 : 0.15 + next * (kind === "ceiling" ? 3.2 : 5.5);
    }
    if (moved) invalidate();
  });

  if (kind === "ceiling") {
    return (
      <group ref={group} position={[baseX, 1.72, -0.35]}>
        <mesh>
          <cylinderGeometry args={[0.28, 0.28, 0.04, 24]} />
          <meshStandardMaterial color={colors.metal} />
        </mesh>
        <mesh position={[0, -0.08, 0]}>
          <sphereGeometry args={[0.07, 18, 14]} />
          <meshStandardMaterial ref={bulb} color="#fff8ee" emissive={warmthColor(light.warmth)} emissiveIntensity={unknown ? 0.04 : 1} />
        </mesh>
        <pointLight ref={lamp} position={[0, -0.12, 0]} distance={6} decay={2} intensity={unknown ? 0 : 2} />
        <mesh position={[0, -0.08, 0]}>
          <sphereGeometry args={[0.16, 16, 12]} />
          <meshBasicMaterial ref={glow} transparent opacity={0} depthWrite={false} color={colors.outline} />
        </mesh>
      </group>
    );
  }

  return (
    <group ref={group} position={[baseX, 0, -0.25]}>
      <mesh position={[0, 0.03, 0]}>
        <cylinderGeometry args={[0.18, 0.22, 0.06, 20]} />
        <meshStandardMaterial color={colors.metal} />
      </mesh>
      <mesh position={[0, 0.78, 0]}>
        <cylinderGeometry args={[0.028, 0.034, 1.45, 12]} />
        <meshStandardMaterial color={colors.metal} />
      </mesh>
      <mesh position={[0, 1.42, 0]}>
        <cylinderGeometry args={[0.16, 0.28, 0.22, 24]} />
        <meshStandardMaterial color={colors.shade} />
      </mesh>
      <mesh position={[0, 1.3, 0]}>
        <sphereGeometry args={[0.08, 20, 16]} />
        <meshStandardMaterial ref={bulb} color="#fff8ee" emissive={warmthColor(light.warmth)} emissiveIntensity={unknown ? 0.04 : 1.2} />
      </mesh>
      <pointLight ref={lamp} position={[0, 1.28, 0.1]} distance={7} decay={2} intensity={unknown ? 0 : 3} />
      <mesh position={[0, 1.32, 0]}>
        <sphereGeometry args={[0.2, 20, 16]} />
        <meshBasicMaterial ref={glow} transparent opacity={0} depthWrite={false} color={colors.outline} />
      </mesh>
    </group>
  );
}
