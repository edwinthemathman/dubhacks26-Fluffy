import {
  asLevel,
  asRecord,
  asString,
  asWarmth,
  BLINDS_ID,
  CEILING_ID,
  cssColor,
  finiteNumber,
  LAMP_ID,
  parseTime,
  pulsesFor,
} from "./levels";
import type {
  ActionResultFact,
  CanonicalEvent,
  EvidenceRow,
  HomePatch,
  LightReading,
  LivePlan,
} from "./types";

export type NormalizeContext = { now: number };

/** One backend message becomes zero or more canonical events. Invalid shapes are dropped. */
export function normalizeEvent(raw: unknown, ctx: NormalizeContext): CanonicalEvent[] {
  const event = unwrapEnvelope(raw);
  if (!event) return [];
  const type = asString(event.type);
  if (!type) return [];
  const meta = streamMeta(event);
  const body = parse(type, event, ctx);
  if (!body) return [];
  return body.map((item) => ({ ...meta, ...item }));
}

function unwrapEnvelope(raw: unknown): Record<string, unknown> | null {
  const event = asRecord(raw);
  if (!event) return null;
  const payload = asRecord(event.payload);
  const type = asString(event.type);
  const enveloped = payload != null && type != null && (event.event_id != null || event.sequence != null);
  if (!enveloped || !type || !payload) return event;
  const flat: Record<string, unknown> = {
    ...payload,
    type,
    event_id: event.event_id,
    session_id: event.session_id ?? payload.session_id,
    sequence: event.sequence,
    timestamp: event.timestamp,
  };
  const nested = asRecord(payload.result);
  if (nested && (type === "action.result" || type === "plan.result")) {
    return { ...flat, ...nested, type };
  }
  return flat;
}

function parse(type: string, event: Record<string, unknown>, ctx: NormalizeContext): CanonicalEvent[] | null {
  if (type === "state.snapshot" || type === "home.state" || type === "home.snapshot" || type === "snapshot") {
    const patch = parseHomePayload(event.state ?? event.home ?? event, ctx.now);
    if (!patch) return null;
    const revision = finiteNumber(event.revision);
    return [{ type: "snapshot", patch: { ...patch, replace: true, revision: revision ?? patch.revision ?? null } }];
  }
  if (type === "state") {
    const patch = parseHomePayload(event.state ?? event, ctx.now);
    if (!patch) return null;
    const revision = finiteNumber(event.revision);
    return [{ type: event.state ? "snapshot" : "home", patch: { ...patch, revision: revision ?? patch.revision, replace: Boolean(event.state) } }];
  }
  if (type === "turn.start") return turnStart(event);
  if (type === "turn.end") {
    const turnId = asString(event.turn_id ?? event.turnId);
    if (!turnId) return null;
    return [{ type: "turn.end", turnId }];
  }
  if (type === "think" || type === "status") return observation(event);
  if (type === "inference.status") return inferenceStatus(event);
  if (type === "inference.error") return inferenceError(event);
  if (type === "sense.see" || type === "sense.hear") return sense(type, event);
  if (type === "say" || type === "output.message") return say(event);
  if (type === "speech.status") return speech(event);
  if (type === "ask") return ask(event);
  if (type === "question.opened") return ask({ ...event, question: event.question ?? event.text });
  if (type === "ask.answer" || type === "ask.resolved" || type === "question.resolved") return askResolved(event);
  if (type === "plan.proposed") return planProposed(event);
  if (type === "plan.decision") return planDecision(event);
  if (type === "plan.result") return planResult(event);
  if (type === "plan.cancel") return planCancel(event);
  if (type === "tool.call") return toolCall(event);
  if (type === "tool.status") return toolStatus(event);
  if (type.startsWith("action.")) return actionEvent(type, event, ctx.now);
  if (type === "incident.updated" || type === "incident") return incident(event);
  if (type === "executor.status") {
    const online = event.online === true || event.connected === true;
    const status = asString(event.status) ?? (online ? "executor online" : "executor offline");
    return [{ type: "executor", connected: online, status }];
  }
  if (type === "caregiver.alert") return caregiverAlert(event, ctx.now);
  return null;
}

