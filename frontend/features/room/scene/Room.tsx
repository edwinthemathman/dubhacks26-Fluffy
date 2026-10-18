import type { ThemeColors } from "./theme";

export function Room({ colors }: { colors: ThemeColors }) {
  return (
    <group>
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, 0, 0]}>
        <planeGeometry args={[4.4, 3.8]} />
        <meshStandardMaterial color={colors.floor} />
      </mesh>
      <mesh position={[0.1, 0.012, 0.15]} rotation={[-Math.PI / 2, 0, 0]}>
        <planeGeometry args={[1.5, 1]} />
        <meshStandardMaterial color={colors.rug} />
      </mesh>
      <mesh position={[0, 0.95, -1.86]}>
        <boxGeometry args={[4.4, 1.9, 0.08]} />
        <meshStandardMaterial color={colors.wall} />
      </mesh>
      <mesh position={[-2.16, 0.95, 0]}>
        <boxGeometry args={[0.08, 1.9, 3.8]} />
        <meshStandardMaterial color={colors.wallSide} />
      </mesh>
      <mesh position={[-2.1, 1.2, -0.2]}>
        <boxGeometry args={[0.02, 0.62, 0.78]} />
        <meshStandardMaterial color={colors.bg} emissive={colors.outline} emissiveIntensity={0.18} />
      </mesh>
      <mesh position={[0, 0.05, -1.8]}>
        <boxGeometry args={[4.3, 0.1, 0.06]} />
        <meshStandardMaterial color={colors.trim} />
      </mesh>
      <mesh position={[-2.1, 0.05, 0]}>
        <boxGeometry args={[0.06, 0.1, 3.6]} />
        <meshStandardMaterial color={colors.trim} />
      </mesh>
    </group>
  );
}
