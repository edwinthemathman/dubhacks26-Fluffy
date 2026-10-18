import { useRef } from "react";
import { useFrame, useThree } from "@react-three/fiber";
import type { Group, MeshBasicMaterial } from "three";
import type { PendingState } from "../state";

export function approach(current: number, target: number, dt: number, speed: number, reduced: boolean): number {
  if (reduced || Math.abs(current - target) < 0.001) return target;
  const next = current + (target - current) * (1 - Math.exp(-speed * Math.min(dt, 0.05)));
  return Math.abs(next - target) < 0.001 ? target : next;
}

/** Soft pulse while asking, a short red shake when a call fails. */
export function useAct(baseX: number, pending: PendingState | undefined, reduced: boolean, fail: string, outline: string) {
  const group = useRef<Group>(null);
  const glow = useRef<MeshBasicMaterial>(null);
  const failedAt = useRef(0);
  const { invalidate } = useThree();

  useFrame(() => {
    const node = group.current;
    if (!node) return;
    let moving = false;
    if (pending === "failed") {
      if (!failedAt.current) failedAt.current = performance.now();
    } else {
      failedAt.current = 0;
    }
    const age = failedAt.current ? performance.now() - failedAt.current : 0;
    const wobble = pending === "failed" && !reduced && age < 650 ? Math.sin(performance.now() / 42) * 0.05 : 0;
    if (Math.abs(node.position.x - (baseX + wobble)) > 0.0001) {
      node.position.x = baseX + wobble;
      moving = true;
    }
    if (glow.current) {
      const show = pending === "asking" || pending === "pending" || (pending === "failed" && age < 650);
      const opacity = !show ? 0 : pending === "failed" ? 0.7 : reduced ? 0.45 : 0.28 + Math.sin(performance.now() / 220) * 0.2;
      if (Math.abs(glow.current.opacity - opacity) > 0.01) {
        glow.current.opacity = opacity;
        glow.current.color.set(pending === "failed" ? fail : outline);
        moving = true;
      }
      if (show && !reduced) moving = true;
    }
    if (moving) invalidate();
  });

  return { group, glow };
}