function streamMeta(event: Record<string, unknown>): { dedupKey?: string; seq?: number; sessionId?: string } {
  const seq = finiteNumber(event.seq ?? event.sequence);
  const sessionId = asString(event.session_id ?? event.sessionId) ?? undefined;
  return { seq: seq ?? undefined, sessionId };
}

function turnStart(event: Record<string, unknown>): CanonicalEvent[] | null {
  const turnId = asString(event.turn_id ?? event.turnId);
  if (!turnId) return null;
  const kind = asString(event.kind ?? event.source);
  const source = kind === "spoken" || kind === "text" || kind === "speech" ? "text" : kind === "sound" || kind === "audio" ? "audio" : kind === "inferred" || kind === "vision" ? "vision" : "system";
  return [{
    type: "turn.start",
    turnId,
    requestId: asString(event.request_id ?? event.requestId) ?? undefined,
    source,
    text: asString(event.trigger ?? event.text ?? event.utterance) ?? undefined,
  }];
}

function inferenceStatus(event: Record<string, unknown>): CanonicalEvent[] | null {
  const stage = asString(event.stage);
  const status = asString(event.status);
  if (!stage || !status) return null;
  const label =
    stage === "text" && status === "started" ? "Interpreting request"
    : stage === "text" && status === "completed" ? "Interpreting request"
    : stage === "scene" && status === "started" ? "Reading the camera"
    : stage === "scene" && status === "completed" ? "Camera frame analyzed"
    : stage === "decide" && status === "completed" ? "Deciding"
    : stage === "sound" && status === "started" ? "Classifying a sound"
    : stage === "sound" && status === "completed" ? "Sound classified"
    : stage === "submit" ? "Sending the proposal"
    : stage === "execution" && status === "completed" ? "One execution"
    : null;
  if (!label) return null;
  const completed = status === "completed";
  const step =
    completed && stage === "scene" ? "Saw"
    : completed && (stage === "decide" || stage === "text") ? "Decision"
    : completed && stage === "execution" ? "Execution"
    : undefined;
  const thinking = asString(event.thinking) ?? undefined;
  return [{ type: "stage", label, analyzed: stage === "scene" && completed, step, thinking }];
}

function inferenceError(event: Record<string, unknown>): CanonicalEvent[] | null {
  const message = asString(event.message ?? event.note);
  if (!message) return null;
  return [{
    type: "inference.error",
    turnId: asString(event.turn_id ?? event.turnId ?? event.request_id) ?? undefined,
    message,
  }];
}

function observation(event: Record<string, unknown>): CanonicalEvent[] | null {
  const text = asString(event.note ?? event.text ?? event.status);
  if (!text || text.length > 180) return null;
  if (/chain[- ]of[- ]thought|reasoning trace/i.test(text)) return null;
  return [{
    type: "observation",
    turnId: asString(event.turn_id ?? event.turnId) ?? undefined,
    text,
    pending: event.pending === true,
  }];
}

function sense(type: string, event: Record<string, unknown>): CanonicalEvent[] | null {
  const turnId = asString(event.turn_id ?? event.turnId) ?? undefined;
  if (type === "sense.hear") {
    const text = asString(event.text ?? event.transcript);
    const sound = asString(event.sound);
    const line = text ? `Heard “${text}”` : sound ? `Heard ${sound}` : null;
    if (!line) return null;
    return [{ type: "observation", turnId, text: line, pending: false }];
  }
  const chips = Array.isArray(event.chips) ? event.chips.filter((item): item is string => typeof item === "string" && item.trim().length > 0) : [];
  const summary = asString(event.summary) ?? (chips.length ? chips.join(", ") : null);
  if (!summary) return [{ type: "observation", turnId, text: "Checking the room", pending: true }];
  return [{ type: "observation", turnId, text: `Saw ${summary}`, pending: false }];
}

