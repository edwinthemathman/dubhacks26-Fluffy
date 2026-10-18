import { describe, expect, it } from "vitest";
import { projectHomeToRoom } from "@/features/room/projectHomeToRoom";
import { unknownRoom } from "@/features/room/state";
import { emptyHome } from "@/lib/fluffy/reducer";
import type { ActionModel, HomeModel } from "@/lib/fluffy/types";

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
});
