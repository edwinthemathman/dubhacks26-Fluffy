import { currentAction, thinkingLabel, type SensingFacts } from "./phrases";
import type { ActionModel, SessionState } from "./types";
import { activityTurn } from "./reducer";

/** One loop, summed from each GIF's graphic-control delays. */
export const MASCOT_MS = {
  greeting: 1650,
  happy: 2100,
} as const;

export type MascotActivity = "idle" | "greeting" | "working" | "success" | "static";

export type MascotSprite = "idle" | "greeting" | "walk" | "happy";

export type MascotCue = {
  activity: MascotActivity;
  /** Stable for a confirmed completion so the success loop plays once per action. */
  playKey: string;
  status: string;
};

const EMERGENCY_TOOLS = new Set(["alert_caregiver", "flash_lights"]);

const quietSensing: SensingFacts = {
  started: false,
  paused: false,
  preview: false,
  cameraOn: false,
  transport: "idle",
  inferenceAvailable: false,
};

export function mascotCue(
  state: SessionState,
  sensing: SensingFacts = quietSensing,
  options?: { greet?: boolean; fault?: string | null },
): MascotCue {
  const action = currentAction(state);
  const activity = resolveActivity(state, sensing, action, options);
  return {
    activity,
    playKey: activity === "success" && action ? action.id : activity,
    status: statusText(state, sensing, activity, options?.fault),
  };
}

export function historyActivity(action: ActionModel, options?: { actionClass?: string; recorded?: boolean; incident?: boolean }): Exclude<MascotActivity, "greeting"> {
  if (action.status === "failed" || action.status === "unknown") return "static";
  if (action.status === "started") return "working";
  if (routineSuccess(action, options?.actionClass, options?.recorded === true, options?.incident === true)) return "success";
  return "idle";
}

export function spriteFor(activity: MascotActivity): MascotSprite {
  if (activity === "greeting") return "greeting";
  if (activity === "working") return "walk";
  if (activity === "success") return "happy";
  return "idle";
}

function resolveActivity(
  state: SessionState,
  sensing: SensingFacts,
  action: ActionModel | null,
  options?: { greet?: boolean; fault?: string | null },
): MascotActivity {
  if (state.connection !== "open") return "static";

  const question = pendingQuestion(state);
  if (question) return "idle";

  if (action?.status === "started") return "working";
  if (state.inference === "running" || state.observationPending) return "working";

  if (action && (action.status === "failed" || action.status === "unknown")) return "static";
  if (isErrorNotice(state.notice) || options?.fault) return "static";

  if (action && routineSuccess(action, action.planId ? state.plans[action.planId]?.actionClass : undefined, Boolean(actionTurn(state, action)?.recorded), state.incident != null)) {
    return "success";
  }
  if (options?.greet) return "greeting";
  return "idle";
}

function routineSuccess(action: ActionModel, actionClass: string | undefined, recorded: boolean, incident: boolean): boolean {
  if (recorded || incident) return false;
  if (action.status !== "verified" && action.status !== "delivered") return false;
  if (EMERGENCY_TOOLS.has(action.tool) || actionClass === "safety") return false;
  return true;
}

function pendingQuestion(state: SessionState): string | null {
  if (state.question && !state.question.answer) return state.question.text;
  const turn = activityTurn(state);
  if (turn?.recorded && turn.recordedConfirm && !turn.recordedConfirmed) return turn.recordedConfirm.question;
  return null;
}

function actionTurn(state: SessionState, action: ActionModel) {
  return state.turns.find((turn) => turn.id === action.turnId || turn.actionIds.includes(action.id));
}

function statusText(state: SessionState, sensing: SensingFacts, activity: MascotActivity, fault?: string | null): string {
  if (state.connection !== "open") {
    if (isErrorNotice(state.notice)) return state.notice ?? "Offline";
    if (state.connection === "connecting") return "Connecting";
    if (state.connection === "reconnecting") return "Reconnecting";
    return "Offline";
  }
  const question = pendingQuestion(state);
  if (question) return question;
  const live = thinkingLabel(state, sensing);
  if (activity === "static") {
    if (isErrorNotice(state.notice)) return state.notice;
    if (fault) return fault;
    return live || "Something went wrong";
  }
  if (live) return live;
  if (fault) return fault;
  return "Ready";
}

function isErrorNotice(text: string | null): text is string {
  if (!text) return false;
  if (text.startsWith("Answer sent.")) return false;
  if (text.startsWith("Recorded demo is on.")) return false;
  return true;
}
