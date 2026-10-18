import { describe, expect, it } from "vitest";
import { normalizeEvent } from "@/lib/fluffy/normalizeEvent";
import { thinkingLabel } from "@/lib/fluffy/phrases";
import { activityTurn, createSession, dedupeTurns, reduceSession } from "@/lib/fluffy/reducer";
import type { CanonicalEvent, SessionState } from "@/lib/fluffy/types";

const NOW = 1_700_000_000_000;
const sensing = {
  started: true,
  paused: false,
  preview: true,
  cameraOn: true,
  transport: "reaching" as const,
  inferenceAvailable: false,
};

function apply(events: CanonicalEvent[], historical = false): SessionState {
  return events.reduce((state, event) => reduceSession(state, event, { now: NOW, historical }), createSession("ses_1"));
}

function fromRaw(raw: unknown, historical = false): SessionState {
  return apply(normalizeEvent(raw, { now: NOW }), historical);
}

function feed(state: SessionState, raw: unknown): SessionState {
  return normalizeEvent(raw, { now: NOW }).reduce((current, event) => reduceSession(current, event, { now: NOW }), state);
}

describe("session reducer", () => {
  it("does not complete a proposed or approved action", () => {
    const state = fromRaw({
      type: "plan.proposed",
      plan: {
        plan_id: "pln_1",
        intent: "Dim the lamp",
        actions: [{ action_id: "act_1", tool: "dim_lights", args: { luminosity: 35, light: "lamp.living_floor" } }],
        confidence: 0.5,
        trigger: { kind: "spoken", room: "living_room" },
        consent: { required: true },
      },
    });
    expect(state.actions.act_1.status).toBe("proposed");
    expect(state.home.lights["lamp.living_floor"]).toBeUndefined();
    expect(thinkingLabel(state, sensing)).toBe("Proposed action");
    const approved = reduceSession(state, { type: "plan.decision", planId: "pln_1", decision: "approved", reason: "yes" }, { now: NOW });
    expect(approved.actions.act_1.status).toBe("approved");
    expect(approved.home.known).toBe(false);
  });

  it("applies a verified light result on the 0-100 scale", () => {
    let state = fromRaw({
      type: "plan.proposed",
      plan: {
        plan_id: "pln_1",
        intent: "Dim the lamp",
        actions: [{ action_id: "act_1", tool: "dim_lights", args: { luminosity: 1, light: "lamp.living_floor" } }],
        confidence: 0.5,
        trigger: { kind: "spoken" },
        consent: { required: false },
      },
    });
    state = reduceSession(
      state,
      {
        type: "plan.result",
        planId: "pln_1",
        status: "succeeded",
        results: [{ actionId: "act_1", rawStatus: "ok", newState: { lights: { "lamp.living_floor": { luminosity: 1, warmth: "warm" } } } }],
      },
      { now: NOW },
    );
    expect(state.actions.act_1.status).toBe("verified");
    expect(state.home.lights["lamp.living_floor"].luminosity).toBe(1);
    expect(state.actions.act_1.summary).toBe("Lights set to 1%");
  });

  it("does not let a ceiling update overwrite the floor lamp", () => {
    const state = fromRaw({
      type: "state",
      lamp: { luminosity: 35, warmth: "warm" },
      ceiling: { luminosity: 1 },
    });
    expect(state.home.lights["lamp.living_floor"].luminosity).toBe(35);
    expect(state.home.lights["light.living_ceiling"].luminosity).toBe(1);
  });

  it("keeps spotify done without provider evidence unverified", () => {
    let state = apply([{ type: "action", actionId: "m1", tool: "spotify_play", args: { track: "Rain" }, status: "proposed" }]);
    state = feed(state, { type: "tool.status", call_id: "m1", status: "done", tool: "spotify_play" });
    expect(state.actions.m1.status).toBe("unknown");
    expect(state.home.speaker.playback).not.toBe("playing");
    expect(state.actions.m1.summary).toBe("Could not verify playback");
  });

  it("separates message acceptance from delivery", () => {
    const accepted = apply([{ type: "action", actionId: "a1", tool: "alert_caregiver", status: "accepted", detail: "Message submitted" }]);
    expect(accepted.actions.a1.summary).toBe("Message submitted");
    const delivered = apply([{ type: "action", actionId: "a1", tool: "alert_caregiver", status: "delivered" }]);
    expect(delivered.actions.a1.summary).toBe("Message delivered");
  });

  it("does not queue historical speech and ignores duplicates", () => {
    const say = { type: "say", messageId: "say_1", text: "Hello", permitAudio: true, audioUrl: "/api/voice/say_1" } as const;
    const historical = apply([say], true);
    expect(historical.speechQueue).toEqual([]);
    expect(historical.playedIds).toContain("say_1");
    const live = apply([say, say]);
    expect(live.speechQueue).toHaveLength(1);
  });

  it("replaces state from a snapshot and ignores an older revision", () => {
    let state = feed(createSession("ses_1"), {
      type: "home.state",
      revision: 4,
      state: { rooms: { living_room: { lights: { "lamp.living_floor": { luminosity: 20 } }, blinds: { "blinds.living_window": { position: 100 } }, speaker: { sound: null, volume: 0 } } } },
    });
    state = feed(state, {
      type: "home.state",
      revision: 2,
      state: { rooms: { living_room: { lights: { "lamp.living_floor": { luminosity: 90 } }, blinds: {}, speaker: { sound: null, volume: 0 } } } },
    });
    expect(state.home.revision).toBe(4);
    expect(state.home.lights["lamp.living_floor"].luminosity).toBe(20);
    expect(state.home.blinds["blinds.living_window"].position).toBe(100);
  });

  it("accepts a restarted sequence as a new epoch", () => {
    let state = apply([{ type: "say", messageId: "say_old", text: "Old", permitAudio: false, seq: 5, sessionId: "s" }]);
    state = reduceSession(state, { type: "say", messageId: "say_new", text: "New", permitAudio: false, seq: 1, sessionId: "s" }, { now: NOW });
    expect(state.epoch).toBe(2);
    expect(state.captions.map((item) => item.text)).toContain("New");
  });

  it("shows a question without calling it done", () => {
    const state = apply([{ type: "ask", questionId: "q1", question: "Dim the floor lamp?", choices: ["Yes", "No"] }]);
    expect(thinkingLabel(state, sensing)).toBe("Dim the floor lamp?");
    expect(state.home.known).toBe(false);
  });

  it("labels camera preview separately from perception", () => {
    expect(thinkingLabel(createSession(), sensing)).toBe("Camera preview only");
    expect(
      thinkingLabel(createSession(), { ...sensing, inferenceAvailable: true }),
    ).toBe("Watching and listening");
  });

  it("keeps one turn when the server echoes the request id as the turn id", () => {
    const submitted = apply([{ type: "submit", requestId: "req_4eac7daf-dbc6-4938-aaa4-50edd7bcc004", text: "dim the lamp" }]);
    const started = feed(submitted, {
      type: "turn.start",
      turn_id: "req_4eac7daf-dbc6-4938-aaa4-50edd7bcc004",
      kind: "text",
      trigger: "dim the lamp",
    });
    expect(started.turns).toHaveLength(1);
    expect(new Set(started.turns.map((turn) => turn.id)).size).toBe(1);
  });

  it("collapses a turn that was already stored twice", () => {
    const once = apply([{ type: "submit", requestId: "req_4eac7daf-dbc6-4938-aaa4-50edd7bcc004", text: "dim the lamp" }]);
    const withResult = {
      ...once.turns[0],
      planId: "pln_1",
      actionIds: ["act_1"],
      observations: [{ text: "Perceptron proposed a dim.", at: NOW }],
    };
    const duplicated = { ...once, turns: [withResult, { ...once.turns[0], open: false }] };
    const collapsed = dedupeTurns(duplicated).turns;
    expect(collapsed).toHaveLength(1);
    expect(collapsed[0].planId).toBe("pln_1");
    expect(collapsed[0].actionIds).toEqual(["act_1"]);
    expect(collapsed[0].observations.map((item) => item.text)).toEqual(["Perceptron proposed a dim."]);
    expect(collapsed[0].text).toBe("dim the lamp");
  });

  it("keeps the turn that called a tool after a later empty turn", () => {
    let state = apply([
      { type: "turn.start", turnId: "turn_act", source: "vision" },
      { type: "action", actionId: "c1", turnId: "turn_act", tool: "dim_lights", status: "proposed" },
      { type: "turn.start", turnId: "turn_idle", source: "vision" },
    ]);
    expect(activityTurn(state)?.id).toBe("turn_act");
    state = feed(state, { type: "think", note: "Saw the lamp" });
    expect(state.turns.map((turn) => turn.id)).toEqual(["turn_act", "turn_idle"]);
    expect(state.lastSeen).toBe("Saw the lamp");
    const quiet = feed(createSession("ses_1"), { type: "think", note: "Checking the room" });
    expect(quiet.turns).toEqual([]);
    expect(quiet.lastSeen).toBe("Checking the room");
  });

  it("shows the inference stage without treating a frame ack as analysis", () => {
    const reading = fromRaw({ type: "inference.status", stage: "scene", status: "started" });
    expect(reading.stage).toBe("Reading the camera");
    expect(thinkingLabel(reading, sensing)).toBe("Reading the camera");
    const analyzed = fromRaw({ type: "inference.status", stage: "scene", status: "completed", thinking: "The person is upright." });
    expect(analyzed.stage).toBe("Camera frame analyzed");
    expect(analyzed.thinking).toEqual([{ label: "Saw", text: "The person is upright." }]);
    const empty = fromRaw({ type: "inference.status", stage: "decide", status: "completed", thinking: "" });
    expect(empty.thinking).toEqual([{ label: "Decision", text: "not returned" }]);
    const execution = feed(analyzed, { type: "inference.status", stage: "execution", status: "completed", thinking: "asked to dim" });
    expect(execution.thinking.map((item) => item.label)).toEqual(["Saw", "Execution"]);
    expect(execution.thinking[1].text).toBe("asked to dim");
    const failed = feed(reading, { type: "inference.error", turn_id: "req_1", message: "The camera read failed." });
    expect(failed.inference).toBe("idle");
    expect(failed.notice).toBe("The camera read failed.");
  });

  it("keeps distinct requests as separate turns", () => {
    const state = apply([
      { type: "submit", requestId: "req_one", text: "dim the lamp" },
      { type: "submit", requestId: "req_two", text: "open the blinds" },
    ]);
    expect(state.turns.map((turn) => turn.requestId)).toEqual(["req_one", "req_two"]);
    expect(new Set(state.turns.map((turn) => turn.id)).size).toBe(2);
  });
});
