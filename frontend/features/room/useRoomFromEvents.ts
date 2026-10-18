"use client";

import { unknownRoom, type RoomState } from "./state";

/** Intentionally does not subscribe to a socket. The session provider owns the only UI connection. */
export function useRoomFromEvents(): RoomState {
  return unknownRoom();
}
