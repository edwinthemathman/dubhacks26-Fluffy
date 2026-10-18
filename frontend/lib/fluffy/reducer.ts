import { asLevel, asRecord, asString, asWarmth, LAMP_ID } from "./levels";
import { patchFromNewState } from "./normalizeEvent";
import { isLightTool, isMessageTool, isMusicTool, startedPhrase, statusPhrase, verifiedPhrase } from "./phrases";
import type {
  ActionModel,
  ActionResultFact,
  ActionStatus,
  CanonicalEvent,
  HomeModel,
  HomePatch,
  PlanModel,
  SessionState,
  SpeakerModel,
  TurnModel,
} from "./types";

const TURN_LIMIT = 12;
const SEEN_LIMIT = 500;
const PLAYED_LIMIT = 200;

export function emptyHome(): HomeModel {
  return {
    known: false,
    revision: null,
    lights: {},
    blinds: {},
    speaker: { sound: null, volume: null, playback: "unknown", confirmed: false },
    flash: null,
    caption: null,
    caregiver: null,
    caregiverName: null,
  };
}

export function createSession(sessionId = "local"): SessionState {
  return {
    sessionId,
    streamSession: null,
    epoch: 1,
    lastSeq: null,
    seen: [],
    connection: "offline",
    executorConnected: null,
    executorStatus: "",
    home: emptyHome(),
    turns: [],
    plans: {},
    actions: {},
    question: null,
    incident: null,
    speechQueue: [],
    playedIds: [],
    speechStatus: {},
    captions: [],
    capabilities: { text: "unknown", questions: "unknown", media: "unknown", stop: "unknown", inference: "unknown" },
    capabilityNotes: {},
    inference: "idle",
    observationPending: false,
    notice: null,
    inputAccepted: false,
  };
}

export function reduceSession(state: SessionState, event: CanonicalEvent, options?: { historical?: boolean; now?: number }): SessionState {
  const now = options?.now ?? Date.now();
  const historical = options?.historical === true;
  let next = noteStream(state, event);
  if (next == null) return state;
  next = apply(next, event, now, historical);
  return next;
}

/** Null means the event was a duplicate in this epoch. */
function noteStream(state: SessionState, event: CanonicalEvent): SessionState | null {
  let next = state;
  if (event.sessionId && next.streamSession && event.sessionId !== next.streamSession) {
    next = newEpoch(next, event.sessionId);
  } else if (event.sessionId && !next.streamSession) {
    next = { ...next, streamSession: event.sessionId };
  }
  if (event.seq != null && next.lastSeq != null && event.seq < next.lastSeq) {
    next = newEpoch(next, event.sessionId ?? next.streamSession);
  }
  if (event.seq != null) next = { ...next, lastSeq: event.seq };
  const key = event.dedupKey ?? defaultKey(event);
  if (key && next.seen.includes(key)) return null;
  if (key) next = { ...next, seen: trim([...next.seen, key], SEEN_LIMIT) };
  return next;
}

function newEpoch(state: SessionState, streamSession: string | null): SessionState {
  const played = new Set(state.playedIds);
  for (const item of state.speechQueue) played.add(item.messageId);
  return {
    ...state,
    epoch: state.epoch + 1,
    streamSession,
    lastSeq: null,
    seen: [],
    speechQueue: [],
    playedIds: trim([...played], PLAYED_LIMIT),
    home: { ...state.home, revision: null },
  };
}

function defaultKey(event: CanonicalEvent): string | null {
  if (event.type === "plan") return `plan:${event.plan.planId}`;
  if (event.type === "plan.decision") return `plan-decision:${event.planId}:${event.decision}`;
  if (event.type === "plan.result") return `plan-result:${event.planId}`;
  if (event.type === "say") return `say:${event.messageId}`;
  if (event.type === "speech") return `speech:${event.messageId}:${event.status}`;
  if (event.type === "ask") return `ask:${event.questionId}`;
  if (event.type === "action") return `action:${event.actionId}:${event.status}`;
  if (event.type === "snapshot" && event.patch.revision != null) return `snapshot:${event.patch.revision}`;
  if (event.type === "home" && event.patch.revision != null) return `home:${event.patch.revision}`;
  if (event.type === "incident") return `incident:${event.incident.id}:${event.incident.status}`;
  return null;
}

