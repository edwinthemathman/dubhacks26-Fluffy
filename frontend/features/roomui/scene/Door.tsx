import { useRef } from "react";
import { useFrame, useThree } from "@react-three/fiber";
import type { Group } from "three";
import type { PendingState } from "../state";
import { approach, useAct } from "./motion";
import type { ThemeColors } from "./theme";

const BASE_X = 0.35;
const CLOSED = 0;
const OPEN = -80 * (Math.PI / 180);

export function Door({
  open,
  pending,
  colors,
  reduced,
}: {
  open: boolean;
  pending?: PendingState;
  colors: ThemeColors;
  reduced: boolean;
}) {
  const { group, glow } = useAct(BASE_X, pending, reduced, colors.fail, colors.outline);
  const hinge = useRef<Group>(null);
  const angle = useRef(open ? OPEN : CLOSED);
  const { invalidate } = useThree();

  useFrame((_, dt) => {
    const target = open ? OPEN : CLOSED;
    const next = approach(angle.current, target, dt, 6, reduced);
    if (Math.abs(next - angle.current) > 0.0005 && hinge.current) {
      angle.current = next;
      hinge.current.rotation.y = next;
      invalidate();
    } else {
      angle.current = next;
      if (hinge.current) hinge.current.rotation.y = next;
    }
  });

  return (
    <group ref={group} position={[BASE_X, 0, -1.66]}>
      <mesh position={[-0.52, 0.95, 0]}>
        <boxGeometry args={[0.06, 1.9, 0.08]} />
        <meshStandardMaterial color={colors.wood} />
      </mesh>
      <mesh position={[0.52, 0.95, 0]}>
        <boxGeometry args={[0.06, 1.9, 0.08]} />
        <meshStandardMaterial color={colors.wood} />
      </mesh>
      <mesh position={[0, 1.9, 0]}>
        <boxGeometry args={[1.1, 0.08, 0.08]} />
        <meshStandardMaterial color={colors.wood} />
      </mesh>
      <group ref={hinge} rotation={[0, open ? OPEN : CLOSED, 0]}>
        <mesh position={[0.46, 0.95, 0.03]}>
          <boxGeometry args={[0.9, 1.78, 0.05]} />
          <meshStandardMaterial color={colors.wood} />
        </mesh>
        <mesh position={[0.78, 0.95, 0.07]}>
          <sphereGeometry args={[0.035, 12, 12]} />
          <meshStandardMaterial color={colors.metal} />
        </mesh>
        <mesh position={[0.46, 0.95, 0.08]}>
          <boxGeometry args={[1.02, 1.92, 0.02]} />
          <meshBasicMaterial ref={glow} transparent opacity={0} depthWrite={false} color={colors.outline} />
        </mesh>
      </group>
    </group>
  );
}
