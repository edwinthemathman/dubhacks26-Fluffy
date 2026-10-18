import { describe, expect, it } from "vitest";
import { roomReducer } from "../src/reducer";
import { initialRoom, type RoomEvent, type RoomState } from "../src/state";
import { toRoomEvent } from "../src/events";

const NOW = 1_700_000_000_000;

function act(tool: string, args: Record<string, unknown>, status: string, result?: Record<string, unknown>, id = tool): RoomState {
  const call: RoomEvent = { type: "tool.call", call_id: id, tool, args };
  const done: RoomEvent = { type: "tool.status", call_id: id, status, result, now: NOW };
  return [call, done].reduce((room, event) => roomReducer(room, event), initialRoom());
}

describe("roomReducer", () => {
  it("waits for done before a tool changes the room", () => {
    const proposed = roomReducer(initialRoom(), {
      type: "tool.call",
      call_id: "a",
      tool: "dim_lights",
      args: { luminosity: 30 },
    });
    expect(proposed.light.level).toBe(78);
    const asking = roomReducer(proposed, { type: "tool.status", call_id: "a", status: "asking", now: NOW });
    expect(asking.light.level).toBe(78);
    expect(asking.pending.light).toBe("asking");
    const done = roomReducer(asking, { type: "tool.status", call_id: "a", status: "done", now: NOW });
    expect(done.light.level).toBe(30);
    expect(done.pending.light).toBeUndefined();
  });

  it("maps each tool and alias onto the right object", () => {
    expect(act("dim_lights", { luminosity: 30 }, "done").light.level).toBe(30);
    expect(act("set_brightness", { brightness: 80 }, "done").light.level).toBe(80);
    expect(act("set_light_warmth", { warmth: "cool" }, "done").light.warmth).toBe("cool");
    expect(act("flash_lights", { color: "blue", duration_s: 2 }, "done").light.flash).toEqual({
      color: "#3b82f6",
      until: NOW + 2000,
    });
    expect(act("close_door", {}, "done").door.open).toBe(false);
    expect(act("open_door", {}, "done").door.open).toBe(true);
    expect(act("set_door", { open: false }, "done").door.open).toBe(false);
    const music = act("spotify_play", {}, "done", { track: "Clair de Lune", artist: "Debussy" });
    expect(music.speaker).toMatchObject({ playing: true, track: "Clair de Lune", artist: "Debussy" });
    expect(act("play_sound", { sound: "rain" }, "done").speaker.track).toBe("rain");
    expect(act("stop_music", {}, "done", undefined, "stop").speaker.playing).toBe(false);
    const text = act("photon_send", { to: "Alex", text: "Sam may need help", channel: "sms" }, "done", {
      delivered: true,
    });
    expect(text.phone).toMatchObject({
      mode: "texting",
      to: "Alex",
      preview: "Sam may need help",
      channel: "sms",
      delivered: true,
      until: NOW + 4000,
    });
    expect(act("call_person", { to: "Alex" }, "done").phone.mode).toBe("calling");
    expect(act("call_person", { to: "Alex" }, "done", { ended: true }).phone.mode).toBe("idle");
    const ask = act("ask_user", { question: "Leave the lamp on?", choices: ["Yes", "No"] }, "done");
    expect(ask.ask).toEqual({ question: "Leave the lamp on?", choices: ["Yes", "No"] });
  });

  it("shakes and changes nothing when a call is declined or fails", () => {
    for (const status of ["rejected", "declined", "failed"]) {
      const room = act("close_door", {}, status);
      expect(room.door.open).toBe(true);
      expect(room.pending.door).toBe("failed");
    }
    const spotify = act("spotify_play", { track: "Clair de Lune" }, "done", { failed: true });
    expect(spotify.speaker.playing).toBe(false);
    expect(spotify.pending.speaker).toBe("failed");
  });

  it("lets a state event override the room and clear a waiting outline", () => {
    const asking = roomReducer(
      roomReducer(initialRoom(), { type: "tool.call", call_id: "a", tool: "dim_lights", args: { luminosity: 10 } }),
      { type: "tool.status", call_id: "a", status: "asking", now: NOW },
    );
    const next = roomReducer(asking, {
      type: "state",
      lamp: { luminosity: 12, warmth: "cool" },
      door: { open: false },
      speaker: { sound: "music", volume: 40 },
      now: NOW,
    });
    expect(next.light.level).toBe(12);
    expect(next.light.warmth).toBe("cool");
    expect(next.door.open).toBe(false);
    expect(next.speaker.playing).toBe(true);
    expect(next.speaker.track).toBe("music");
    expect(next.pending.light).toBeUndefined();
  });

  it("ignores unknown tools and unrelated events", () => {
    const room = act("set_blinds", { position: 0 }, "done");
    expect(room.light).toEqual(initialRoom().light);
    expect(room.door).toEqual(initialRoom().door);
    expect(room.speaker).toEqual(initialRoom().speaker);
    expect(room.phone).toEqual(initialRoom().phone);
    expect(roomReducer(initialRoom(), { type: "sense.see" } as unknown as RoomEvent)).toEqual(initialRoom());
  });

  it("shows a Photon reply and clears an ask when the person answers", () => {
    const reply = roomReducer(initialRoom(), {
      type: "message.received",
      from: "Alex",
      text: "On my way",
      channel: "imessage",
      now: NOW,
    });
    expect(reply.phone).toMatchObject({ mode: "incoming", to: "Alex", preview: "On my way" });
    const asked = roomReducer(initialRoom(), { type: "ask", question: "Leave the lamp on?" });
    expect(roomReducer(asked, { type: "ask.answer" }).ask).toBeNull();
  });

  it("expires a flash and a failed outline on a tick", () => {
    const flashed = act("flash_lights", { color: "red", duration_s: 1 }, "done");
    const later = roomReducer(flashed, { type: "tick", now: NOW + 1000 });
    expect(later.light.flash).toBeUndefined();
    const failed = act("open_door", {}, "failed");
    const shaken = roomReducer(failed, { type: "tick", now: NOW + 800 });
    expect(shaken.pending.door).toBeUndefined();
    expect(shaken.door.open).toBe(true);
  });
});

describe("toRoomEvent", () => {
  it("keeps harness tool and state messages and drops the rest", () => {
    expect(
      toRoomEvent({ type: "tool.call", call_id: "c1", tool: "dim_lights", args: { luminosity: 30 } }),
    ).toMatchObject({ type: "tool.call", tool: "dim_lights" });
    expect(toRoomEvent({ type: "tool.status", call_id: "c1", status: "done" })?.type).toBe("tool.status");
    expect(toRoomEvent({ type: "state", lamp: { luminosity: 40, warmth: "warm" } })).toMatchObject({
      type: "state",
      lamp: { luminosity: 40, warmth: "warm" },
    });
    expect(toRoomEvent({ type: "think", note: "quiet" })).toBeNull();
  });
});