function apply(state: SessionState, event: CanonicalEvent, now: number, historical: boolean): SessionState {
  switch (event.type) {
    case "connection":
      return { ...state, connection: event.status, notice: event.status === "open" ? state.notice : state.notice };
    case "capability":
      return {
        ...state,
        capabilities: { ...state.capabilities, [event.name]: event.available ? "available" : "missing" },
        capabilityNotes: { ...state.capabilityNotes, [event.name]: event.detail },
      };
    case "executor":
      return { ...state, executorConnected: event.connected, executorStatus: event.status };
    case "snapshot": {
      if (event.patch.revision != null && state.home.revision != null && event.patch.revision < state.home.revision) return state;
      return { ...state, home: applyPatch(emptyHome(), { ...event.patch, replace: true }, now, state.home) };
    }
    case "home":
      return { ...state, home: applyPatch(state.home, event.patch, now, state.home) };
    case "submit":
      return pushTurn(state, {
        id: event.requestId,
        requestId: event.requestId,
        createdAt: now,
        source: "text",
        text: event.text,
        observations: [],
        actionIds: [],
        open: true,
        recorded: false,
      }, { ...state, inference: "running", notice: null, inputAccepted: false });
    case "submit.accepted":
      return { ...state, inputAccepted: true, inference: "running" };
    case "submit.failed":
      return {
        ...state,
        inference: "idle",
        inputAccepted: false,
        notice: event.detail,
        turns: state.turns.map((turn) => (turn.requestId === event.requestId ? { ...turn, open: false } : turn)),
      };
    case "turn.start":
      return startTurn(state, event, now);
    case "turn.end":
      return {
        ...state,
        inference: "idle",
        observationPending: false,
        turns: state.turns.map((turn) => (turn.id === event.turnId || turn.requestId === event.turnId ? { ...turn, open: false } : turn)),
      };
    case "observation":
      return addObservation(state, event.text, event.turnId, now, event.pending === true);
    case "say":
      return addSay(state, event, now, historical);
    case "speech":
      return {
        ...state,
        speechStatus: { ...state.speechStatus, [event.messageId]: event.status },
        speechQueue: event.status === "played" || event.status === "failed" ? state.speechQueue.filter((item) => item.messageId !== event.messageId) : state.speechQueue,
        playedIds: event.status === "played" || event.status === "failed" ? trim([...state.playedIds, event.messageId], PLAYED_LIMIT) : state.playedIds,
      };
    case "ask":
      return {
        ...state,
        question: { id: event.questionId, turnId: event.turnId, text: event.question, choices: event.choices.length ? event.choices : ["Yes", "No"] },
      };
    case "ask.resolved":
      if (state.question && event.questionId && state.question.id !== event.questionId) return state;
      return { ...state, question: null };
    case "plan":
      return addPlan(state, event.plan, event.turnId, now);
    case "plan.decision":
      return decidePlan(state, event.planId, event.decision, event.reason, now);
    case "plan.result":
      return applyPlanResult(state, event.planId, event.status, event.results, now);
    case "action":
      return upsertAction(state, event, now);
    case "incident":
      return { ...state, incident: event.incident };
    case "recorded":
      return pushTurn({ ...state, inference: "idle" }, {
        id: event.requestId,
        requestId: event.requestId,
        createdAt: now,
        source: "recorded",
        text: event.text,
        observations: [{ text: "Recorded demo", at: now }],
        planId: event.plan.planId,
        actionIds: event.plan.actions.map((action) => action.actionId),
        open: false,
        recorded: true,
        recordedRows: event.rows,
        recordedSpoken: event.spoken,
        recordedConfirm: event.confirm,
        recordedConfirmed: false,
      }, {
        ...state,
        plans: { ...state.plans, [event.plan.planId]: event.plan },
        inference: "idle",
        notice: null,
      });
    case "notice":
      return { ...state, notice: event.text };
    case "recorded.confirm":
      return {
        ...state,
        turns: state.turns.map((turn) => {
          if (turn.requestId !== event.requestId || !turn.recordedConfirm) return turn;
          return {
            ...turn,
            recordedConfirmed: true,
            recordedRows: [...(turn.recordedRows ?? []), turn.recordedConfirm.then],
          };
        }),
      };
    default:
      return state;
  }
}