function say(event: Record<string, unknown>): CanonicalEvent[] | null {
  const text = asString(event.text);
  const messageId = asString(event.say_id ?? event.sayId ?? event.message_id ?? event.messageId);
  if (!text || !messageId) return null;
  const audioUrl = asString(event.audio_url ?? event.audioUrl) ?? undefined;
  const permit = event.voice === true || event.audio === true || Boolean(audioUrl);
  return [{ type: "say", messageId, turnId: asString(event.turn_id ?? event.turnId) ?? undefined, text, permitAudio: permit, audioUrl }];
}

function speech(event: Record<string, unknown>): CanonicalEvent[] | null {
  const messageId = asString(event.message_id ?? event.messageId ?? event.say_id);
  const status = asString(event.status);
  if (!messageId || (status !== "generated" && status !== "playing" && status !== "played" && status !== "failed")) return null;
  return [{ type: "speech", messageId, status }];
}

function ask(event: Record<string, unknown>): CanonicalEvent[] | null {
  const question = asString(event.question);
  if (!question) return null;
  const choices = Array.isArray(event.choices) ? event.choices.filter((item): item is string => typeof item === "string" && item.trim().length > 0) : [];
  const questionId = asString(event.question_id ?? event.questionId ?? event.ask_id) ?? `ask:${question}`;
  return [{ type: "ask", questionId, turnId: asString(event.turn_id ?? event.turnId) ?? undefined, question, choices }];
}

function askResolved(event: Record<string, unknown>): CanonicalEvent[] | null {
  const questionId = asString(event.question_id ?? event.questionId ?? event.ask_id) ?? "";
  return [{ type: "ask.resolved", questionId, answer: asString(event.choice ?? event.answer ?? event.text) ?? undefined }];
}

function planProposed(event: Record<string, unknown>): CanonicalEvent[] | null {
  const plan = asRecord(event.plan);
  if (!plan) return null;
  const live = toLivePlan(plan);
  if (!live) return null;
  return [{ type: "plan", plan: live, turnId: asString(event.turn_id ?? event.turnId) ?? undefined }];
}

function toLivePlan(plan: Record<string, unknown>): LivePlan | null {
  const planId = asString(plan.plan_id ?? plan.planId);
  const intent = asString(plan.intent);
  const actionsRaw = Array.isArray(plan.actions) ? plan.actions : null;
  if (!planId || !intent || !actionsRaw || actionsRaw.length === 0) return null;
  if (plan.confidence != null && finiteNumber(plan.confidence) == null) return null;
  const actions = [];
  for (const item of actionsRaw) {
    const row = asRecord(item);
    if (!row) return null;
    const actionId = asString(row.action_id ?? row.actionId);
    const tool = asString(row.tool);
    const args = asRecord(row.args) ?? {};
    if (!actionId || !tool) return null;
    if (!argsFinite(args)) return null;
    actions.push({ actionId, tool, args });
  }
  const trigger = asRecord(plan.trigger);
  const consent = asRecord(plan.consent);
  const evidence = Array.isArray(plan.evidence) ? plan.evidence.map(evidenceRow).filter((item): item is EvidenceRow => item != null) : [];
  return {
    planId,
    createdAt: asString(plan.created_at ?? plan.createdAt) ?? "",
    expiresAt: asString(plan.expires_at ?? plan.expiresAt) ?? undefined,
    intent,
    reasoning: asString(plan.reasoning_summary ?? plan.reasoning) ?? "",
    confidence: finiteNumber(plan.confidence),
    triggerKind: asString(trigger?.kind) ?? "unknown",
    room: asString(trigger?.room) ?? "",
    actionClass: asString(plan.action_class ?? plan.actionClass) ?? undefined,
    consentRequired: consent ? consent.required === true : plan.requires_confirmation === true,
    question: asString(consent?.question) ?? undefined,
    evidence,
    actions,
  };
}

