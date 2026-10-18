/** Canonical UI contract. Components should not branch on raw backend shapes. */

export type ContractMode = "execution-v2" | "legacy-live";

export type Warmth = "warm" | "neutral" | "cool";

export type ActionStatus =
  | "proposed"
  | "approved"
  | "rejected"
  | "started"
  | "verified"
  | "accepted"
  | "delivered"
  | "failed"
  | "unknown";

export type TurnSource = "text" | "audio" | "vision" | "system" | "recorded";

export type CapabilityName = "text" | "questions" | "media" | "stop" | "inference";

export type CapabilityState = "unknown" | "available" | "missing";

export type LightReading = {
  id: string;
  luminosity: number | null;
  warmth: Warmth | null;
};

export type BlindReading = {
  id: string;
  position: number | null;
};

export type SpeakerModel = {
  sound: string | null;
  volume: number | null;
  /** Playing only when a snapshot or verified result says so. */
  playback: "unknown" | "pending" | "playing" | "stopped" | "failed";
  confirmed: boolean;
};

export type FlashModel = {
  id: string;
  color: string;
  startedAt: number;
  endedAt: number;
  pulses: number;
};

export type CaregiverModel = {
  alertId: string;
  message: string;
  severity: string;
  delivery: "accepted" | "delivered" | "acknowledged" | "failed" | "unknown";
  /** Present only when the backend named someone. */
  recipient: string | null;
  animateUntil: number;
};

export type DoorReading = {
  id: string;
  open: boolean | null;
};

export type CallReading = {
  to: string;
  until: number;
};

export type HomeModel = {
  known: boolean;
  revision: number | null;
  lights: Record<string, LightReading>;
  blinds: Record<string, BlindReading>;
  doors: Record<string, DoorReading>;
  speaker: SpeakerModel;
  flash: FlashModel | null;
  caption: { text: string; until: number | null } | null;
  caregiver: CaregiverModel | null;
  caregiverName: string | null;
  call: CallReading | null;
};

export type HomePatch = {
  revision?: number | null;
  lights?: LightReading[];
  blinds?: BlindReading[];
  doors?: DoorReading[];
  speaker?: Partial<SpeakerModel>;
  call?: CallReading | null;
  flash?: FlashModel | null;
  caption?: { text: string; until: number | null } | null;
  caregiver?: CaregiverModel | null;
  caregiverName?: string | null;
  /** When true, omitted devices stay unknown instead of keeping stale values. */
  replace?: boolean;
};

export type LiveAction = {
  actionId: string;
  tool: string;
  args: Record<string, unknown>;
};

export type EvidenceRow = { kind: string; text: string };

export type LivePlan = {
  planId: string;
  createdAt: string;
  expiresAt?: string;
  intent: string;
  reasoning: string;
  confidence: number | null;
  triggerKind: string;
  room: string;
  actionClass?: string;
  consentRequired: boolean;
  question?: string;
  evidence: EvidenceRow[];
  actions: LiveAction[];
};

export type ActionModel = {
  id: string;
  planId?: string;
  turnId?: string;
  tool: string;
  args: Record<string, unknown>;
  status: ActionStatus;
  detail?: string;
  summary?: string;
  updatedAt: number;
};

export type PlanModel = LivePlan & {
  decision?: "approved" | "rejected";
  reason?: string;
  resultStatus?: string;
};

export type TurnModel = {
  id: string;
  requestId?: string;
  createdAt: number;
  source: TurnSource;
  text?: string;
  observations: { text: string; at: number }[];
  planId?: string;
  actionIds: string[];
  open: boolean;
  recorded: boolean;
  /** Scripted rows for an explicitly selected recorded scene. Not device state. */
  recordedRows?: { label: string; from: string; to: string }[];
  recordedSpoken?: string | null;
  recordedConfirm?: { question: string; then: { label: string; from: string; to: string } } | null;
  recordedConfirmed?: boolean;
};

