import { Html } from "@react-three/drei";
import { useRef } from "react";
import { useFrame, useThree } from "@react-three/fiber";
import type { Group, Mesh } from "three";
import type { PendingState, RoomState } from "../state";
import { useAct } from "./motion";
import { channelColor, type ThemeColors } from "./theme";

const BASE_X = 1.15;

export function Phone({
  phone,
  pending,
  colors,
  reduced,
}: {
  phone: RoomState["phone"];
  pending?: PendingState;
  colors: ThemeColors;
  reduced: boolean;
}) {
  const { group, glow } = useAct(BASE_X, pending, reduced, colors.fail, colors.outline);
  const handset = useRef<Group>(null);
  const ring = useRef<Mesh>(null);
  const { invalidate } = useThree();
  const alive = phone.mode !== "idle" && (phone.until == null || Date.now() < phone.until);
  const mode = alive ? phone.mode : "idle";
  const bubble = channelColor(phone.channel);

  useFrame(() => {
    let moving = false;
    if (handset.current) {
      const buzz = mode === "incoming" && !reduced ? Math.sin(performance.now() / 28) * 0.03 : 0;
      if (Math.abs(handset.current.rotation.z - buzz) > 0.001) {
        handset.current.rotation.z = buzz;
        moving = true;
      }
    }
    if (ring.current) {
      const show = mode === "calling";
      const scale = show && !reduced ? 1 + (Math.sin(performance.now() / 200) * 0.5 + 0.5) * 0.8 : show ? 1.2 : 0.2;
      ring.current.scale.setScalar(scale);
      ring.current.visible = show;
      if (show) moving = true;
    }
    if (moving) invalidate();
  });

  return (
    <group ref={group} position={[BASE_X, 0, -0.15]}>
      <mesh position={[0, 0.22, 0]}>
        <boxGeometry args={[0.7, 0.08, 0.46]} />
        <meshStandardMaterial color={colors.wood} />
      </mesh>
      <group ref={handset} position={[0.08, 0.32, 0]}>
        <mesh>
          <boxGeometry args={[0.22, 0.04, 0.4]} />
          <meshStandardMaterial color={colors.phone} />
        </mesh>
        <mesh position={[0, 0.025, 0]}>
          <boxGeometry args={[0.18, 0.01, 0.34]} />
          <meshStandardMaterial
            color={mode === "idle" ? "#1a2836" : bubble}
            emissive={mode === "idle" ? "#000000" : bubble}
            emissiveIntensity={mode === "idle" ? 0 : 0.7}
          />
        </mesh>
      </group>
      <mesh ref={ring} position={[0.08, 0.36, 0]} rotation={[Math.PI / 2, 0, 0]} visible={false}>
        <torusGeometry args={[0.2, 0.012, 8, 24]} />
        <meshBasicMaterial color={bubble} transparent opacity={0.7} />
      </mesh>
      <mesh position={[0.08, 0.36, 0.24]}>
        <boxGeometry args={[0.5, 0.28, 0.02]} />
        <meshBasicMaterial ref={glow} transparent opacity={0} depthWrite={false} color={colors.outline} />
      </mesh>
      {mode === "texting" || mode === "incoming" ? (
        <Html position={[0.55, 0.7, 0]} center zIndexRange={[14, 0]} style={{ pointerEvents: "none" }}>
          <div
            style={{
              maxWidth: 150,
              background: mode === "incoming" ? colors.bubble : bubble,
              color: mode === "incoming" ? colors.ink : "#ffffff",
              borderRadius: 12,
              padding: "6px 8px",
              fontFamily: "Nunito, Avenir Next, Segoe UI, sans-serif",
              fontSize: 12,
              lineHeight: 1.3,
              border: mode === "incoming" ? `2px solid ${bubble}` : "none",
            }}
          >
            <strong>{mode === "incoming" ? phone.to ?? "Reply" : `To ${phone.to ?? "someone"}`}</strong>
            {phone.delivered ? " ✓" : ""}
            {phone.preview ? <div>{phone.preview}</div> : null}
          </div>
        </Html>
      ) : null}
    </group>
  );
}