function argsFinite(args: Record<string, unknown>): boolean {
  for (const value of Object.values(args)) {
    if (typeof value === "number" && !Number.isFinite(value)) return false;
  }
  return true;
}

function evidenceRow(value: unknown): EvidenceRow | null {
  const row = asRecord(value);
  if (!row) return null;
  const kind = asString(row.kind) ?? "state";
  if (kind === "speech") {
    const text = asString(row.text);
    return text ? { kind, text: `Heard “${text}”` } : null;
  }
  if (kind === "vision") {
    const summary = asString(row.summary);
    return summary ? { kind, text: summary } : null;
  }
  if (kind === "sound") {
    const text = asString(row.summary ?? row.sound ?? row.text);
    return text ? { kind, text } : null;
  }
  const device = asString(row.device_id ?? row.device);
  return { kind, text: device ? device : "Home state" };
}

function planDecision(event: Record<string, unknown>): CanonicalEvent[] | null {
  const planId = asString(event.plan_id ?? event.planId);
  const decision = event.decision === "approved" || event.decision === "rejected" ? event.decision : null;
  const reason = asString(event.reason) ?? "";
  if (!planId || !decision) return null;
  return [{ type: "plan.decision", planId, decision, reason }];
}

function planCancel(event: Record<string, unknown>): CanonicalEvent[] | null {
  const planId = asString(event.plan_id ?? event.planId);
  if (!planId) return null;
  return [{ type: "plan.decision", planId, decision: "rejected", reason: asString(event.reason) ?? "cancelled" }];
}

function planResult(event: Record<string, unknown>): CanonicalEvent[] | null {
  const planId = asString(event.plan_id ?? event.planId);
  const resultsRaw = Array.isArray(event.action_results) ? event.action_results : null;
  if (!planId || !resultsRaw) return null;
  const results: ActionResultFact[] = [];
  for (const item of resultsRaw) {
    const row = asRecord(item);
    if (!row) return null;
    const actionId = asString(row.action_id ?? row.actionId);
    if (!actionId) return null;
    const raw = row.status;
    const rawStatus = raw === "ok" || raw === "succeeded" ? "ok" : raw === "error" || raw === "failed" ? "error" : raw === "skipped" || raw === "cancelled" ? "skipped" : "unknown";
    results.push({
      actionId,
      rawStatus,
      error: asString(row.error) ?? undefined,
      newState: row.new_state ?? row.newState,
      providerConfirmed: providerConfirmed(row),
      delivery: deliveryOf(row),
    });
  }
  return [{ type: "plan.result", planId, status: asString(event.status) ?? "unknown", results }];
}

function providerConfirmed(row: Record<string, unknown>): boolean {
  if (row.provider_confirmed === true || row.confirmed === true) return true;
  const verification = asString(row.verification);
  if (verification === "state_committed" || verification === "playback_confirmed" || verification === "provider_delivered") return true;
  const playback = asString(row.playback ?? row.provider_status);
  return playback === "playing" || playback === "confirmed";
}

function deliveryOf(row: Record<string, unknown>): ActionResultFact["delivery"] {
  if (row.delivered === true) return "delivered";
  if (row.failed === true || row.status === "error") return "failed";
  if (row.accepted === true) return "accepted";
  return undefined;
}

function toolCall(event: Record<string, unknown>): CanonicalEvent[] | null {
  const actionId = asString(event.call_id ?? event.callId ?? event.action_id);
  const tool = asString(event.tool);
  if (!actionId || !tool) return null;
  const args = asRecord(event.args) ?? {};
  if (!argsFinite(args)) return null;
  return [{
    type: "action",
    actionId,
    planId: asString(event.plan_id ?? event.planId) ?? undefined,
    turnId: asString(event.turn_id ?? event.turnId) ?? undefined,
    tool,
    args,
    status: "proposed",
  }];
}

