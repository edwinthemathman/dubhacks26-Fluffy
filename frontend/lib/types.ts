export type Verification =
  | "state_committed"
  | "playback_confirmed"
  | "provider_accepted"
  | "provider_delivered"
  | "unverified";

export interface LightState {
  id: string;
  room: string;
  label: string;
  luminosity: number;
  warmth: "warm" | "neutral" | "cool";
  color: string;
}

export interface BlindState {
  id: string;
  room: string;
  label: string;
  position: number;
}

export interface MediaState {
  room: string;
  provider: "none" | "spotify" | "local";
  sound_id: string | null;
  title: string | null;
  volume: number | null;
  playback: "idle" | "requesting" | "playing" | "paused" | "error";
  error_code: string | null;
  verification: Verification | null;
  simulated: boolean;
}

export interface FlashOverlay {
  id: string;
  room: string;
  light_ids: string[];
  color: "blue" | "amber" | "white";
  pulses: number;
  started_at: string;
  ends_at: string;
  on_ms: number;
  off_ms: number;
}

export interface Caption {
  id: string;
  text: string;
  priority: "normal" | "high";
  created_at: string;
  expires_at: string | null;
  source: string;
  resolved: boolean;
  message_id: string | null;
  speak: boolean;
  audio_url: string | null;
  playback: "none" | "generated" | "started" | "ended" | "failed" | "blocked";
}

export interface AlertView {
  incident_id: string;
  severity: "info" | "high";
  message: string;
  status: "submitting" | "accepted" | "delivered" | "failed" | "unknown" | "cancelled";
  verification: Verification;
  provider_reference: string | null;
  error_code: string | null;
  acknowledged: boolean;
}

export interface IncidentView {
  incident_id: string;
  phase: string;
  room: string;
  audio_failed: boolean;
  question_id: string | null;
  question_text: string | null;
}

export interface HomeSnapshot {
  revision: number;
  updated_at: string;
  rooms: string[];
  lights: LightState[];
  blinds: BlindState[];
  media: MediaState[];
  flashes: FlashOverlay[];
  captions: Caption[];
  alerts: AlertView[];
  incident: IncidentView | null;
}

export interface Activity {
  id: string;
  planId: string;
  tool: string;
  status: string;
  verification: string | null;
  errorCode: string | null;
  detail: string | null;
}

export interface Question {
  questionId: string;
  text: string;
  choices: string[];
  incidentId?: string;
}

export interface UiEvent {
  event_id: string;
  session_id: string;
  sequence: number;
  timestamp: string;
  type: string;
  payload: Record<string, unknown>;
}

export interface AppState {
  revision: number;
  lights: LightState[];
  blinds: BlindState[];
  media: MediaState[];
  flashes: FlashOverlay[];
  captions: Caption[];
  alerts: AlertView[];
  incident: IncidentView | null;
  activity: Activity[];
  questions: Question[];
  connected: boolean;
  executorOnline: boolean;
  listening: string;
  lastSequence: number;
  historicalSpeech: string[];
  speechQueue: string[];
  playedSpeech: string[];
}

export function emptyState(): AppState {
  return {
    revision: 0,
    lights: [],
    blinds: [],
    media: [],
    flashes: [],
    captions: [],
    alerts: [],
    incident: null,
    activity: [],
    questions: [],
    connected: false,
    executorOnline: false,
    listening: "paused",
    lastSequence: 0,
    historicalSpeech: [],
    speechQueue: [],
    playedSpeech: [],
  };
}
