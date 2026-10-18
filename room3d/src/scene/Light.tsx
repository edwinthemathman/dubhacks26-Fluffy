import { useRef } from "react";
import { useFrame, useThree } from "@react-three/fiber";
import type { MeshStandardMaterial, PointLight } from "three";
import type { PendingState, RoomState } from "../state";
import { approach, useAct } from "./motion";
import { warmthColor, type ThemeColors } from "./theme";

const BASE_X = -1.2;

export function RoomLight({
  light,
  pending,
  colors,
  reduced,
}: {
  light: RoomState["light"];
  pending?: PendingState;
  colors: ThemeColors;
  reduced: boolean;
}) {
  const { group, glow } = useAct(BASE_X, pending, reduced, colors.fail, colors.outline);
  const level = useRef(light.level / 100);
  const bulb = useRef<MeshStandardMaterial>(null);
  const lamp = useRef<PointLight>(null);
  const { invalidate } = useThree();

  useFrame((_, dt) => {
    const target = light.level / 100;
    const next = approach(level.current, target, dt, 4.5, reduced);
    const moved = Math.abs(next - level.current) > 0.001;
    level.current = next;
    const color = warmthColor(light.warmth);
    if (bulb.current) {
      bulb.current.emissive.set(color);
      bulb.current.emissiveIntensity = 0.2 + next * 2;
    }
    if (lamp.current) {
      lamp.current.color.set(color);
      lamp.current.intensity = 0.15 + next * 5.5;
    }
    if (moved) invalidate();
  });

  return (
    <group ref={group} position={[BASE_X, 0, -0.25]}>
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
        <meshStandardMaterial ref={bulb} color="#fff8ee" emissive={warmthColor(light.warmth)} emissiveIntensity={1.2} />
      </mesh>
      <pointLight ref={lamp} position={[0, 1.28, 0.1]} distance={7} decay={2} intensity={3} />
      <mesh position={[0, 1.32, 0]}>
        <sphereGeometry args={[0.2, 20, 16]} />
        <meshBasicMaterial ref={glow} transparent opacity={0} depthWrite={false} color={colors.outline} />
      </mesh>
    </group>
  );
}
