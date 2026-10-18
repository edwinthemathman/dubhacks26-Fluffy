"use client";

import { useEffect, useState } from "react";
import { toRoomEvent } from "./events";
import { roomReducer } from "./reducer";
import { initialRoom, type RoomEvent, type RoomState } from "./state";

type MessageSource = {
  addEventListener(type: "message", listener: (event: { data: unknown }) => void): void;
  removeEventListener(type: "message", listener: (event: { data: unknown }) => void): void;
};

/** Fold a list of events, or subscribe to a harness socket on `/ws/ui`. */
export function useRoomFromEvents(source: RoomEvent[] | MessageSource | null | undefined): RoomState {
  const [state, setState] = useState(initialRoom);

  useEffect(() => {
    if (!Array.isArray(source)) return;
    setState(source.reduce((room, event) => roomReducer(room, event), initialRoom()));
  }, [source]);

  useEffect(() => {
    if (source == null || Array.isArray(source)) return;
    setState(initialRoom());
    const onMessage = (message: { data: unknown }) => {
      const raw = typeof message.data === "string" ? parse(message.data) : message.data;
      const event = toRoomEvent(raw);
      if (!event) return;
      setState((room) => roomReducer(room, event));
    };
    source.addEventListener("message", onMessage);
    return () => source.removeEventListener("message", onMessage);
  }, [source]);

  useEffect(() => {
    const timer = setInterval(() => {
      const now = Date.now();
      setState((room) => roomReducer(room, { type: "tick", now }));
    }, 200);
    return () => clearInterval(timer);
  }, []);

  return state;
}

function parse(data: string): unknown {
  try {
    return JSON.parse(data);
  } catch {
    return null;
  }
}
