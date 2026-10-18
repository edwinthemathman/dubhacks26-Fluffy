export type Warmth = "warm" | "neutral" | "cool";
export type PhoneMode = "idle" | "texting" | "calling" | "incoming";
export type PhoneChannel = "imessage" | "sms" | "whatsapp" | "other";
export type RoomObject = "light" | "door" | "speaker" | "phone";
export type PendingState = "asking" | "failed";

export type RoomState = {
  light: { level: number; warmth: Warmth; flash?: { color: string; until: number } };
  door: { open: boolean };
  speaker: { playing: boolean; track?: string; artist?: string };
  phone: {
    mode: PhoneMode;
    to?: string;
    preview?: string;
    channel?: PhoneChannel;
    delivered?: boolean;
    until?: number;
  };
  ask: { question: string; choices?: string[] } | null;
  pending: Partial<Record<RoomObject, PendingState>>;
  /** Which call is waiting, so a later status can finish it. Not drawn. */
  calls: Record<string, { tool: string; args: Record<string, unknown> }>;
  failedAt: Partial<Record<RoomObject, number>>;
};

export function initialRoom(): RoomState {
  return {
    light: { level: 78, warmth: "warm" },
    door: { open: true },
    speaker: { playing: false },
    phone: { mode: "idle" },
    ask: null,
    pending: {},
    calls: {},
    failedAt: {},
  };
}

export type RoomEvent =
  | { type: "tool.call"; call_id: string; tool: string; args?: Record<string, unknown> }
  | {
      type: "tool.status";
      call_id: string;
      status: string;
      detail?: string;
      result?: Record<string, unknown>;
      now?: number;
    }
  | {
      type: "state";
      lamp?: { luminosity?: number; warmth?: string };
      ceiling?: { luminosity?: number };
      speaker?: {
        sound?: string | null;
        volume?: number;
        playing?: boolean;
        track?: string;
        artist?: string;
      };
      flash?: { active?: boolean; color?: string | null; pattern?: string | null; duration_s?: number };
      door?: { open?: boolean };
      phone?: Partial<RoomState["phone"]>;
      light?: { level?: number; warmth?: string };
      now?: number;
    }
  | { type: "ask"; question: string; choices?: string[] }
  | { type: "ask.answer" }
  | {
      type: "message.received";
      from?: string;
      text?: string;
      preview?: string;
      channel?: string;
      now?: number;
    }
  | { type: "tick"; now: number };
