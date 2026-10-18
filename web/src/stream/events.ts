export type ProfileId = "blind" | "deaf" | "speech_cognitive";

export type ToolStatus =
  | "proposed"
  | "asking"
  | "approved"
  | "done"
  | "rejected"
  | "declined"
  | "failed";

export type SafetyState = "ok" | "checkin1" | "checkin2" | "alerted" | "acked";

export type TurnKind = "spoken" | "sound" | "offer" | "safety";

export type AskBy = "voice" | "nod" | "button" | "typing" | "none";

export type Outcome = "acted" | "asked" | "nothing";

export type HomeState = {
  lamp: { luminosity: number; warmth: "warm" | "neutral" | "cool" };
  ceiling: { luminosity: number };
  blinds: number;
  speaker: { sound: string | null; volume: number };
  flash: { active: boolean; color: string | null; pattern: string | null };
  caption: string | null;
};

export const initialHome = (): HomeState => ({
  lamp: { luminosity: 78, warmth: "warm" },
  ceiling: { luminosity: 36 },
  blinds: 55,
  speaker: { sound: null, volume: 0 },
  flash: { active: false, color: null, pattern: null },
  caption: null,
});

export type TagChip = { name: string; where: string };

export type FluffyEvent =
  | {
      type: "state";
      lamp?: Partial<HomeState["lamp"]>;
      ceiling?: Partial<HomeState["ceiling"]>;
      blinds?: number;
      speaker?: Partial<HomeState["speaker"]>;
      flash?: Partial<HomeState["flash"]>;
      caption?: string | null;
    }
  | { type: "profile"; profile: ProfileId; needs?: string[] }
  | { type: "turn.start"; turn_id: string; kind: TurnKind; trigger: string }
  | { type: "sense.see"; turn_id?: string; thumb?: string; chips: string[]; tags?: TagChip[] }
  | { type: "sense.hear"; turn_id?: string; text?: string; sound?: string }
  | { type: "think"; turn_id: string; note: string }
  | {
      type: "tool.call";
      turn_id: string;
      call_id: string;
      tool: string;
      args: Record<string, unknown>;
      why: string;
      kind: "device" | "meta";
    }
  | {
      type: "tool.status";
      call_id: string;
      plan_id?: string;
      status: ToolStatus;
      detail?: string;
    }
  | { type: "ask"; turn_id: string; question: string; choices?: string[] }
  | { type: "ask.answer"; turn_id: string; by: AskBy; choice?: string }
  | { type: "say"; turn_id?: string; text: string; say_id?: string; voice?: boolean }
  | { type: "turn.end"; turn_id: string; ms: number; outcome: Outcome }
  | { type: "turn.raw"; turn_id: string; text: string }
  | { type: "safety"; state: SafetyState }
  | { type: "fallback"; active: boolean; scene?: string };

export type ClientMessage =
  | { type: "answer"; plan_id?: string; choice: "yes" | "no" | "help"; by: "button" | "typing" | "voice"; text?: string }
  | { type: "profile.set"; profile: ProfileId }
  | { type: "room.reset" }
  | { type: "volume.set"; volume: number }
  | { type: "room.scan" }
  | { type: "tts.start" }
  | { type: "tts.end" }
  | { type: "mock.play"; scene: string }
  | { type: "typed"; text: string }
  | { type: "speech"; text: string }
  | { type: "caregiver.ack" };

export type Line =
  | { type: "heard"; text: string }
  | { type: "think"; text: string }
  | { type: "tool"; callId: string }
  | { type: "ask"; question: string; choices: string[]; answer?: { by: AskBy; choice?: string } }
  | { type: "say"; text: string; voice?: boolean; sayId?: string };

export type ToolRow = {
  callId: string;
  tool: string;
  args: Record<string, unknown>;
  why: string;
  kind: "device" | "meta";
  status: ToolStatus;
  detail?: string;
  planId?: string;
};

export type Turn = {
  id: string;
  kind: TurnKind;
  trigger: string;
  lines: Line[];
  tools: Record<string, ToolRow>;
  ms?: number;
  outcome?: Outcome;
  raw?: string;
};

export type AlertItem = {
  id: string;
  message: string;
  severity: string;
  acked: boolean;
};

export type Snapshot = {
  home: HomeState;
  profile: ProfileId | null;
  needs: string[];
  senses: { chips: string[]; thumb?: string; hear: string; sound?: string; tags: TagChip[] };
  turns: Turn[];
  speech: string;
  speechId: string;
  speechVoice: boolean;
  safety: SafetyState;
  fallback: boolean;
  alerts: AlertItem[];
  link: "idle" | "live" | "mock" | "offline";
};

export const emptySnapshot = (): Snapshot => ({
  home: initialHome(),
  profile: null,
  needs: [],
  senses: { chips: [], hear: "", tags: [] },
  turns: [],
  speech: "",
  speechId: "",
  speechVoice: false,
  safety: "ok",
  fallback: false,
  alerts: [],
  link: "idle",
});

export type TimedEvent = { t: number; event: FluffyEvent };

export type AvatarState =
  | "idle"
  | "seeing"
  | "hearing"
  | "thinking"
  | "acting"
  | "asking"
  | "safety";
