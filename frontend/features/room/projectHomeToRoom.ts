import { CEILING_ID, FRONT_DOOR_ID, LAMP_ID, parseTime } from "@/lib/fluffy/levels";
import type { ActionModel, ActionStatus, HomeModel, PlanModel, QuestionModel, SessionState } from "@/lib/fluffy/types";
import type { PendingState, RoomState } from "./state";
import { unknownRoom } from "./state";
import { actionTargets } from "./toolMap";

const PENDING: ReadonlySet<ActionStatus> = new Set(["proposed", "approved", "started", "unknown"]);
const FAILED: ReadonlySet<ActionStatus> = new Set(["failed", "rejected"]);

/** Non-terminal markers from every live turn. Failed markers stay on the current turn. */
export function actionsForRoom(state: SessionState, now: number): ActionModel[] {
  const current = [...state.turns].reverse().find((turn) => !turn.recorded && turn.open) ?? [...state.turns].reverse().find((turn) => !turn.recorded);
  const chosen: ActionModel[] = [];
  for (const turn of state.turns) {
    if (turn.recorded) continue;
    for (const id of turn.actionIds) {
      const action = state.actions[id];
      if (!action) continue;
      const expired = isExpired(action, state.plans, now);
      if (PENDING.has(action.status) && expired) {
        chosen.push({ ...action, status: "failed", detail: action.detail ?? "Not confirmed" });
        continue;
      }
      if (FAILED.has(action.status)) {
        if (turn.id === current?.id) chosen.push(action);
        continue;
      }
      if (PENDING.has(action.status)) chosen.push(action);
    }
  }
  return chosen;
}

function isExpired(action: ActionModel, plans: Record<string, PlanModel>, now: number): boolean {
  const expires = action.planId ? plans[action.planId]?.expiresAt : undefined;
  const at = parseTime(expires);
  return at != null && at <= now;
}

export function projectHomeToRoom(input: {
  home: HomeModel;
  actions: ActionModel[];
  question: QuestionModel | null;
  now: number;
}): RoomState {
  const room = unknownRoom();
  const { home, now } = input;
  if (!home.known) {
    room.ask = questionOf(input.question);
    room.pending = pendingOf(input.actions);
    return room;
  }
  room.known = true;
  const lamp = home.lights[LAMP_ID];
  const ceiling = home.lights[CEILING_ID];
  if (lamp) room.lamp = { level: lamp.luminosity, warmth: lamp.warmth };
  if (ceiling) room.ceiling = { level: ceiling.luminosity, warmth: ceiling.warmth };
  const front = home.doors[FRONT_DOOR_ID];
  if (front) room.door = { open: front.open };
  const blind = Object.values(home.blinds)[0];
  if (blind) room.blinds = { position: blind.position };
  const speaker = home.speaker;
  room.speaker = {
    playing: speaker.confirmed && speaker.playback === "playing",
    pending: speaker.playback === "pending" || speaker.playback === "unknown" && speaker.sound != null && !speaker.confirmed,
    volume: speaker.volume,
    track: speaker.sound ?? undefined,
  };
  if (home.flash && now < home.flash.endedAt && now >= home.flash.startedAt) {
    room.flash = home.flash;
  } else {
    room.flash = null;
  }
  room.caption = home.caption?.text ?? null;
  if (home.call && now < home.call.until) {
    room.phone = {
      mode: "incoming",
      delivery: null,
      to: home.call.to,
      animateUntil: home.call.until,
    };
  }
  if (home.caregiver) {
    const alert = home.caregiver;
    const animating = now < alert.animateUntil;
    room.phone = {
      mode: animating ? "texting" : "idle",
      delivery: alert.delivery,
      to: alert.recipient ?? undefined,
      preview: alert.message,
      animateUntil: alert.animateUntil,
    };
  }
  room.ask = questionOf(input.question);
  room.pending = pendingOf(input.actions);
  return room;
}

function questionOf(question: QuestionModel | null): RoomState["ask"] {
  if (!question || question.answer) return null;
  return { question: question.text, choices: question.choices };
}

function pendingOf(actions: ActionModel[]): RoomState["pending"] {
  const pending: RoomState["pending"] = {};
  for (const action of actions) {
    const mark = markOf(action);
    if (!mark) continue;
    for (const target of targetsOf(action)) {
      pending[target] = stronger(pending[target], mark);
    }
  }
  return pending;
}

function targetsOf(action: ActionModel): ReturnType<typeof actionTargets> {
  return actionTargets(action.tool, action.args);
}

function markOf(action: ActionModel): PendingState | null {
  if (action.status === "failed" || action.status === "rejected") return "failed";
  if (action.status === "proposed" || action.status === "approved" || action.status === "started" || action.status === "unknown") return "pending";
  return null;
}

function stronger(current: PendingState | undefined, next: PendingState): PendingState {
  if (current === "failed" || next === "failed") return "failed";
  if (current === "asking" || next === "asking") return "asking";
  return "pending";
}
