import { describe, expect, it } from "vitest";
import { actionsForRoom, projectHomeToRoom } from "@/features/room/projectHomeToRoom";
import { actionTargets } from "@/features/room/toolMap";
import { unknownRoom } from "@/features/room/state";
import { normalizeEvent } from "@/lib/fluffy/normalizeEvent";
import { createSession, dedupeTurns, emptyHome, reduceSession } from "@/lib/fluffy/reducer";
import type { ActionModel, HomeModel, SessionState } from "@/lib/fluffy/types";

const NOW = 1_700_000_000_000;

function action(partial: Partial<ActionModel> & Pick<ActionModel, "id" | "tool" | "status">): ActionModel {
  return {
    args: {},
    updatedAt: NOW,
    ...partial,
  };
}

describe("projectHomeToRoom", () => {
  it("does not invent a brightness before the first snapshot", () => {
    const room = projectHomeToRoom({ home: emptyHome(), actions: [], question: null, now: NOW });
    expect(room).toEqual(unknownRoom());
    expect(room.lamp.level).toBeNull();
    expect(room.known).toBe(false);
  });

  it("keeps the two lights and blinds distinct", () => {
    const home: HomeModel = {
      ...emptyHome(),
      known: true,
      lights: {
        "lamp.living_floor": { id: "lamp.living_floor", luminosity: 1, warmth: "warm" },
        "light.living_ceiling": { id: "light.living_ceiling", luminosity: 80, warmth: "cool" },
      },
      blinds: { "blinds.living_window": { id: "blinds.living_window", position: 0 } },
    };
    const room = projectHomeToRoom({ home, actions: [], question: null, now: NOW });
    expect(room.lamp.level).toBe(1);
    expect(room.ceiling.level).toBe(80);
    expect(room.blinds.position).toBe(0);
  });

  it("shows a flash only inside its absolute window and keeps two pulses", () => {
    const home: HomeModel = {
      ...emptyHome(),
      known: true,
      flash: { id: "blue-2", color: "#3b82f6", startedAt: NOW, endedAt: NOW + 2000, pulses: 2 },
    };
    const live = projectHomeToRoom({ home, actions: [], question: null, now: NOW + 100 });
    expect(live.flash).toMatchObject({ id: "blue-2", pulses: 2 });
    const expired = projectHomeToRoom({ home, actions: [], question: null, now: NOW + 2000 });
    expect(expired.flash).toBeNull();
  });

  it("plays audio only when playback is confirmed, including a muted track", () => {
    const pending = projectHomeToRoom({
      home: { ...emptyHome(), known: true, speaker: { sound: "rain", volume: 40, playback: "pending", confirmed: false } },
      actions: [],
      question: null,
      now: NOW,
    });
    expect(pending.speaker.playing).toBe(false);
    const muted = projectHomeToRoom({
      home: { ...emptyHome(), known: true, speaker: { sound: "rain", volume: 0, playback: "playing", confirmed: true } },
      actions: [],
      question: null,
      now: NOW,
    });
    expect(muted.speaker.playing).toBe(true);
    expect(muted.speaker.volume).toBe(0);
  });

  it("keeps alert delivery after the phone animation ends", () => {
    const home: HomeModel = {
      ...emptyHome(),
      known: true,
      caregiver: {
        alertId: "al_1",
        message: "Need a check",
        severity: "info",
        delivery: "accepted",
        recipient: null,
        animateUntil: NOW + 1000,
      },
    };
    const during = projectHomeToRoom({ home, actions: [], question: null, now: NOW });
    const after = projectHomeToRoom({ home, actions: [], question: null, now: NOW + 1000 });
    expect(during.phone.mode).toBe("texting");
    expect(during.phone.to).toBeUndefined();
    expect(after.phone.mode).toBe("idle");
    expect(after.phone.delivery).toBe("accepted");
  });

  it("opens the front door and shows the phone texting", () => {
    const room = projectHomeToRoom({
      home: {
        ...emptyHome(),
        known: true,
        doors: { front_door: { id: "front_door", open: true } },
        caregiver: {
          alertId: "Alex",
          message: "Sam needs help",
          severity: "info",
          delivery: "delivered",
          recipient: "Alex",
          animateUntil: NOW + 4000,
        },
      },
      actions: [],
      question: null,
      now: NOW,
    });
    expect(room.door.open).toBe(true);
    expect(room.phone.mode).toBe("texting");
    expect(room.phone.to).toBe("Alex");
  });

  it("marks a proposed lamp change without moving the ceiling", () => {
    const room = projectHomeToRoom({
      home: emptyHome(),
      actions: [action({ id: "a", tool: "dim_lights", status: "proposed", args: { light: "lamp.living_floor", luminosity: 10 } })],
      question: null,
      now: NOW,
    });
    expect(room.pending.lamp).toBe("pending");
    expect(room.pending.ceiling).toBeUndefined();
    expect(room.lamp.level).toBeNull();
  });

  it("dims the floor lamp only after confirmation and leaves the ceiling alone", () => {
    let state = sessionFrom({
      type: "plan.proposed",
      plan: {
        plan_id: "pln_lamp",
        intent: "Dim the floor lamp",
        actions: [{ action_id: "act_lamp", tool: "dim_lights", args: { room: "living_room", light: "lamp.living_floor", luminosity: 30 } }],
        confidence: 0.9,
        trigger: { kind: "spoken", room: "living_room" },
        consent: { required: false },
      },
    });
    state = reduceSession(state, { type: "action", actionId: "act_lamp", status: "started" }, { now: NOW });
    const pending = projectHomeToRoom({ home: state.home, actions: Object.values(state.actions), question: null, now: NOW });
    expect(pending.lamp.level).toBeNull();
    expect(pending.pending.lamp).toBe("pending");
    expect(pending.pending.ceiling).toBeUndefined();

    state = reduceSession(
      state,
      {
        type: "plan.result",
        planId: "pln_lamp",
        status: "succeeded",
        results: [{ actionId: "act_lamp", rawStatus: "ok", newState: { luminosity: { "lamp.living_floor": 30 } }, providerConfirmed: true }],
      },
      { now: NOW },
    );
    const confirmed = projectHomeToRoom({ home: state.home, actions: Object.values(state.actions), question: null, now: NOW });
    expect(state.home.lights["lamp.living_floor"].luminosity).toBe(30);
    expect(state.home.lights["light.living_ceiling"]).toBeUndefined();
    expect(confirmed.lamp.level).toBe(30);
    expect(confirmed.ceiling.level).toBeNull();
    expect(confirmed.pending.lamp).toBeUndefined();
  });

  it("opens only the front door after verification and leaves a failed attempt unchanged", () => {
    let state = sessionFrom({
      type: "plan.proposed",
      plan: {
        plan_id: "pln_door",
        intent: "Open the door",
        actions: [{ action_id: "act_door", tool: "open_door", args: { door: "front_door" } }],
        confidence: 0.9,
        trigger: { kind: "spoken", room: "living_room" },
        consent: { required: false },
      },
    });
    state = reduceSession(state, { type: "action", actionId: "act_door", status: "started" }, { now: NOW });
    const pending = projectHomeToRoom({ home: state.home, actions: Object.values(state.actions), question: null, now: NOW });
    expect(pending.door.open).toBeNull();
    expect(pending.pending.door).toBe("pending");
    expect(pending.pending.lamp).toBeUndefined();

    state = reduceSession(
      state,
      {
        type: "plan.result",
        planId: "pln_door",
        status: "succeeded",
        results: [{ actionId: "act_door", rawStatus: "ok", newState: { door: "front_door", state: "open" }, providerConfirmed: true }],
      },
      { now: NOW },
    );
    const open = projectHomeToRoom({ home: state.home, actions: Object.values(state.actions), question: null, now: NOW });
    expect(state.home.doors.front_door.open).toBe(true);
    expect(open.door.open).toBe(true);
    expect(open.lamp.level).toBeNull();

    const failed = projectHomeToRoom({
      home: state.home,
      actions: [action({ id: "act_fail", tool: "open_door", status: "failed", args: { door: "front_door" } })],
      question: null,
      now: NOW,
    });
    expect(failed.door.open).toBe(true);
    expect(failed.pending.door).toBe("failed");

    const unknown = projectHomeToRoom({
      home: { ...state.home, doors: { front_door: { id: "front_door", open: false } } },
      actions: [action({ id: "act_unknown", tool: "close_door", status: "unknown", args: { door: "front_door" } })],
      question: null,
      now: NOW,
    });
    expect(unknown.door.open).toBe(false);
    expect(unknown.pending.door).toBe("pending");
  });

  it("changes no room object for an unknown tool or an unknown light id", () => {
    expect(actionTargets("dim_lights", { luminosity: 10 })).toEqual(["lamp", "ceiling"]);
    expect(actionTargets("dim_lights", { light: "lamp.kitchen", luminosity: 10 })).toEqual([]);
    expect(actionTargets("open_door", { door: "front_door" })).toEqual(["door"]);
    expect(actionTargets("describe_scene", { text: "someone waved" })).toEqual([]);
    expect(actionTargets("show_caption", { text: "hello" })).toEqual([]);
    expect(actionTargets("send_text", { to: "Alex", message: "home" })).toEqual([]);
    const room = projectHomeToRoom({
      home: { ...emptyHome(), known: true, lights: { "lamp.living_floor": { id: "lamp.living_floor", luminosity: 80, warmth: "neutral" } }, doors: { front_door: { id: "front_door", open: false } } },
      actions: [action({ id: "cue", tool: "describe_scene", status: "started", args: { text: "dim it" } })],
      question: null,
      now: NOW,
    });
    expect(room.lamp.level).toBe(80);
    expect(room.door.open).toBe(false);
    expect(room.pending).toEqual({});
  });

  it("maps started actions to their object and confirmed playback to the speaker", () => {
    expect(actionTargets("set_blinds", { room: "living_room", position: 20 })).toEqual(["blinds"]);
    expect(actionTargets("set_light_warmth", { warmth: "warm" })).toEqual(["lamp", "ceiling"]);
    expect(actionTargets("flash_lights", { room: "living_room", color: "blue", pulses: 2 })).toEqual(["lamp", "ceiling"]);
    expect(actionTargets("play_sound", { room: "living_room", sound: "chime" })).toEqual(["speaker"]);
    expect(actionTargets("stop_sound", { room: "living_room" })).toEqual(["speaker"]);
    expect(actionTargets("play_music", { query: "spotify:track:abc" })).toEqual(["speaker"]);
    expect(actionTargets("set_volume", { room: "living_room", volume: 20 })).toEqual(["speaker"]);
    expect(actionTargets("announce", { text: "hello", room: "living_room" })).toEqual(["phone"]);
    expect(actionTargets("alert_caregiver", { incident_id: "inc_1", severity: "info", message: "check" })).toEqual(["phone"]);
    const started = projectHomeToRoom({
      home: { ...emptyHome(), known: true },
      actions: [action({ id: "music", tool: "play_music", status: "started", args: { query: "spotify:track:abc" } })],
      question: null,
      now: NOW,
    });
    expect(started.speaker.playing).toBe(false);
    expect(started.pending.speaker).toBe("pending");
    const playing = projectHomeToRoom({
      home: { ...emptyHome(), known: true, speaker: { sound: "spotify:track:abc", volume: 40, playback: "playing", confirmed: true } },
      actions: [action({ id: "music", tool: "play_music", status: "verified", args: { query: "spotify:track:abc" } })],
      question: null,
      now: NOW,
    });
    expect(playing.speaker.playing).toBe(true);
    expect(playing.pending.speaker).toBeUndefined();
  });

  it("uses one deduped session for the chat and the room", () => {
    let state = sessionFrom({
      type: "plan.proposed",
      plan: {
        plan_id: "pln_1",
        intent: "Open the door",
        actions: [{ action_id: "act_door", tool: "open_door", args: { door: "front_door" } }],
        confidence: 0.8,
        trigger: { kind: "spoken" },
        consent: { required: false },
      },
    });
    state = feed(state, { type: "turn.start", turn_id: "req_same", request_id: "req_same", source: "text" });
    state = feed(state, { type: "turn.start", turn_id: "req_same", request_id: "req_same", source: "text" });
    const shared = dedupeTurns(state);
    expect(shared.turns.filter((turn) => turn.id === "req_same" || turn.requestId === "req_same")).toHaveLength(1);
    const room = projectHomeToRoom({
      home: shared.home,
      actions: Object.values(shared.actions),
      question: shared.question,
      now: NOW,
    });
    expect(room.pending.door).toBe("pending");
    expect(room.door.open).toBeNull();
    expect(shared.home).toBe(state.home);
  });

  it("keeps failed markers on the current turn and drops recorded scenes", () => {
    const home = { ...emptyHome(), known: true, lights: { "lamp.living_floor": { id: "lamp.living_floor", luminosity: 80, warmth: "neutral" as const } } };
    const base = createSession("ses_room");
    const live = {
      ...base,
      home,
      turns: [
        { id: "old", createdAt: NOW, source: "text" as const, observations: [], actionIds: ["old_fail"], open: false, recorded: false },
        { id: "now", createdAt: NOW + 1, source: "text" as const, observations: [], actionIds: ["live_fail", "live_start"], open: true, recorded: false },
        { id: "tape", createdAt: NOW + 2, source: "recorded" as const, observations: [], actionIds: ["tape_start"], open: true, recorded: true },
      ],
      actions: {
        old_fail: action({ id: "old_fail", tool: "dim_lights", status: "failed", turnId: "old", args: { light: "lamp.living_floor", luminosity: 1 } }),
        live_fail: action({ id: "live_fail", tool: "set_blinds", status: "rejected", turnId: "now", args: { room: "living_room", position: 0 } }),
        live_start: action({ id: "live_start", tool: "play_sound", status: "started", turnId: "now", args: { room: "living_room", sound: "chime" } }),
        tape_start: action({ id: "tape_start", tool: "dim_lights", status: "started", turnId: "tape", args: { light: "lamp.living_floor", luminosity: 1 } }),
      },
    };
    const shown = actionsForRoom(live, NOW);
    expect(shown.map((item) => item.id).sort()).toEqual(["live_fail", "live_start"]);
    const room = projectHomeToRoom({ home, actions: shown, question: null, now: NOW });
    expect(room.lamp.level).toBe(80);
    expect(room.pending.lamp).toBeUndefined();
    expect(room.pending.blinds).toBe("failed");
    expect(room.pending.speaker).toBe("pending");
    expect(room.speaker.playing).toBe(false);
  });

  it("shows an expired non-terminal action as unconfirmed", () => {
    const base = createSession("ses_room");
    const home = { ...emptyHome(), known: true, lights: { "lamp.living_floor": { id: "lamp.living_floor", luminosity: 80, warmth: "neutral" as const } } };
    const state = {
      ...base,
      home,
      plans: {
        pln_old: {
          planId: "pln_old",
          createdAt: new Date(NOW - 60_000).toISOString(),
          expiresAt: new Date(NOW - 1).toISOString(),
          intent: "Dim the floor lamp",
          reasoning: "",
          confidence: 0.4,
          triggerKind: "spoken",
          room: "living_room",
          consentRequired: true,
          evidence: [],
          actions: [],
        },
      },
      turns: [{ id: "t", createdAt: NOW, source: "text" as const, observations: [], actionIds: ["act"], open: true, recorded: false }],
      actions: {
        act: action({ id: "act", planId: "pln_old", tool: "dim_lights", status: "started", turnId: "t", args: { light: "lamp.living_floor", luminosity: 30 } }),
      },
    };
    const shown = actionsForRoom(state, NOW);
    expect(shown[0]?.status).toBe("failed");
    const room = projectHomeToRoom({ home, actions: shown, question: null, now: NOW });
    expect(room.pending.lamp).toBe("failed");
    expect(room.lamp.level).toBe(80);
  });
});

function sessionFrom(raw: unknown): SessionState {
  return normalizeEvent(raw, { now: NOW }).reduce((state, event) => reduceSession(state, event, { now: NOW }), createSession("ses_room"));
}

function feed(state: SessionState, raw: unknown): SessionState {
  return normalizeEvent(raw, { now: NOW }).reduce((current, event) => reduceSession(current, event, { now: NOW }), state);
}
