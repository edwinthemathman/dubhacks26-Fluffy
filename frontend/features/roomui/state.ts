/**
 * Render state for the living-room scene.
 * Unknown values stay unknown. Do not seed this with a pretend brightness.
 */
export type Warmth = "warm" | "neutral" | "cool";
export type PhoneChannel = "imessage" | "sms" | "whatsapp" | "other";
export type RoomObject = "lamp" | "ceiling" | "blinds" | "speaker" | "phone" | "door";
export type PendingState = "asking" | "failed" | "pending";
export type DeliveryState = "accepted" | "delivered" | "acknowledged" | "failed" | "unknown";

export type FlashVisual = {
  id: string;
  color: string;
  startedAt: number;
  endedAt: number;
  pulses: number;
};

export type RoomState = {
  known: boolean;
  lamp: { level: number | null; warmth: Warmth | null };
  ceiling: { level: number | null; warmth: Warmth | null };
  blinds: { position: number | null };
  /** Scenery only unless a real manifest adds door control. */
  door: { open: boolean | null };
  speaker: {
    playing: boolean;
    pending: boolean;
    volume: number | null;
    track?: string;
  };
  phone: {
    mode: "idle" | "texting" | "incoming";
    delivery: DeliveryState | null;
    to?: string;
    preview?: string;
    channel?: PhoneChannel;
    animateUntil?: number;
  };
  ask: { question: string; choices?: string[] } | null;
  pending: Partial<Record<RoomObject, PendingState>>;
  flash: FlashVisual | null;
  caption: string | null;
};

export function unknownRoom(): RoomState {
  return {
    known: false,
    lamp: { level: null, warmth: null },
    ceiling: { level: null, warmth: null },
    blinds: { position: null },
    door: { open: null },
    speaker: { playing: false, pending: false, volume: null },
    phone: { mode: "idle", delivery: null },
    ask: null,
    pending: {},
    flash: null,
    caption: null,
  };
}

/** Kept so older imports cannot treat a fixture brightness as the live home. */
export function initialRoom(): RoomState {
  return unknownRoom();
}

/** A still living room for the design canvas. Nothing here is read from a session. */
export function designRoom(): RoomState {
  return {
    known: true,
    lamp: { level: 72, warmth: "warm" },
    ceiling: { level: 36, warmth: "neutral" },
    blinds: { position: 55 },
    door: { open: false },
    speaker: { playing: false, pending: false, volume: 0 },
    phone: { mode: "idle", delivery: null },
    ask: null,
    pending: {},
    flash: null,
    caption: null,
  };
}
