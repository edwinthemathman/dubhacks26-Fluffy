import type { TimedEvent } from "./events";

export const SCENES = [
  { id: "describe_find", label: "Describe & find", profile: "blind" },
  { id: "hands_free", label: "Hands-free", profile: "speech_cognitive" },
  { id: "doorbell", label: "Sound to light", profile: "deaf" },
  { id: "fall", label: "Fall check-in", profile: "any" },
] as const;

export type SceneId = (typeof SCENES)[number]["id"];

export const timelines: Record<SceneId, TimedEvent[]> = {
  describe_find: [
    { t: 0, event: { type: "profile", profile: "blind", needs: ["speech", "spatial"] } },
    { t: 200, event: { type: "fallback", active: true, scene: "describe_find" } },
    {
      t: 400,
      event: { type: "sense.see", chips: ["keys on table", "left of mug", "letter in hand"] },
    },
    { t: 900, event: { type: "sense.hear", turn_id: "t1", text: "Fluffy, where are my keys?" } },
    { t: 1000, event: { type: "turn.start", turn_id: "t1", kind: "spoken", trigger: "where are my keys?" } },
    {
      t: 1100,
      event: {
        type: "turn.raw",
        turn_id: "t1",
        text: "User is blind. Last keys observation was the coffee table, left of the mug, about two minutes ago.",
      },
    },
    { t: 1500, event: { type: "think", turn_id: "t1", note: "keys last seen on table" } },
    {
      t: 1900,
      event: {
        type: "tool.call",
        turn_id: "t1",
        call_id: "c1",
        tool: "find_object",
        args: { object: "keys" },
        why: "keys → table, left of mug",
        kind: "meta",
      },
    },
    { t: 2100, event: { type: "tool.status", call_id: "c1", status: "done" } },
    {
      t: 2600,
      event: {
        type: "say",
        turn_id: "t1",
        text: "On the table, to the left of your mug. I saw them two minutes ago.",
      },
    },
    { t: 3000, event: { type: "turn.end", turn_id: "t1", ms: 2000, outcome: "acted" } },
    { t: 5200, event: { type: "sense.hear", turn_id: "t2", text: "What does this letter say?" } },
    {
      t: 5400,
      event: { type: "sense.see", chips: ["letter close", "print readable", "held toward camera"] },
    },
    { t: 5600, event: { type: "turn.start", turn_id: "t2", kind: "spoken", trigger: "what does this letter say?" } },
    { t: 6100, event: { type: "think", turn_id: "t2", note: "hold steady, print readable" } },
    {
      t: 6600,
      event: {
        type: "tool.call",
        turn_id: "t2",
        call_id: "c2",
        tool: "read_text",
        args: { target: "letter" },
        why: "letter → sender + points",
        kind: "meta",
      },
    },
    { t: 6900, event: { type: "tool.status", call_id: "c2", status: "done" } },
    {
      t: 7600,
      event: {
        type: "say",
        turn_id: "t2",
        text: "From Maya. Appointment Thursday at 2. Bring your ID.",
      },
    },
    { t: 8200, event: { type: "turn.end", turn_id: "t2", ms: 2600, outcome: "acted" } },
  ],
  hands_free: [
    { t: 0, event: { type: "profile", profile: "speech_cognitive", needs: ["confirm", "short"] } },
    { t: 200, event: { type: "fallback", active: true, scene: "hands_free" } },
    { t: 500, event: { type: "sense.see", chips: ["pointing → lamp", "on sofa", "lamp at 78%"] } },
    { t: 1100, event: { type: "sense.hear", turn_id: "t3", text: "that one… dimmer" } },
    { t: 1300, event: { type: "turn.start", turn_id: "t3", kind: "spoken", trigger: "that one… dimmer" } },
    { t: 1800, event: { type: "think", turn_id: "t3", note: 'heard: "that one dimmer"' } },
    { t: 2300, event: { type: "think", turn_id: "t3", note: "target? pointing → lamp" } },
    { t: 2800, event: { type: "think", turn_id: "t3", note: "speech profile → confirm back" } },
    {
      t: 3300,
      event: {
        type: "tool.call",
        turn_id: "t3",
        call_id: "c3",
        tool: "dim_lights",
        args: { room: "living_room", light: "lamp.living_floor", luminosity: 35 },
        why: "pointed lamp, dimmer",
        kind: "device",
      },
    },
    {
      t: 3500,
      event: { type: "tool.status", call_id: "c3", plan_id: "pln_dim", status: "asking", detail: "Dim the floor lamp?" },
    },
    { t: 3700, event: { type: "ask", turn_id: "t3", question: "Dim the floor lamp?", choices: ["yes", "no"] } },
    { t: 3900, event: { type: "say", turn_id: "t3", text: "Dim the floor lamp?" } },
    { t: 9000, event: { type: "ask.answer", turn_id: "t3", by: "nod", choice: "yes" } },
    { t: 9400, event: { type: "tool.status", call_id: "c3", plan_id: "pln_dim", status: "approved" } },
    { t: 10000, event: { type: "tool.status", call_id: "c3", plan_id: "pln_dim", status: "done" } },
    { t: 10200, event: { type: "state", lamp: { luminosity: 35, warmth: "warm" } } },
    { t: 10600, event: { type: "say", turn_id: "t3", text: "Dimmed." } },
    { t: 11000, event: { type: "turn.end", turn_id: "t3", ms: 10000, outcome: "asked" } },
  ],
  doorbell: [
    { t: 0, event: { type: "profile", profile: "deaf", needs: ["caption", "light"] } },
    { t: 150, event: { type: "fallback", active: true, scene: "doorbell" } },
    { t: 400, event: { type: "sense.see", chips: ["on sofa", "back to door", "tv on"] } },
    { t: 900, event: { type: "sense.hear", turn_id: "t4", sound: "doorbell", text: "" } },
    { t: 1000, event: { type: "turn.start", turn_id: "t4", kind: "sound", trigger: "doorbell rang" } },
    { t: 1500, event: { type: "think", turn_id: "t4", note: "doorbell, user deaf → visual" } },
    {
      t: 1900,
      event: {
        type: "tool.call",
        turn_id: "t4",
        call_id: "c4",
        tool: "flash_lights",
        args: { room: "living_room", color: "blue", pattern: "pulse3" },
        why: "deaf → light",
        kind: "device",
      },
    },
    { t: 2100, event: { type: "tool.status", call_id: "c4", status: "approved" } },
    {
      t: 2300,
      event: { type: "state", flash: { active: true, color: "blue", pattern: "pulse3" } },
    },
    { t: 2500, event: { type: "tool.status", call_id: "c4", status: "done" } },
    {
      t: 2700,
      event: {
        type: "tool.call",
        turn_id: "t4",
        call_id: "c5",
        tool: "show_caption",
        args: { text: "Doorbell. Someone is at the front door.", size: "xl" },
        why: "say who's there",
        kind: "device",
      },
    },
    { t: 2900, event: { type: "tool.status", call_id: "c5", status: "done" } },
    {
      t: 3100,
      event: {
        type: "state",
        caption: "Doorbell. Someone is at the front door.",
      },
    },
    {
      t: 3200,
      event: { type: "say", turn_id: "t4", text: "Doorbell. Someone is at the front door." },
    },
    { t: 3600, event: { type: "turn.end", turn_id: "t4", ms: 1900, outcome: "acted" } },
    {
      t: 7800,
      event: { type: "state", flash: { active: false, color: null, pattern: null } },
    },
  ],
  fall: [
    { t: 0, event: { type: "profile", profile: "blind", needs: ["speech", "caption"] } },
    { t: 200, event: { type: "fallback", active: true, scene: "fall" } },
    { t: 600, event: { type: "sense.see", chips: ["on floor", "still", "yoga mat"] } },
    { t: 900, event: { type: "turn.start", turn_id: "t5", kind: "safety", trigger: "still on the floor" } },
    { t: 1400, event: { type: "think", turn_id: "t5", note: "on floor, still" } },
    { t: 1700, event: { type: "safety", state: "checkin1" } },
    { t: 1900, event: { type: "ask", turn_id: "t5", question: "Are you okay?", choices: ["yes", "no"] } },
    { t: 2000, event: { type: "say", turn_id: "t5", text: "Are you okay?" } },
    { t: 5600, event: { type: "ask.answer", turn_id: "t5", by: "none" } },
    { t: 5900, event: { type: "think", turn_id: "t5", note: "no answer → ask again" } },
    { t: 6200, event: { type: "safety", state: "checkin2" } },
    { t: 6400, event: { type: "ask", turn_id: "t5", question: "Are you okay? Please answer.", choices: ["yes", "no"] } },
    { t: 6600, event: { type: "say", turn_id: "t5", text: "Are you okay? Please answer." } },
    { t: 10200, event: { type: "ask.answer", turn_id: "t5", by: "none" } },
    { t: 10500, event: { type: "think", turn_id: "t5", note: "no answer twice → alert" } },
    {
      t: 10900,
      event: {
        type: "tool.call",
        turn_id: "t5",
        call_id: "c6",
        tool: "alert_caregiver",
        args: { severity: "high", message: "Sam may have fallen in the living room." },
        why: "no answer twice",
        kind: "device",
      },
    },
    { t: 11200, event: { type: "tool.status", call_id: "c6", status: "approved" } },
    { t: 11600, event: { type: "tool.status", call_id: "c6", status: "done" } },
    { t: 11800, event: { type: "safety", state: "alerted" } },
    { t: 12000, event: { type: "say", turn_id: "t5", text: "Alex has been notified." } },
    { t: 12400, event: { type: "turn.end", turn_id: "t5", ms: 11500, outcome: "acted" } },
  ],
};

export function isSceneId(value: string | null): value is SceneId {
  return value === "describe_find" || value === "hands_free" || value === "doorbell" || value === "fall";
}
