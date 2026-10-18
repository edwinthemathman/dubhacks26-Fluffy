import { useRef } from "react";
import { useFrame, useThree } from "@react-three/fiber";
import type { MeshBasicMaterial } from "three";
import type { RoomState } from "../state";

export function FlashOverlay({ flash, reduced }: { flash: RoomState["light"]["flash"]; reduced: boolean }) {
  const mat = useRef<MeshBasicMaterial>(null);
  const { invalidate } = useThree();

  useFrame(() => {
    const material = mat.current;
    if (!material || !flash) return;
    const active = Date.now() < flash.until;
    const opacity = !active ? 0 : reduced ? 0.16 : 0.08 + Math.abs(Math.sin(performance.now() / 170)) * 0.16;
    if (Math.abs(material.opacity - opacity) > 0.01) {
      material.opacity = opacity;
      material.color.set(flash.color);
      invalidate();
    } else if (active && !reduced) {
      material.opacity = opacity;
      invalidate();
    }
  });

  if (!flash) return null;
  return (
    <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, 0.03, 0]}>
      <planeGeometry args={[4.5, 3.4]} />
      <meshBasicMaterial ref={mat} transparent opacity={0} depthWrite={false} color={flash.color} />
    </mesh>
  );
}
