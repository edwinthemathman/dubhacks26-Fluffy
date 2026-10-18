import { useRef } from "react";
import { useFrame, useThree } from "@react-three/fiber";
import type { MeshBasicMaterial } from "three";
import type { FlashVisual } from "../state";

/**
 * Pulse count is absolute. Two pulses means two cycles between start and end.
 * An expired window stays dark, including after a reconnect.
 */
export function FlashOverlay({
  flash,
  now,
  reduced,
}: {
  flash: FlashVisual | null;
  now: number;
  reduced: boolean;
}) {
  const mat = useRef<MeshBasicMaterial>(null);
  const { invalidate } = useThree();
  const active = flash != null && now >= flash.startedAt && now < flash.endedAt;

  useFrame(() => {
    const material = mat.current;
    if (!material || !flash) return;
    const span = Math.max(1, flash.endedAt - flash.startedAt);
    const t = Math.min(1, Math.max(0, (now - flash.startedAt) / span));
    const wave = Math.sin(t * flash.pulses * Math.PI * 2);
    const opacity = !active ? 0 : reduced ? (wave > 0 ? 0.18 : 0.04) : Math.max(0, wave) * 0.24;
    if (Math.abs(material.opacity - opacity) > 0.01 || active) {
      material.opacity = opacity;
      material.color.set(flash.color);
      if (active) invalidate();
    }
  });

  if (!flash || now >= flash.endedAt) return null;
  return (
    <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, 0.03, 0]}>
      <planeGeometry args={[4.5, 3.4]} />
      <meshBasicMaterial ref={mat} transparent opacity={0} depthWrite={false} color={flash.color} />
    </mesh>
  );
}
