import { CEILING_ID, LAMP_ID } from "@/lib/fluffy/levels";
import type { ActionModel, HomeModel, QuestionModel } from "@/lib/fluffy/types";
import { isLightTool, isMessageTool, isMusicTool } from "@/lib/fluffy/phrases";
import type { PendingState, RoomObject, RoomState } from "./state";
import { unknownRoom } from "./state";

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

function targetsOf(action: ActionModel): RoomObject[] {
  if (action.tool === "set_blinds") return ["blinds"];
  if (isMusicTool(action.tool)) return ["speaker"];
  if (isMessageTool(action.tool)) return ["phone"];
  if (!isLightTool(action.tool)) return [];
  if (action.tool === "flash_lights" || action.tool === "set_light_warmth") return ["lamp", "ceiling"];
  if (action.args.light === CEILING_ID) return ["ceiling"];
  if (action.args.light === LAMP_ID) return ["lamp"];
  if (action.args.light == null) return ["lamp", "ceiling"];
  return [];
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
