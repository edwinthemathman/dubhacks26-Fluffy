import { useEffect } from "react";
import { useThree } from "@react-three/fiber";
import type { PerspectiveCamera } from "three";
import type { RoomState } from "../state";
import { AskBubble } from "./AskBubble";
import { Blinds } from "./Blinds";
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
  now,
}: {
  state: RoomState;
  theme: RoomThemeName;
  reduced: boolean;
  now: number;
}) {
  const colors = themeColors(theme);
  return (
    <>
      <color attach="background" args={[colors.bg]} />
      <Wake state={state} theme={theme} reduced={reduced} now={now} />
      <CameraRig />
      <ambientLight intensity={theme === "dark" ? 0.45 : 0.78} />
      <directionalLight position={[3, 5, 2]} intensity={theme === "dark" ? 0.45 : 0.7} />
      <Room colors={colors} />
      <Blinds position={state.blinds.position} pending={state.pending.blinds} colors={colors} reduced={reduced} />
      <RoomLight kind="floor" light={state.lamp} pending={state.pending.lamp} colors={colors} reduced={reduced} />
      <RoomLight kind="ceiling" light={state.ceiling} pending={state.pending.ceiling} colors={colors} reduced={reduced} />
      <Door open={state.door.open !== false} pending={undefined} colors={colors} reduced={reduced} />
      <Speaker speaker={state.speaker} pending={state.pending.speaker} colors={colors} reduced={reduced} />
      <Phone phone={state.phone} pending={state.pending.phone} colors={colors} reduced={reduced} now={now} />
      <FlashOverlay flash={state.flash} now={now} reduced={reduced} />
      <AskBubble ask={state.ask} colors={colors} />
    </>
  );
}

function Wake({ state, theme, reduced, now }: { state: RoomState; theme: RoomThemeName; reduced: boolean; now: number }) {
  const { invalidate } = useThree();
  useEffect(() => {
    invalidate();
  }, [state, theme, reduced, now, invalidate]);
  return null;
}

function CameraRig() {
  const { camera, invalidate } = useThree();
  useEffect(() => {
    const perspective = camera as PerspectiveCamera;
    /* eslint-disable react-hooks/immutability -- R3F exposes a live camera object */
    perspective.fov = 36;
    perspective.position.set(3.05, 2.05, 3.45);
    perspective.lookAt(0.05, 0.72, 0);
    perspective.updateProjectionMatrix();
    /* eslint-enable react-hooks/immutability */
    invalidate();
  }, [camera, invalidate]);
  return null;
}
