import assert from "node:assert/strict";
import test from "node:test";
import { applySnapshot, createState, markSpeechPlayed, reduce } from "./reducer";
import { HomeSnapshot, UiEvent } from "./types";

function snapshot(revision: number, luminosity: number, extra: Partial<HomeSnapshot> = {}): HomeSnapshot {
  return {
    revision,
    updated_at: "2026-10-18T01:00:00Z",
    rooms: ["living_room"],
    lights: [
      {
        id: "lamp.living_floor",
        room: "living_room",
        label: "floor lamp",
        luminosity,
        warmth: "neutral",
        color: "#FFF1D0",
      },
    ],
    blinds: [{ id: "blinds.living_window", room: "living_room", label: "blinds", position: 100 }],
    media: [],
    flashes: [],
    captions: [],
    alerts: [],
    incident: null,
    ...extra,
  };
}

function event(sequence: number, type: string, payload: Record<string, unknown>): UiEvent {
  return {
    event_id: `evt_${sequence}`,
    session_id: "ses_demo",
    sequence,
    timestamp: "2026-10-18T01:00:00Z",
    type,
    payload,
  };
}

test("newer brightness survives an expired flash and an older snapshot", () => {
  let state = createState();
  state = applySnapshot(
    state,
    snapshot(1, 40, {
      flashes: [
        {
          id: "flash",
          room: "living_room",
          light_ids: ["lamp.living_floor"],
          color: "blue",
          pulses: 2,
          started_at: "2026-10-18T01:00:00Z",
          ends_at: "2026-10-18T01:00:02Z",
          on_ms: 600,
          off_ms: 600,
        },
      ],
    }),
    true,
  );
  state = applySnapshot(state, snapshot(2, 10), false);
  state = applySnapshot(state, snapshot(1, 40), false);
  assert.equal(state.lights[0].luminosity, 10);
  assert.deepEqual(state.flashes, []);
});

test("captions from an older revision cannot erase a newer caption", () => {
  let state = createState();
  state = applySnapshot(
    state,
    snapshot(3, 80, {
      captions: [
        {
          id: "new",
          text: "Newer note",
          priority: "normal",
          created_at: "2026-10-18T01:00:02Z",
          expires_at: "2026-10-18T01:00:12Z",
          source: "show_caption",
          resolved: false,
          message_id: "new",
          speak: false,
          audio_url: null,
          playback: "none",
        },
      ],
    }),
    true,
  );
  state = applySnapshot(
    state,
    snapshot(2, 80, {
      captions: [
        {
          id: "old",
          text: "Old note",
          priority: "normal",
          created_at: "2026-10-18T01:00:00Z",
          expires_at: "2026-10-18T01:00:10Z",
          source: "show_caption",
          resolved: false,
          message_id: "old",
          speak: false,
          audio_url: null,
          playback: "none",
        },
      ],
    }),
    false,
  );
  assert.deepEqual(state.captions.map((caption) => caption.text), ["Newer note"]);
});

test("reconnect does not replay speech that was already in the snapshot", () => {
  const spoken = snapshot(4, 80, {
    captions: [
      {
        id: "msg_old",
        text: "Are you okay?",
        priority: "high",
        created_at: "2026-10-18T01:00:00Z",
        expires_at: null,
        source: "announce",
        resolved: false,
        message_id: "msg_old",
        speak: true,
        audio_url: "/api/audio/aud_old",
        playback: "generated",
      },
    ],
  });
  let state = applySnapshot(createState(), spoken, true);
  assert.deepEqual(state.speechQueue, []);
  assert.deepEqual(state.historicalSpeech, ["msg_old"]);
  state = applySnapshot(
    state,
    snapshot(5, 80, {
      captions: [
        ...spoken.captions,
        {
          id: "msg_new",
          text: "Brightness updated.",
          priority: "normal",
          created_at: "2026-10-18T01:00:04Z",
          expires_at: "2026-10-18T01:00:14Z",
          source: "announce",
          resolved: false,
          message_id: "msg_new",
          speak: true,
          audio_url: "/api/audio/aud_new",
          playback: "generated",
        },
      ],
    }),
    false,
  );
  assert.deepEqual(state.speechQueue, ["msg_new"]);
  state = markSpeechPlayed(state, "msg_new");
  state = applySnapshot(state, snapshot(6, 80, { captions: state.captions }), false);
  assert.deepEqual(state.speechQueue, []);
});

test("activity follows requested, executing, and unverified results", () => {
  let state = reduce(
    createState(),
    event(1, "plan.proposed", {
      plan: {
        plan_id: "pln_1",
        actions: [{ action_id: "act_1", tool: "play_sound" }],
      },
    }),
  );
  state = reduce(state, event(2, "action.started", { action_id: "act_1", plan_id: "pln_1", tool: "play_sound" }));
  state = reduce(
    state,
    event(3, "action.result", {
      result: {
        action_id: "act_1",
        plan_id: "pln_1",
        tool: "play_sound",
        status: "unknown",
        verification: "unverified",
        error_code: "spotify_unverified",
      },
    }),
  );
  assert.equal(state.activity[0].status, "unknown");
  assert.equal(state.activity[0].verification, "unverified");
  const ignored = reduce(state, event(2, "action.started", { action_id: "act_1", plan_id: "pln_1", tool: "play_sound" }));
  assert.equal(ignored.activity[0].status, "unknown");
});
