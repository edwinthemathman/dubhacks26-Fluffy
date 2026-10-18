import { describe, expect, it } from "vitest";
import { normalizeEvent } from "@/lib/fluffy/normalizeEvent";
import { asLevel } from "@/lib/fluffy/levels";

const NOW = 1_700_000_000_000;

describe("asLevel", () => {
  it("keeps 1 as 1 percent and rejects nonfinite values", () => {
    expect(asLevel(1)).toBe(1);
    expect(asLevel(0.5)).toBe(0.5);
    expect(asLevel(100)).toBe(100);
    expect(asLevel(0)).toBe(0);
    expect(asLevel(101)).toBeNull();
    expect(asLevel(Number.NaN)).toBeNull();
    expect(asLevel(Number.POSITIVE_INFINITY)).toBeNull();
  });
});

describe("normalizeEvent", () => {
  it("reads a version 2 event envelope and a flat home snapshot", () => {
    const events = normalizeEvent(
      {
        event_id: "evt_1",
        session_id: "ses_server",
        sequence: 4,
        timestamp: "2026-10-18T01:00:00Z",
        type: "home.snapshot",
        payload: {
          revision: 3,
          lights: [
            { id: "lamp.living_floor", luminosity: 30, warmth: "warm" },
            { id: "light.living_ceiling", luminosity: 30, warmth: "warm" },
          ],
          blinds: [{ id: "blinds.living_window", position: 100 }],
          media: [{ room: "living_room", sound_id: null, volume: 35, playback: "idle" }],
        },
      },
      { now: NOW },
    );
    expect(events[0]).toMatchObject({
      type: "snapshot",
      seq: 4,
      sessionId: "ses_server",
      patch: {
        revision: 3,
        lights: [
          { id: "lamp.living_floor", luminosity: 30, warmth: "warm" },
          { id: "light.living_ceiling", luminosity: 30, warmth: "warm" },
        ],
      },
    });
    const started = normalizeEvent(
      {
        event_id: "evt_2",
        sequence: 5,
        type: "action.result",
        payload: { result: { action_id: "act_demo", plan_id: "pln_demo", tool: "dim_lights", status: "succeeded", verification: "state_committed" } },
      },
      { now: NOW },
    );
    expect(started[0]).toMatchObject({ type: "action", actionId: "act_demo", status: "verified" });
  });
  it("maps legacy live events and keeps lamp and ceiling apart", () => {
    const events = normalizeEvent(
      { type: "state", lamp: { luminosity: 1, warmth: "warm" }, ceiling: { luminosity: 40 }, blinds: 0 },
      { now: NOW },
    );
    expect(events[0]).toMatchObject({
      type: "home",
      patch: {
        lights: [
          { id: "lamp.living_floor", luminosity: 1, warmth: "warm" },
          { id: "light.living_ceiling", luminosity: 40 },
        ],
        blinds: [{ id: "blinds.living_window", position: 0 }],
      },
    });
  });

  it("drops nonfinite levels and raw reasoning traces", () => {
    expect(normalizeEvent({ type: "state", lamp: { luminosity: Number.NaN } }, { now: NOW })).toEqual([]);
    expect(normalizeEvent({ type: "think", note: "chain of thought: secret" }, { now: NOW })).toEqual([]);
    expect(normalizeEvent({ type: "say", text: "hello" }, { now: NOW })).toEqual([]);
  });

  it("does not treat tool.status done as verified playback", () => {
    const [event] = normalizeEvent({ type: "tool.status", call_id: "c1", status: "done", tool: "spotify_play" }, { now: NOW });
    expect(event).toMatchObject({ type: "action", status: "unknown", detail: "Could not verify playback" });
  });

  it("maps a submitted call to started and a done call without a result to verified", () => {
    const [started] = normalizeEvent({ type: "tool.status", call_id: "c1", status: "submitted", tool: "dim_lights" }, { now: NOW });
    expect(started).toMatchObject({ status: "started", tool: "dim_lights" });
    const [done] = normalizeEvent({ type: "tool.status", call_id: "c1", status: "done", tool: "dim_lights" }, { now: NOW });
    expect(done).toMatchObject({ status: "verified" });
    expect(done).not.toHaveProperty("patch");
    const [music] = normalizeEvent({ type: "tool.status", call_id: "c2", status: "done", tool: "play_music", result: { ok: true } }, { now: NOW });
    expect(music).toMatchObject({ status: "verified" });
  });

  it("reads a phone, a door, and a live flash from the loop home", () => {
    const [event] = normalizeEvent(
      {
        type: "state",
        state: {
          lamp: { luminosity: 40, warmth: "warm" },
          phone: { mode: "text", to: "Alex", preview: "Sam needs help", delivered: true },
          door: { front_door: "open", bedroom_door: "closed" },
          flash: { active: true, color: "red", pattern: "pulse2" },
        },
      },
      { now: NOW },
    );
    expect(event).toMatchObject({
      type: "snapshot",
      patch: {
        caregiver: { recipient: "Alex", message: "Sam needs help", delivery: "delivered", animateUntil: NOW + 4000 },
        doors: { front_door: "open", bedroom_door: "closed" },
        flash: { startedAt: NOW, endedAt: NOW + 3000, pulses: 2 },
      },
    });
  });

  it("reads a version 2 plan, speech, and incident", () => {
    const plan = normalizeEvent(
      {
        type: "plan.proposed",
        schema_version: "1.0",
        plan: {
          plan_id: "pln_1",
          created_at: "2026-10-18T00:00:00Z",
          expires_at: "2026-10-18T00:00:30Z",
          trigger: { kind: "spoken", room: "living_room" },
          action_class: "comfort",
          intent: "Dim the floor lamp",
          actions: [{ action_id: "act_1", tool: "dim_lights", args: { room: "living_room", luminosity: 35, light: "lamp.living_floor" } }],
          confidence: 0.8,
          consent: { required: true, question: "Dim the floor lamp?" },
          reasoning_summary: "The lamp is bright.",
          evidence: [{ kind: "speech", text: "it's bright" }],
        },
      },
      { now: NOW },
    );
    expect(plan[0]).toMatchObject({ type: "plan", plan: { planId: "pln_1", consentRequired: true } });
    expect(normalizeEvent({ type: "speech.status", message_id: "m1", status: "generated" }, { now: NOW })[0]).toMatchObject({
      type: "speech",
      status: "generated",
    });
    expect(normalizeEvent({ type: "incident.updated", incident_id: "inc_1", status: "open", summary: "Check in" }, { now: NOW })[0]).toMatchObject({
      type: "incident",
    });
  });

  it("keeps two flash pulses on an absolute window", () => {
    const [event] = normalizeEvent(
      {
        type: "home.state",
        revision: 3,
        state: {
          rooms: {
            living_room: {
              lights: {},
              blinds: {},
              speaker: { sound: null, volume: 0 },
              flash: { id: "flash-blue", color: "blue", pattern: "pulse2", started_at: NOW, until: NOW + 2000, pulses: 2 },
            },
          },
        },
      },
      { now: NOW },
    );
    expect(event).toMatchObject({
      type: "snapshot",
      patch: { flash: { id: "flash-blue", pulses: 2, startedAt: NOW, endedAt: NOW + 2000 } },
    });
  });
});