export type QuestionModel = {
  id: string;
  turnId?: string;
  text: string;
  choices: string[];
  answer?: string;
};

export type IncidentModel = {
  id: string;
  status: string;
  summary: string;
};

export type SpeechItem = {
  messageId: string;
  text: string;
  audioUrl?: string;
  permitAudio: boolean;
};

export type ConnectionState = "offline" | "connecting" | "open" | "reconnecting";

export type SessionState = {
  sessionId: string;
  streamSession: string | null;
  epoch: number;
  lastSeq: number | null;
  seen: string[];
  connection: ConnectionState;
  executorConnected: boolean | null;
  executorStatus: string;
  home: HomeModel;
  turns: TurnModel[];
  plans: Record<string, PlanModel>;
  actions: Record<string, ActionModel>;
  question: QuestionModel | null;
  incident: IncidentModel | null;
  speechQueue: SpeechItem[];
  playedIds: string[];
  speechStatus: Record<string, "generated" | "playing" | "played" | "failed" | "unavailable">;
  captions: { messageId: string; text: string; at: number }[];
  capabilities: Record<CapabilityName, CapabilityState>;
  capabilityNotes: Partial<Record<CapabilityName, string>>;
  inference: "idle" | "running";
  stage: string | null;
  thinking: { label: string; text: string }[];
  observationPending: boolean;
  notice: string | null;
  inputAccepted: boolean;
};

type StreamMeta = {
  dedupKey?: string;
  seq?: number;
  sessionId?: string;
};

export type ActionResultFact = {
  actionId: string;
  rawStatus: "ok" | "error" | "skipped" | "unknown";
  error?: string;
  newState?: unknown;
  providerConfirmed?: boolean;
  delivery?: "accepted" | "delivered" | "failed" | "unknown";
};

export type CanonicalEvent = StreamMeta &
  (
    | { type: "snapshot"; patch: HomePatch }
    | { type: "home"; patch: HomePatch }
    | { type: "turn.start"; turnId: string; requestId?: string; source: TurnSource; text?: string }
    | { type: "turn.end"; turnId: string }
    | { type: "stage"; label: string; analyzed: boolean; step?: string; thinking?: string }
    | { type: "inference.error"; turnId?: string; message: string }
    | { type: "observation"; turnId?: string; text: string; pending?: boolean }
    | { type: "say"; messageId: string; turnId?: string; text: string; permitAudio: boolean; audioUrl?: string }
    | { type: "speech"; messageId: string; status: "generated" | "playing" | "played" | "failed" }
    | { type: "ask"; questionId: string; turnId?: string; question: string; choices: string[] }
    | { type: "ask.resolved"; questionId: string; answer?: string }
    | { type: "plan"; plan: LivePlan; turnId?: string }
    | { type: "plan.decision"; planId: string; decision: "approved" | "rejected"; reason: string }
    | { type: "plan.result"; planId: string; status: string; results: ActionResultFact[] }
    | {
        type: "action";
        actionId: string;
        planId?: string;
        turnId?: string;
        tool?: string;
        args?: Record<string, unknown>;
        status: ActionStatus;
        detail?: string;
        patch?: HomePatch;
      }
    | { type: "incident"; incident: IncidentModel }
    | { type: "executor"; connected: boolean; status: string }
    | { type: "connection"; status: ConnectionState }
    | { type: "capability"; name: CapabilityName; available: boolean; detail: string }
    | { type: "submit"; requestId: string; text: string }
    | { type: "submit.accepted"; requestId: string }
    | { type: "submit.failed"; requestId: string; detail: string }
    | {
        type: "recorded";
        requestId: string;
        text: string;
        title: string;
        plan: LivePlan;
        rows: { label: string; from: string; to: string }[];
        spoken: string | null;
        confirm: { question: string; then: { label: string; from: string; to: string } } | null;
      }
    | { type: "recorded.confirm"; requestId: string }
    | { type: "notice"; text: string | null }
  );
