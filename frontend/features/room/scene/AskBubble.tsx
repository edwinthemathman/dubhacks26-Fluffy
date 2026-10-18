import { Html } from "@react-three/drei";
import type { RoomState } from "../state";
import type { ThemeColors } from "./theme";

export function AskBubble({ ask, colors }: { ask: RoomState["ask"]; colors: ThemeColors }) {
  if (!ask) return null;
  return (
    <Html position={[0, 1.55, 0.35]} center zIndexRange={[20, 0]} style={{ pointerEvents: "none" }}>
      <div
        style={{
          display: "flex",
          gap: 8,
          alignItems: "flex-start",
          maxWidth: 230,
          background: colors.bubble,
          color: colors.ink,
          border: `1px solid ${colors.outline}`,
          borderRadius: 16,
          padding: "8px 10px",
          fontFamily: "Inter, ui-sans-serif, system-ui, sans-serif",
          fontSize: 14,
          lineHeight: 1.35,
          boxShadow: "0 10px 24px rgba(13, 39, 68, 0.14)",
        }}
      >
        <span
          style={{
            width: 22,
            height: 22,
            borderRadius: 11,
            background: colors.outline,
            color: "#ffffff",
            display: "grid",
            placeItems: "center",
            fontWeight: 800,
            flex: "none",
          }}
        >
          ?
        </span>
        <span>{ask.question}</span>
      </div>
    </Html>
  );
}