function toolStatus(event: Record<string, unknown>): CanonicalEvent[] | null {
  const actionId = asString(event.call_id ?? event.callId ?? event.action_id);
  const status = asString(event.status);
  if (!actionId || !status) return null;
  const mapped = mapToolStatus(status);
  if (!mapped) return null;
  const detail = asString(event.detail) ?? undefined;
  const result = asRecord(event.result);
  let next = mapped;
  let patch: HomePatch | undefined;
  if (mapped === "verified") {
    const confirmed = result ? providerConfirmed(result) || result.ok === true : false;
    if (!confirmed) {
      next = "unknown";
    } else {
      patch = patchFromNewState(result?.new_state ?? result, 0) ?? undefined;
      if (!patch) next = "unknown";
    }
  }
  return [{
    type: "action",
    actionId,
    planId: asString(event.plan_id ?? event.planId) ?? undefined,
    status: next,
    detail: next === "unknown" && isMusicName(asString(event.tool)) ? detail ?? "Could not verify playback" : detail,
    patch,
  }];
}

function mapToolStatus(status: string): "proposed" | "approved" | "rejected" | "started" | "verified" | "failed" | "unknown" | null {
  if (status === "proposed") return "proposed";
  if (status === "approved") return "approved";
  if (status === "asking" || status === "repeated") return "proposed";
  if (status === "rejected" || status === "declined") return "rejected";
  if (status === "started" || status === "running") return "started";
  if (status === "failed" || status === "error") return "failed";
  if (status === "done") return "verified";
  return "unknown";
}

function actionEvent(type: string, event: Record<string, unknown>, now: number): CanonicalEvent[] | null {
  const actionId = asString(event.action_id ?? event.actionId ?? event.call_id);
  const tool = asString(event.tool) ?? undefined;
  if (!actionId) return null;
  const args = asRecord(event.args) ?? undefined;
  const base = {
    type: "action" as const,
    actionId,
    planId: asString(event.plan_id ?? event.planId) ?? undefined,
    turnId: asString(event.turn_id ?? event.turnId) ?? undefined,
    tool,
    args,
    detail: asString(event.detail ?? event.error) ?? undefined,
  };
  if (type === "action.proposed") return [{ ...base, status: "proposed" }];
  if (type === "action.approved") return [{ ...base, status: "approved" }];
  if (type === "action.started") return [{ ...base, status: "started" }];
  if (type === "action.result") {
    const status = asString(event.status);
    if (status === "succeeded") return actionEvent("action.completed", event, now);
    if (status === "failed" || status === "cancelled" || status === "skipped") return actionEvent("action.failed", event, now);
    return [{ ...base, status: "unknown" }];
  }
  if (type === "action.failed") return [{ ...base, status: "failed" }];
  if (type === "action.accepted") return [{ ...base, status: "accepted" }];
  if (type === "action.delivered") return [{ ...base, status: "delivered", patch: messagePatch(event, "delivered", now) }];
  if (type === "action.completed" || type === "action.verified") {
    const patch = patchFromNewState(event.new_state ?? event.newState ?? event.result, now) ?? undefined;
    const music = isMusicName(tool);
    const confirmed = providerConfirmed(event) || !music;
    if (music && !providerConfirmed(event) && !patch?.speaker?.confirmed) {
      return [{ ...base, status: "unknown", detail: base.detail ?? "Could not verify playback" }];
    }
    if (!patch && !confirmed) return [{ ...base, status: "unknown" }];
    return [{ ...base, status: "verified", patch }];
  }
  return [{ ...base, status: "unknown" }];
}

function messagePatch(event: Record<string, unknown>, delivery: "delivered" | "accepted", now: number): HomePatch {
  return {
    caregiver: {
      alertId: asString(event.action_id ?? event.alert_id) ?? "alert",
      message: asString(event.message ?? event.text) ?? "",
      severity: asString(event.severity) ?? "info",
      delivery,
      recipient: asString(event.recipient ?? event.to) ?? null,
      animateUntil: now + 4000,
    },
  };
}