function startTurn(state: SessionState, event: Extract<CanonicalEvent, { type: "turn.start" }>, now: number): SessionState {
  const existing = event.requestId ? state.turns.find((turn) => turn.requestId === event.requestId) : undefined;
  if (existing) {
    return {
      ...state,
      inference: event.source === "text" ? "running" : state.inference,
      turns: state.turns.map((turn) => (turn === existing ? { ...turn, id: event.turnId, source: event.source, text: event.text ?? turn.text, open: true } : turn)),
    };
  }
  return pushTurn(state, {
    id: event.turnId,
    requestId: event.requestId,
    createdAt: now,
    source: event.source,
    text: event.text,
    observations: [],
    actionIds: [],
    open: true,
    recorded: false,
  }, { ...state, inference: event.source === "text" ? "running" : state.inference, observationPending: event.source === "vision" });
}

function pushTurn(state: SessionState, turn: TurnModel, base: SessionState = state): SessionState {
  return { ...base, turns: trim([...base.turns, turn], TURN_LIMIT) };
}

function addObservation(state: SessionState, text: string, turnId: string | undefined, now: number, pending: boolean): SessionState {
  const target = turnId ?? [...state.turns].reverse().find((turn) => turn.open)?.id;
  if (!target) {
    return addObservation(startTurn(state, { type: "turn.start", turnId: turnId ?? `turn_${now}`, source: "system" }, now), text, turnId ?? `turn_${now}`, now, pending);
  }
  return {
    ...state,
    observationPending: pending,
    inference: pending ? state.inference : state.inference,
    turns: state.turns.map((turn) => {
      if (turn.id !== target && turn.requestId !== target) return turn;
      const observations = trim([...turn.observations, { text, at: now }], 8);
      return { ...turn, observations };
    }),
  };
}

function addSay(state: SessionState, event: Extract<CanonicalEvent, { type: "say" }>, now: number, historical: boolean): SessionState {
  const captions = trim([...state.captions, { messageId: event.messageId, text: event.text, at: now }], 20);
  const home = { ...state.home, caption: { text: event.text, until: null } };
  if (historical || state.playedIds.includes(event.messageId) || !event.permitAudio) {
    return {
      ...state,
      home,
      captions,
      playedIds: trim([...state.playedIds, event.messageId], PLAYED_LIMIT),
      speechStatus: { ...state.speechStatus, [event.messageId]: event.permitAudio && !historical ? state.speechStatus[event.messageId] ?? "unavailable" : "unavailable" },
    };
  }
  if (!event.audioUrl) {
    return {
      ...state,
      home,
      captions,
      speechStatus: { ...state.speechStatus, [event.messageId]: "unavailable" },
      playedIds: trim([...state.playedIds, event.messageId], PLAYED_LIMIT),
    };
  }
  return {
    ...state,
    home,
    captions,
    speechQueue: [...state.speechQueue, { messageId: event.messageId, text: event.text, audioUrl: event.audioUrl, permitAudio: true }],
  };
}

function addPlan(state: SessionState, plan: PlanModel, turnId: string | undefined, now: number): SessionState {
  const actions = { ...state.actions };
  const actionIds: string[] = [];
  for (const action of plan.actions) {
    actionIds.push(action.actionId);
    actions[action.actionId] = withSummary({
      id: action.actionId,
      planId: plan.planId,
      turnId,
      tool: action.tool,
      args: action.args,
      status: "proposed",
      updatedAt: now,
    }, state.home);
  }
  const turn = ensureTurn(state, turnId, now);
  return {
    ...turn.state,
    inference: "idle",
    plans: { ...turn.state.plans, [plan.planId]: plan },
    actions,
    turns: turn.state.turns.map((item) => (item.id === turn.id ? { ...item, planId: plan.planId, actionIds: [...item.actionIds, ...actionIds] } : item)),
  };
}

function decidePlan(state: SessionState, planId: string, decision: "approved" | "rejected", reason: string, now: number): SessionState {
  const plan = state.plans[planId];
  if (!plan) return state;
  const status: ActionStatus = decision === "approved" ? "approved" : "rejected";
  const actions = { ...state.actions };
  for (const action of Object.values(actions)) {
    if (action.planId !== planId) continue;
    if (action.status === "verified" || action.status === "failed" || action.status === "delivered") continue;
    actions[action.id] = withSummary({ ...action, status, detail: reason, updatedAt: now }, state.home);
  }
  return {
    ...state,
    plans: { ...state.plans, [planId]: { ...plan, decision, reason } },
    actions,
    inference: "idle",
  };
}

