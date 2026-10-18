import { useEffect } from "react";
import { useThree } from "@react-three/fiber";
import type { PerspectiveCamera } from "three";
import type { RoomState } from "../state";
import { AskBubble } from "./AskBubble";
import { Door } from "./Door";
import { FlashOverlay } from "./FlashOverlay";
import { RoomLight } from "./Light";
import { Phone } from "./Phone";
import { Room } from "./Room";
import { Speaker } from "./Speaker";
import { themeColors, type RoomThemeName } from "./theme";

export function Stage({
  state,
  theme,
  reduced,
}: {
  state: RoomState;
  theme: RoomThemeName;
  reduced: boolean;
}) {
  const colors = themeColors(theme);
  return (
    <>
      <color attach="background" args={[colors.bg]} />
      <Wake state={state} theme={theme} reduced={reduced} />
      <CameraRig />
      <ambientLight intensity={theme === "dark" ? 0.45 : 0.72} />
      <directionalLight position={[3, 5, 2]} intensity={theme === "dark" ? 0.55 : 0.85} />
      <Room colors={colors} />
      <RoomLight light={state.light} pending={state.pending.light} colors={colors} reduced={reduced} />
      <Door open={state.door.open} pending={state.pending.door} colors={colors} reduced={reduced} />
      <Speaker speaker={state.speaker} pending={state.pending.speaker} colors={colors} reduced={reduced} />
      <Phone phone={state.phone} pending={state.pending.phone} colors={colors} reduced={reduced} />
      <FlashOverlay flash={state.light.flash} reduced={reduced} />
      <AskBubble ask={state.ask} colors={colors} />
    </>
  );
}

function Wake({ state, theme, reduced }: { state: RoomState; theme: RoomThemeName; reduced: boolean }) {
  const { invalidate } = useThree();
  useEffect(() => {
    invalidate();
  }, [state, theme, reduced, invalidate]);
  return null;
}

function CameraRig() {
  const { camera, invalidate } = useThree();
  useEffect(() => {
    const perspective = camera as PerspectiveCamera;
    perspective.fov = 36;
    perspective.position.set(3.05, 2.05, 3.45);
    perspective.lookAt(0.05, 0.72, 0);
    perspective.updateProjectionMatrix();
    invalidate();
  }, [camera, invalidate]);
  return null;
}