function incident(event: Record<string, unknown>): CanonicalEvent[] | null {
  const id = asString(event.incident_id ?? event.incidentId ?? event.id);
  const summary = asString(event.summary ?? event.message ?? event.status);
  if (!id || !summary) return null;
  return [{ type: "incident", incident: { id, status: asString(event.status) ?? "open", summary } }];
}

function caregiverAlert(event: Record<string, unknown>, now: number): CanonicalEvent[] | null {
  const alert = asRecord(event.alert) ?? event;
  const alertId = asString(alert.alert_id ?? alert.alertId);
  const message = asString(alert.message);
  if (!alertId || !message) return null;
  const delivery = alert.acknowledged === true ? "acknowledged" : alert.delivered === true ? "delivered" : alert.failed === true ? "failed" : "accepted";
  return [{
    type: "home",
    patch: {
      caregiver: {
        alertId,
        message,
        severity: asString(alert.severity) ?? "info",
        delivery,
        recipient: asString(alert.recipient ?? event.caregiver_name) ?? null,
        animateUntil: now + 4000,
      },
    },
  }];
}

export function parseHomePayload(raw: unknown, now: number): HomePatch | null {
  const record = asRecord(raw);
  if (!record) return null;
  if (Array.isArray(record.lights) || Array.isArray(record.blinds) || Array.isArray(record.media)) {
    return parseV2Home(record, now);
  }
  const rooms = asRecord(record.rooms);
  if (rooms) return parseRooms(rooms, record, now);
  if (record.lamp || record.ceiling || record.lights || record.blinds || record.speaker || record.flash || record.caption) {
    return parseLegacy(record, now);
  }
  const nested = asRecord(record.state);
  if (nested) return parseHomePayload(nested, now);
  return null;
}

function parseV2Home(record: Record<string, unknown>, now: number): HomePatch | null {
  const patch: HomePatch = { replace: true, lights: [], blinds: [] };
  const revision = finiteNumber(record.revision);
  if (revision != null) patch.revision = revision;
  if (Array.isArray(record.lights)) {
    for (const item of record.lights) {
      const row = asRecord(item);
      const id = asString(row?.id);
      if (!row || !id) continue;
      const reading = readLight(id, row);
      if (reading === "invalid") return null;
      if (reading) patch.lights!.push(reading);
    }
  }
  if (Array.isArray(record.blinds)) {
    for (const item of record.blinds) {
      const row = asRecord(item);
      const id = asString(row?.id);
      if (!row || !id) continue;
      const reading = readBlind(id, row);
      if (reading === "invalid") return null;
      if (reading) patch.blinds!.push(reading);
    }
  }
  const media = Array.isArray(record.media) ? record.media.map(asRecord).find((row) => row != null) : null;
  if (media) {
    const speaker = readSpeaker(
      { sound: media.sound_id ?? media.title ?? null, volume: media.volume, playing: media.playback === "playing" },
      true,
    );
    if (speaker === "invalid") return null;
    if (speaker) patch.speaker = speaker;
  }
  const flashes = Array.isArray(record.flashes) ? record.flashes : [];
  const flash = asRecord(flashes[flashes.length - 1]);
  if (flash) {
    const parsed = readFlash({ ...flash, until: flash.ends_at }, now);
    if (parsed === "invalid") return null;
    patch.flash = parsed;
  }
  const captions = Array.isArray(record.captions) ? record.captions : [];
  const caption = [...captions].reverse().map(asRecord).find((row) => row != null && row.resolved !== true);
  if (caption) {
    const parsed = readCaption(caption, now);
    if (parsed === "invalid") return null;
    patch.caption = parsed ?? null;
  }
  return patch;
}