function applyPlanResult(state: SessionState, planId: string, resultStatus: string, results: ActionResultFact[], now: number): SessionState {
  const plan = state.plans[planId];
  let next: SessionState = { ...state, plans: plan ? { ...state.plans, [planId]: { ...plan, resultStatus } } : state.plans, inference: "idle" };
  for (const result of results) {
    const existing = next.actions[result.actionId];
    const tool = existing?.tool ?? "";
    const interpreted = interpretResult(tool, result, now);
    next = upsertAction(next, {
      type: "action",
      actionId: result.actionId,
      planId,
      tool: tool || undefined,
      status: interpreted.status,
      detail: interpreted.detail,
      patch: interpreted.patch,
    }, now);
  }
  return next;
}

function interpretResult(tool: string, result: ActionResultFact, now: number): { status: ActionStatus; detail?: string; patch?: HomePatch } {
  if (result.rawStatus === "error") return { status: "failed", detail: result.error ?? "Could not complete that" };
  if (result.rawStatus === "skipped" || result.rawStatus === "unknown") {
    return { status: "unknown", detail: result.error ?? (isMusicTool(tool) ? "Could not verify playback" : "Could not verify that action") };
  }
  if (isMessageTool(tool)) {
    const delivery = result.delivery ?? "accepted";
    if (delivery === "delivered") return { status: "delivered", detail: "Message delivered" };
    if (delivery === "failed") return { status: "failed", detail: result.error ?? "Message failed" };
    return { status: "accepted", detail: "Message submitted" };
  }
  const patch = patchFromNewState(result.newState, now) ?? undefined;
  if (isMusicTool(tool)) {
    const spotify = tool.includes("spotify") || tool === "play_music" || tool === "stop_music";
    if (spotify && !result.providerConfirmed) {
      return { status: "unknown", detail: "Could not verify playback" };
    }
    if (!patch && !result.providerConfirmed) return { status: "unknown", detail: "Could not verify playback" };
    const speaker: Partial<SpeakerModel> = {
      ...(patch?.speaker ?? {}),
      confirmed: true,
      playback: tool.startsWith("stop") ? "stopped" : patch?.speaker?.sound || result.providerConfirmed ? "playing" : (patch?.speaker?.playback ?? "playing"),
    };
    return { status: "verified", patch: { ...(patch ?? {}), speaker } };
  }
  if (!patch && !isLightTool(tool) && tool !== "set_blinds" && tool !== "show_caption" && tool !== "announce" && tool !== "flash_lights") {
    return { status: "unknown", detail: "Could not verify that action" };
  }
  if (!patch && (isLightTool(tool) || tool === "set_blinds")) {
    return { status: "unknown", detail: "Could not verify that action" };
  }
  return { status: "verified", patch };
}

function upsertAction(state: SessionState, event: Extract<CanonicalEvent, { type: "action" }>, now: number): SessionState {
  const previous = state.actions[event.actionId];
  const tool = event.tool ?? previous?.tool ?? "";
  const args = event.args ?? previous?.args ?? {};
  let home = state.home;
  if (event.patch && (event.status === "verified" || event.status === "delivered" || event.status === "accepted")) {
    home = applyPatch(state.home, event.patch, now, state.home);
  }
  const model: ActionModel = withSummary({
    id: event.actionId,
    planId: event.planId ?? previous?.planId,
    turnId: event.turnId ?? previous?.turnId,
    tool,
    args,
    status: event.status,
    detail: event.detail ?? previous?.detail,
    updatedAt: now,
  }, home);
  const actions = { ...state.actions, [event.actionId]: model };
  const turnId = model.turnId;
  let turns = state.turns;
  if (turnId) {
    const ensured = ensureTurn({ ...state, actions, home }, turnId, now);
    turns = ensured.state.turns.map((turn) => (turn.id === ensured.id && !turn.actionIds.includes(event.actionId) ? { ...turn, actionIds: [...turn.actionIds, event.actionId] } : turn));
    return { ...ensured.state, home, actions, turns };
  }
  const open = [...state.turns].reverse().find((turn) => turn.open);
  if (open) {
    turns = state.turns.map((turn) => (turn.id === open.id && !turn.actionIds.includes(event.actionId) ? { ...turn, actionIds: [...turn.actionIds, event.actionId] } : turn));
  }
  return { ...state, home, actions, turns };
}

