import {
  Activity,
  AppState,
  Caption,
  HomeSnapshot,
  UiEvent,
  emptyState,
} from "./types";

export function createState(): AppState {
  return emptyState();
}

export function reduce(state: AppState, event: UiEvent): AppState {
  if (event.sequence > 0 && event.sequence <= state.lastSequence && event.type !== "home.snapshot") {
    return state;
  }
  const next = { ...state, lastSequence: Math.max(state.lastSequence, event.sequence) };
  switch (event.type) {
    case "session.hello":
      return {
        ...next,
        connected: true,
        executorOnline: Boolean(event.payload.executor_online),
        listening: String(event.payload.listening ?? state.listening),
      };
    case "executor.status":
      return { ...next, executorOnline: Boolean(event.payload.online) };
    case "listening.state":
      return { ...next, listening: String(event.payload.state ?? state.listening) };
    case "home.snapshot":
      return applySnapshot(next, event.payload as unknown as HomeSnapshot, false);
    case "plan.proposed":
      return { ...next, activity: proposedActivity(next.activity, event.payload) };
    case "action.started":
      return { ...next, activity: markActivity(next.activity, event.payload, "executing") };
    case "action.result":
      return { ...next, activity: resultActivity(next.activity, event.payload) };
    case "plan.decision":
      if (event.payload.decision === "rejected") {
        return {
          ...next,
          activity: next.activity.map((item) =>
            item.planId === event.payload.plan_id && item.status === "requested"
              ? { ...item, status: "failed", errorCode: String(event.payload.reason ?? "rejected") }
              : item,
          ),
        };
      }
      return next;
    case "question.opened":
      return {
        ...next,
        questions: [
          ...next.questions.filter((item) => item.questionId !== event.payload.question_id),
          {
            questionId: String(event.payload.question_id),
            text: String(event.payload.text ?? ""),
            choices: Array.isArray(event.payload.choices) ? event.payload.choices.map(String) : [],
            incidentId: event.payload.incident_id ? String(event.payload.incident_id) : undefined,
          },
        ],
      };
    case "question.resolved":
      return {
        ...next,
        questions: next.questions.filter((item) => item.questionId !== event.payload.question_id),
      };
    case "incident.updated":
      if (!event.payload.incident_id || event.payload.phase === "resolved_ok" || event.payload.phase === "cancelled") {
        return { ...next, incident: null };
      }
      return {
        ...next,
        incident: {
          incident_id: String(event.payload.incident_id),
          phase: String(event.payload.phase ?? ""),
          room: String(event.payload.room ?? ""),
          audio_failed: Boolean(event.payload.audio_failed),
          question_id: event.payload.question_id ? String(event.payload.question_id) : null,
          question_text: event.payload.question_text ? String(event.payload.question_text) : null,
        },
      };
    default:
      return next;
  }
}

export function applySnapshot(state: AppState, snapshot: HomeSnapshot, historical: boolean): AppState {
  if (!snapshot || typeof snapshot.revision !== "number") {
    return state;
  }
  if (snapshot.revision < state.revision) {
    return state;
  }
  if (snapshot.revision === state.revision && state.revision !== 0) {
    return state;
  }
  const captions = snapshot.captions ?? [];
  const speechIds = captions.filter((caption) => caption.speak && caption.audio_url).map((caption) => caption.id);
  if (historical || state.revision === 0) {
    return {
      ...state,
      ...snapshotFields(snapshot),
      historicalSpeech: unique([...state.historicalSpeech, ...speechIds]),
      speechQueue: state.speechQueue,
    };
  }
  const fresh = speechIds.filter(
    (id) => !state.historicalSpeech.includes(id) && !state.playedSpeech.includes(id) && !state.speechQueue.includes(id),
  );
  return {
    ...state,
    ...snapshotFields(snapshot),
    speechQueue: [...state.speechQueue, ...fresh],
  };
}

export function markSpeechPlayed(state: AppState, messageId: string): AppState {
  return {
    ...state,
    speechQueue: state.speechQueue.filter((id) => id !== messageId),
    playedSpeech: unique([...state.playedSpeech, messageId]),
    historicalSpeech: unique([...state.historicalSpeech, messageId]),
  };
}

function snapshotFields(snapshot: HomeSnapshot) {
  return {
    revision: snapshot.revision,
    lights: snapshot.lights ?? [],
    blinds: snapshot.blinds ?? [],
    media: snapshot.media ?? [],
    flashes: snapshot.flashes ?? [],
    captions: snapshot.captions ?? [],
    alerts: snapshot.alerts ?? [],
    incident: snapshot.incident ?? null,
  };
}

function proposedActivity(activity: Activity[], payload: Record<string, unknown>): Activity[] {
  const plan = (payload.plan ?? {}) as Record<string, unknown>;
  const planId = String(plan.plan_id ?? "");
  const actions = Array.isArray(plan.actions) ? plan.actions : [];
  const additions = actions.map((action) => {
    const item = action as Record<string, unknown>;
    return {
      id: String(item.action_id),
      planId,
      tool: String(item.tool),
      status: "requested",
      verification: null,
      errorCode: null,
      detail: null,
    } satisfies Activity;
  });
  return [...activity.filter((item) => item.planId !== planId), ...additions];
}

function markActivity(activity: Activity[], payload: Record<string, unknown>, status: string): Activity[] {
  const id = String(payload.action_id ?? "");
  if (!activity.some((item) => item.id === id)) {
    return [
      ...activity,
      {
        id,
        planId: String(payload.plan_id ?? ""),
        tool: String(payload.tool ?? ""),
        status,
        verification: null,
        errorCode: null,
        detail: null,
      },
    ];
  }
  return activity.map((item) => (item.id === id ? { ...item, status } : item));
}

function resultActivity(activity: Activity[], payload: Record<string, unknown>): Activity[] {
  const result = (payload.result ?? payload) as Record<string, unknown>;
  const id = String(result.action_id ?? "");
  const updated: Activity = {
    id,
    planId: String(result.plan_id ?? ""),
    tool: String(result.tool ?? ""),
    status: String(result.status ?? "unknown"),
    verification: result.verification ? String(result.verification) : null,
    errorCode: result.error_code ? String(result.error_code) : null,
    detail: result.detail ? String(result.detail) : null,
  };
  if (!activity.some((item) => item.id === id)) {
    return [...activity, updated];
  }
  return activity.map((item) => (item.id === id ? updated : item));
}

function unique(values: string[]): string[] {
  return [...new Set(values)];
}

export function visibleCaptions(captions: Caption[], nowMs: number): Caption[] {
  return captions.filter((caption) => {
    if (caption.resolved) return false;
    if (caption.priority === "high") return true;
    if (!caption.expires_at) return true;
    return Date.parse(caption.expires_at) > nowMs;
  });
}

export function activeFlashes(state: AppState, nowMs: number) {
  return state.flashes.filter((flash) => Date.parse(flash.ends_at) > nowMs);
}