function parseRooms(rooms: Record<string, unknown>, root: Record<string, unknown>, now: number): HomePatch | null {
  const patch: HomePatch = { replace: true, lights: [], blinds: [] };
  const revision = finiteNumber(root.revision);
  if (revision != null) patch.revision = revision;
  const caregiver = asRecord(root.caregiver);
  if (caregiver) {
    const name = asString(caregiver.name);
    if (name) patch.caregiverName = name;
  }
  for (const room of Object.values(rooms)) {
    const body = asRecord(room);
    if (!body) continue;
    const lights = asRecord(body.lights);
    if (lights) {
      for (const [id, value] of Object.entries(lights)) {
        const reading = readLight(id, value);
        if (reading === "invalid") return null;
        if (reading) patch.lights!.push(reading);
      }
    }
    const blinds = asRecord(body.blinds);
    if (blinds) {
      for (const [id, value] of Object.entries(blinds)) {
        const reading = readBlind(id, value);
        if (reading === "invalid") return null;
        if (reading) patch.blinds!.push(reading);
      }
    } else if (typeof body.blinds === "number") {
      const position = asLevel(body.blinds);
      if (position == null) return null;
      patch.blinds!.push({ id: BLINDS_ID, position });
    }
    if (body.speaker) {
      const speaker = readSpeaker(body.speaker, true);
      if (speaker === "invalid") return null;
      if (speaker) patch.speaker = speaker;
    }
    if ("flash" in body) {
      const flash = readFlash(body.flash, now);
      if (flash === "invalid") return null;
      patch.flash = flash ?? null;
    }
    if ("caption" in body) {
      const caption = readCaption(body.caption, now);
      if (caption === "invalid") return null;
      patch.caption = caption ?? null;
    }
  }
  const alerts = Array.isArray(root.alerts) ? root.alerts : [];
  const last = asRecord(alerts[alerts.length - 1]);
  if (last) {
    const alertId = asString(last.alert_id);
    const message = asString(last.message);
    if (alertId && message) {
      patch.caregiver = {
        alertId,
        message,
        severity: asString(last.severity) ?? "info",
        delivery: last.acknowledged === true ? "acknowledged" : last.delivered === true ? "delivered" : "accepted",
        recipient: asString(last.recipient) ?? patch.caregiverName ?? null,
        animateUntil: now + 4000,
      };
    }
  }
  return patch;
}

function parseLegacy(record: Record<string, unknown>, now: number): HomePatch | null {
  const patch: HomePatch = { lights: [], blinds: [] };
  if ("lamp" in record) {
    const reading = readLight(LAMP_ID, record.lamp);
    if (reading === "invalid") return null;
    if (reading) patch.lights!.push(reading);
  }
  if ("ceiling" in record) {
    const reading = readLight(CEILING_ID, record.ceiling);
    if (reading === "invalid") return null;
    if (reading) patch.lights!.push(reading);
  }
  const lights = asRecord(record.lights);
  if (lights) {
    for (const [id, value] of Object.entries(lights)) {
      const reading = readLight(id, value);
      if (reading === "invalid") return null;
      if (reading) patch.lights!.push(reading);
    }
  }
  if ("blinds" in record) {
    if (typeof record.blinds === "number") {
      const position = asLevel(record.blinds);
      if (position == null) return null;
      patch.blinds!.push({ id: BLINDS_ID, position });
    } else {
      const blinds = asRecord(record.blinds);
      if (blinds) {
        for (const [id, value] of Object.entries(blinds)) {
          const reading = readBlind(id, value);
          if (reading === "invalid") return null;
          if (reading) patch.blinds!.push(reading);
        }
      }
    }
  }
  if ("speaker" in record) {
    const speaker = readSpeaker(record.speaker, false);
    if (speaker === "invalid") return null;
    if (speaker) patch.speaker = speaker;
  }
  if ("flash" in record) {
    const flash = readFlash(record.flash, now);
    if (flash === "invalid") return null;
    patch.flash = flash ?? null;
  }
  if ("caption" in record) {
    const caption = readCaption(record.caption, now);
    if (caption === "invalid") return null;
    patch.caption = caption ?? null;
  }
  return patch;
}