function withSummary(action: ActionModel, home: HomeModel): ActionModel {
  if (action.status === "started") return { ...action, summary: startedPhrase(action.tool) };
  if (action.status === "verified") return { ...action, summary: verifiedPhrase(action, home) };
  return { ...action, summary: statusPhrase(action, home) };
}

function ensureTurn(state: SessionState, turnId: string | undefined, now: number): { state: SessionState; id: string } {
  if (!turnId) {
    const open = [...state.turns].reverse().find((turn) => turn.open);
    if (open) return { state, id: open.id };
    const id = `turn_${now}`;
    return { state: pushTurn(state, blankTurn(id, now)), id };
  }
  const existing = state.turns.find((turn) => turn.id === turnId || turn.requestId === turnId);
  if (existing) return { state, id: existing.id };
  return { state: pushTurn(state, blankTurn(turnId, now)), id: turnId };
}

function blankTurn(id: string, now: number): TurnModel {
  return { id, createdAt: now, source: "system", observations: [], actionIds: [], open: true, recorded: false };
}

export function applyPatch(home: HomeModel, patch: HomePatch, now: number, previous: HomeModel): HomeModel {
  if (patch.revision != null && home.revision != null && patch.revision < home.revision) {
    return home;
  }
  const replace = patch.replace === true;
  const lights = replace ? {} : { ...home.lights };
  for (const light of patch.lights ?? []) {
    const prior = lights[light.id];
    lights[light.id] = {
      id: light.id,
      luminosity: light.luminosity != null ? light.luminosity : prior?.luminosity ?? null,
      warmth: light.warmth ?? prior?.warmth ?? null,
    };
  }
  const blinds = replace ? {} : { ...home.blinds };
  for (const blind of patch.blinds ?? []) {
    blinds[blind.id] = { id: blind.id, position: blind.position };
  }
  let speaker = home.speaker;
  if (patch.speaker) {
    speaker = {
      sound: patch.speaker.sound !== undefined ? patch.speaker.sound : speaker.sound,
      volume: patch.speaker.volume !== undefined ? patch.speaker.volume : speaker.volume,
      playback: patch.speaker.playback ?? speaker.playback,
      confirmed: patch.speaker.confirmed ?? speaker.confirmed,
    };
  } else if (replace) {
    speaker = { sound: null, volume: null, playback: "unknown", confirmed: false };
  }
  let caregiver = patch.caregiver === undefined ? home.caregiver : patch.caregiver;
  if (caregiver && previous.caregiver && caregiver.alertId === previous.caregiver.alertId) {
    caregiver = { ...caregiver, animateUntil: previous.caregiver.animateUntil };
  }
  return {
    known: true,
    revision: patch.revision !== undefined ? patch.revision : home.revision,
    lights,
    blinds,
    speaker,
    flash: patch.flash === undefined ? (replace ? null : home.flash) : patch.flash,
    caption: patch.caption === undefined ? (replace ? null : home.caption) : patch.caption,
    caregiver,
    caregiverName: patch.caregiverName === undefined ? (replace ? null : home.caregiverName) : patch.caregiverName,
  };
}

export function levelFromArgs(args: Record<string, unknown>): number | null {
  return asLevel(args.luminosity ?? args.level ?? args.brightness);
}

export function warmthFromArgs(args: Record<string, unknown>): ReturnType<typeof asWarmth> {
  return asWarmth(args.warmth);
}

export function targetedLight(args: Record<string, unknown>): string {
  return asString(args.light) ?? LAMP_ID;
}

export function readSpeakerPatch(value: unknown): SpeakerModel | null {
  const row = asRecord(value);
  if (!row) return null;
  return {
    sound: row.sound === null ? null : asString(row.sound),
    volume: asLevel(row.volume),
    playback: row.playing === true ? "playing" : "unknown",
    confirmed: row.playing === true,
  };
}

function trim<T>(items: T[], limit: number): T[] {
  return items.length > limit ? items.slice(items.length - limit) : items;
}
