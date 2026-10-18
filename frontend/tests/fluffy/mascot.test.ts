import { describe, expect, it } from "vitest";
import { historyActivity, mascotCue } from "@/lib/fluffy/mascot";
import { createSession } from "@/lib/fluffy/reducer";
import type { SensingFacts } from "@/lib/fluffy/phrases";
import type { ActionModel, SessionState } from "@/lib/fluffy/types";

const quiet: SensingFacts = {
  started: false,
  paused: false,
  preview: false,
  cameraOn: false,
  transport: "idle",
  inferenceAvailable: false,
};

const watching: SensingFacts = {
  started: true,
  paused: false,
  preview: true,
  cameraOn: true,
  transport: "reaching",
  inferenceAvailable: true,
};

function open(partial: Partial<SessionState> = {}): SessionState {
  return { ...createSession("ses"), connection: "open", ...partial };
}

function withAction(action: ActionModel, partial: Partial<SessionState> = {}): SessionState {
  return open({
    actions: { [action.id]: action },
    turns: [
      {
        id: "turn_1",
        createdAt: 1,
        source: "text",
        observations: [],
        actionIds: [action.id],
        planId: action.planId,
        open: true,
        recorded: false,
      },
    ],
    ...partial,
  });
}

function act(partial: Pick<ActionModel, "id" | "tool" | "status"> & Partial<ActionModel>): ActionModel {
  return { args: {}, updatedAt: 1, ...partial };
}

describe("mascot cue", () => {
  it("waits with idle when the session is ready", () => {
    expect(mascotCue(open(), quiet)).toMatchObject({ activity: "idle", status: "Ready" });
    expect(mascotCue(open(), watching)).toMatchObject({ activity: "idle", status: "Watching and listening" });
  });

  it("greets once when a session start is requested", () => {
    expect(mascotCue(open(), watching, { greet: true }).activity).toBe("greeting");
    expect(mascotCue(open({ inference: "running" }), watching, { greet: true }).activity).toBe("working");
  });

  it("walks while perceptron work or an approved tool is executing", () => {
    expect(mascotCue(open({ inference: "running", stage: "Reading the room" }), quiet)).toMatchObject({
      activity: "working",
      status: "Reading the room",
    });
    expect(mascotCue(open({ observationPending: true }), quiet).activity).toBe("working");
    expect(mascotCue(withAction(act({ id: "a1", tool: "dim_lights", status: "started" })), quiet).activity).toBe("working");
  });

  it("stays idle for a proposal and for an unanswered question", () => {
    expect(mascotCue(withAction(act({ id: "a1", tool: "dim_lights", status: "proposed" })), quiet)).toMatchObject({
      activity: "idle",
      status: "Proposed action",
    });
    expect(mascotCue(withAction(act({ id: "a1", tool: "dim_lights", status: "approved" })), quiet).activity).toBe("idle");
    const asked = withAction(act({ id: "a1", tool: "dim_lights", status: "verified", summary: "Lights updated" }), {
      question: { id: "q1", text: "Dim the lamp?", choices: ["Yes", "No"] },
    });
    expect(mascotCue(asked, quiet)).toMatchObject({ activity: "idle", status: "Dim the lamp?" });
  });

  it("celebrates a confirmed routine completion and not a proposal", () => {
    const verified = withAction(act({ id: "a1", tool: "dim_lights", status: "verified", summary: "Lights set to 30%" }));
    expect(mascotCue(verified, quiet)).toMatchObject({ activity: "success", playKey: "a1", status: "Lights set to 30%" });
    expect(mascotCue(withAction(act({ id: "a2", tool: "send_text", status: "delivered" })), quiet).activity).toBe("success");
    expect(mascotCue(withAction(act({ id: "a3", tool: "send_text", status: "accepted" })), quiet).activity).not.toBe("success");
  });

  it("does not celebrate caregiver alerts, flashes, safety plans, or recorded scenes", () => {
    expect(mascotCue(withAction(act({ id: "a1", tool: "alert_caregiver", status: "delivered" })), quiet).activity).toBe("idle");
    expect(mascotCue(withAction(act({ id: "a1", tool: "flash_lights", status: "verified" })), quiet).activity).toBe("idle");
    const safety = withAction(act({ id: "a1", tool: "dim_lights", status: "verified", planId: "p1" }), {
      plans: {
        p1: {
          planId: "p1",
          createdAt: "",
          intent: "",
          reasoning: "",
          confidence: null,
          triggerKind: "",
          room: "",
          consentRequired: false,
          evidence: [],
          actions: [],
          actionClass: "safety",
        },
      },
    });
    expect(mascotCue(safety, quiet).activity).toBe("idle");
    const recorded = withAction(act({ id: "a1", tool: "dim_lights", status: "verified" }));
    recorded.turns[0].recorded = true;
    expect(mascotCue(recorded, quiet).activity).toBe("idle");
    expect(mascotCue(withAction(act({ id: "a1", tool: "dim_lights", status: "verified" }), { incident: { id: "i", status: "open", summary: "Fall" } }), quiet).activity).toBe("idle");
  });

  it("uses a static sprite with a clear status for errors and disconnection", () => {
    expect(mascotCue(createSession("ses"), quiet)).toMatchObject({ activity: "static", status: "Offline" });
    expect(mascotCue({ ...createSession("ses"), connection: "reconnecting", notice: "The UI connection could not reach the backend." }, quiet)).toMatchObject({
      activity: "static",
      status: "The UI connection could not reach the backend.",
    });
    expect(mascotCue(withAction(act({ id: "a1", tool: "dim_lights", status: "failed", detail: "The lamp did not respond" })), quiet)).toMatchObject({
      activity: "static",
      status: "The lamp did not respond",
    });
    expect(mascotCue(open({ notice: "The camera read failed." }), quiet)).toMatchObject({
      activity: "static",
      status: "The camera read failed.",
    });
    expect(mascotCue(open(), quiet, { fault: "Camera permission denied" })).toMatchObject({
      activity: "static",
      status: "Camera permission denied",
    });
    expect(mascotCue(open({ notice: "Answer sent. Waiting for the backend." }), quiet).activity).toBe("idle");
    expect(mascotCue(open({ notice: "Recorded demo is on. Lines stay in the browser." }), quiet).activity).toBe("idle");
  });

  it("keeps historical cards on a still activity", () => {
    expect(historyActivity(act({ id: "a1", tool: "dim_lights", status: "verified" }))).toBe("success");
    expect(historyActivity(act({ id: "a1", tool: "dim_lights", status: "started" }))).toBe("working");
    expect(historyActivity(act({ id: "a1", tool: "alert_caregiver", status: "delivered" }))).toBe("idle");
    expect(historyActivity(act({ id: "a1", tool: "dim_lights", status: "failed" }))).toBe("static");
    expect(historyActivity(act({ id: "a1", tool: "dim_lights", status: "proposed" }))).toBe("idle");
  });
});