function readLight(id: string, value: unknown): LightReading | null | "invalid" {
  const row = asRecord(value);
  if (!row) return null;
  const hasLevel = "luminosity" in row || "level" in row;
  const hasWarmth = "warmth" in row;
  if (hasLevel && asLevel(row.luminosity ?? row.level) == null && (row.luminosity ?? row.level) != null) return "invalid";
  if (hasWarmth && row.warmth != null && asWarmth(row.warmth) == null) return "invalid";
  return {
    id,
    luminosity: hasLevel ? asLevel(row.luminosity ?? row.level) : null,
    warmth: hasWarmth ? asWarmth(row.warmth) : null,
  };
}

function readBlind(id: string, value: unknown): { id: string; position: number | null } | null | "invalid" {
  if (typeof value === "number") {
    const position = asLevel(value);
    return position == null ? "invalid" : { id, position };
  }
  const row = asRecord(value);
  if (!row || !("position" in row)) return null;
  const position = asLevel(row.position);
  if (row.position != null && position == null) return "invalid";
  return { id, position };
}

function readSpeaker(value: unknown, deviceSnapshot: boolean): HomePatch["speaker"] | null | "invalid" {
  const row = asRecord(value);
  if (!row) return null;
  if ("volume" in row && row.volume != null && asLevel(row.volume) == null) return "invalid";
  const sound = row.sound === null ? null : asString(row.sound);
  const volume = "volume" in row ? asLevel(row.volume) : null;
  if (typeof row.playing === "boolean") {
    return {
      sound,
      volume,
      playback: row.playing ? "playing" : "stopped",
      confirmed: true,
    };
  }
  if (deviceSnapshot) {
    const playing = typeof sound === "string" && sound.length > 0;
    return { sound, volume, playback: playing ? "playing" : "stopped", confirmed: true };
  }
  return { sound, volume, playback: "unknown", confirmed: false };
}

function readFlash(value: unknown, now: number): HomePatch["flash"] | "invalid" {
  if (value == null) return null;
  const row = asRecord(value);
  if (!row) return "invalid";
  if (row.active === false) return null;
  const until = parseTime(row.until ?? row.ended_at ?? row.endedAt);
  const started = parseTime(row.started_at ?? row.startedAt);
  const duration = finiteNumber(row.duration_s ?? row.durationS);
  if (row.duration_s != null && duration == null) return "invalid";
  let endedAt = until;
  let startedAt = started;
  if (endedAt == null && duration != null) {
    startedAt = startedAt ?? now;
    endedAt = startedAt + duration * 1000;
  }
  if (endedAt == null) return "invalid";
  if (startedAt == null && duration != null) startedAt = endedAt - duration * 1000;
  if (startedAt == null) startedAt = endedAt;
  const color = cssColor(row.color);
  const pulses = pulsesFor(row.pattern, row.pulses ?? row.pulse_count);
  const id = asString(row.id ?? row.overlay_id) ?? `${color}:${startedAt}:${endedAt}:${pulses}`;
  return { id, color, startedAt, endedAt, pulses };
}

function readCaption(value: unknown, now: number): HomePatch["caption"] | "invalid" {
  if (value == null) return null;
  if (typeof value === "string") return { text: value, until: null };
  const row = asRecord(value);
  if (!row) return "invalid";
  const text = asString(row.text);
  if (!text) return null;
  const until = parseTime(row.until) ?? (finiteNumber(row.ttl_s) != null ? now + (finiteNumber(row.ttl_s) as number) * 1000 : null);
  return { text, until };
}

export function patchFromNewState(value: unknown, now: number): HomePatch | null {
  const row = asRecord(value);
  if (!row) return null;
  if (row.rooms || row.lamp || row.ceiling || row.lights || row.blinds || row.speaker || row.flash) {
    return parseHomePayload(row, now);
  }
  return null;
}

function isMusicName(tool: string | null | undefined): boolean {
  if (!tool) return false;
  return tool === "play_sound" || tool === "stop_sound" || tool.includes("spotify") || tool.includes("music");
}
