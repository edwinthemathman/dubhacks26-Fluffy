/** Recorded demo scenes from the Fluffy harness plan. No live executor. */

export type TriggerKind = "spoken" | "inferred";

export type Action = {
  action_id: string;
  tool: string;
  args: Record<string, string | number | boolean>;
  sensitivity: "low" | "high" | "critical";
  reversible: boolean;
};

export type Evidence =
  | { kind: "speech"; text: string; at: string }
  | { kind: "vision"; summary: string; camera: string; frame_id: string }
  | {
      kind: "state";
      device_id: string;
      value: Record<string, string | number | boolean>;
    };

export type Plan = {
  plan_id: string;
  created_at: string;
  expires_at: string;
  trigger: {
    kind: TriggerKind;
    utterance?: string;
    event_ids?: string[];
    room: string;
  };
  intent: string;
  actions: Action[];
  ordering: "sequential";
  confidence: number;
  requires_confirmation: boolean;
  reasoning_summary: string;
  evidence: Evidence[];
  context_snapshot_id: string;
};

export type DeviceChange = {
  device: string;
  label: string;
  from: string;
  to: string;
};

export type Scene = {
  id: string;
  title: string;
  /** phrases the typewriter cycles, and loose text that should hit this scene */
  examples: string[];
  plan: Plan;
  tookMs: number;
  chime: boolean;
  /** spoken only when Fluffy must ask or explain. otherwise null. */
  spoken: string | null;
  changes: DeviceChange[];
  skipped: { label: string; why: string }[];
  confirm: { question: string; then: DeviceChange } | null;
};

const AT = "2026-10-18T22:41:05Z";

export const SCENES: Scene[] = [
  {
    id: "glare",
    title: "Reading glare",
    examples: ["ugh, it's bright", "it's too much", "dim that light"],
    tookMs: 2100,
    chime: true,
    spoken: null,
    confirm: null,
    skipped: [],
    changes: [
      {
        device: "lamp.living_floor",
        label: "Floor lamp",
        from: "100%",
        to: "35%",
      },
    ],
    plan: {
      plan_id: "pln_glare",
      created_at: AT,
      expires_at: "2026-10-18T22:41:35Z",
      trigger: {
        kind: "spoken",
        utterance: "ugh, it's bright",
        room: "living_room",
      },
      intent: "Reduce glare for the person reading on the sofa",
      ordering: "sequential",
      confidence: 0.86,
      requires_confirmation: false,
      reasoning_summary:
        "They said it's bright while shielding their eyes toward the floor lamp, which is at 100%.",
      context_snapshot_id: "ctx_glare",
      evidence: [
        { kind: "speech", text: "ugh, it's bright", at: AT },
        {
          kind: "vision",
          summary: "Person on the sofa, shielding their eyes toward the floor lamp",
          camera: "living_room",
          frame_id: "frm_glare",
        },
        {
          kind: "state",
          device_id: "lamp.living_floor",
          value: { on: true, luminosity: 100 },
        },
      ],
      actions: [
        {
          action_id: "act_1",
          tool: "dim_lights",
          args: { room: "living_room", luminosity: 35, light: "lamp.living_floor" },
          sensitivity: "low",
          reversible: true,
        },
      ],
    },
  },
  {
    id: "bed",
    title: "Winding down",
    examples: ["I'm heading to bed", "going to sleep", "night, Fluffy"],
    tookMs: 2400,
    chime: true,
    spoken: null,
    confirm: null,
    skipped: [],
    changes: [
      { device: "light.bedroom", label: "Bedroom lights", from: "70%", to: "15%" },
      { device: "door.bedroom", label: "Bedroom door", from: "open", to: "shut" },
      {
        device: "speaker.bedroom",
        label: "Bedroom speaker",
        from: "quiet",
        to: "rain, low",
      },
    ],
    plan: {
      plan_id: "pln_bed",
      created_at: "2026-10-18T22:55:10Z",
      expires_at: "2026-10-18T22:55:40Z",
      trigger: {
        kind: "spoken",
        utterance: "I'm heading to bed",
        room: "living_room",
      },
      intent: "Settle the bedroom for the night",
      ordering: "sequential",
      confidence: 0.91,
      requires_confirmation: false,
      reasoning_summary:
        "They said they're heading to bed. The bedtime scene dims the room, shuts the door, and plays rain quietly.",
      context_snapshot_id: "ctx_bed",
      evidence: [
        { kind: "speech", text: "I'm heading to bed", at: "2026-10-18T22:55:10Z" },
        {
          kind: "vision",
          summary: "Person standing, activity leaving the living room",
          camera: "living_room",
          frame_id: "frm_bed",
        },
      ],
      actions: [
        {
          action_id: "act_1",
          tool: "dim_lights",
          args: { room: "bedroom", luminosity: 15 },
          sensitivity: "low",
          reversible: true,
        },
        {
          action_id: "act_2",
          tool: "shut_door",
          args: { door_name: "door.bedroom" },
          sensitivity: "low",
          reversible: true,
        },
        {
          action_id: "act_3",
          tool: "play_sound",
          args: { sound: "rain", room: "bedroom", volume: 20 },
          sensitivity: "low",
          reversible: true,
        },
      ],
    },
  },
  {
    id: "leaving",
    title: "Leaving the house",
    examples: ["I left the front door open", "I'm heading out"],
    tookMs: 1800,
    chime: true,
    spoken: "Lock the front door?",
    confirm: {
      question: "Lock the front door?",
      then: {
        device: "door.front",
        label: "Front door",
        from: "shut, unlocked",
        to: "locked",
      },
    },
    skipped: [],
    changes: [
      { device: "door.front", label: "Front door", from: "open", to: "shut" },
    ],
    plan: {
      plan_id: "pln_leave",
      created_at: "2026-10-18T08:12:00Z",
      expires_at: "2026-10-18T08:12:30Z",
      trigger: {
        kind: "inferred",
        event_ids: ["evt_room_empty"],
        room: "entryway",
      },
      intent: "Close the front door after the room emptied",
      ordering: "sequential",
      confidence: 0.84,
      requires_confirmation: false,
      reasoning_summary:
        "The entryway has been empty with the front door open. Shutting it is safe. Locking waits for a yes.",
      context_snapshot_id: "ctx_leave",
      evidence: [
        {
          kind: "vision",
          summary: "Entryway empty, front door open",
          camera: "entryway",
          frame_id: "frm_leave",
        },
        {
          kind: "state",
          device_id: "door.front",
          value: { state: "open" },
        },
      ],
      actions: [
        {
          action_id: "act_1",
          tool: "shut_door",
          args: { door_name: "door.front" },
          sensitivity: "low",
          reversible: true,
        },
      ],
    },
  },
  {
    id: "guest",
    title: "Guest arrives",
    examples: ["someone's at the door", "there's a guest"],
    tookMs: 2200,
    chime: true,
    spoken: null,
    confirm: null,
    skipped: [],
    changes: [
      {
        device: "speaker.entry",
        label: "Entry speaker",
        from: "quiet",
        to: "doorbell",
      },
      { device: "light.entry", label: "Entry lights", from: "dim", to: "bright" },
    ],
    plan: {
      plan_id: "pln_guest",
      created_at: "2026-10-18T19:04:20Z",
      expires_at: "2026-10-18T19:04:50Z",
      trigger: {
        kind: "inferred",
        event_ids: ["evt_guest"],
        room: "entryway",
      },
      intent: "Greet the person who just appeared at the door",
      ordering: "sequential",
      confidence: 0.88,
      requires_confirmation: false,
      reasoning_summary:
        "A new person is in the entryway. The doorbell plays and the entry lights come up.",
      context_snapshot_id: "ctx_guest",
      evidence: [
        {
          kind: "vision",
          summary: "New arrival in the entryway",
          camera: "entryway",
          frame_id: "frm_guest",
        },
        {
          kind: "state",
          device_id: "light.entry",
          value: { on: true, luminosity: 20 },
        },
      ],
      actions: [
        {
          action_id: "act_1",
          tool: "play_sound",
          args: { sound: "doorbell", room: "entryway", volume: 60 },
          sensitivity: "low",
          reversible: true,
        },
        {
          action_id: "act_2",
          tool: "dim_lights",
          args: { room: "entryway", luminosity: 100, light: "light.entry" },
          sensitivity: "low",
          reversible: true,
        },
      ],
    },
  },
  {
    id: "asleep",
    title: "Everyone's asleep",
    examples: ["everyone's asleep", "the TV is still on and we're all asleep"],
    tookMs: 3400,
    chime: true,
    spoken: null,
    confirm: null,
    changes: [
      { device: "appliance.tv", label: "TV", from: "on", to: "off" },
      { device: "appliance.console", label: "Game console", from: "on", to: "off" },
      { device: "appliance.heater", label: "Space heater", from: "on", to: "off" },
    ],
    skipped: [
      { label: "Fridge", why: "Essential. Left running." },
      { label: "Phone", why: "Charging, so it's in use. Left alone." },
    ],
    plan: {
      plan_id: "pln_asleep",
      created_at: "2026-10-18T23:10:00Z",
      expires_at: "2026-10-18T23:10:30Z",
      trigger: {
        kind: "inferred",
        event_ids: ["evt_all_asleep"],
        room: "living_room",
      },
      intent: "Turn off what nobody is using now that the house is asleep",
      ordering: "sequential",
      confidence: 0.9,
      requires_confirmation: false,
      reasoning_summary:
        "Every occupied room has been asleep since after 21:00. The TV, console, and heater are on and idle. The fridge is essential and the phone is charging.",
      context_snapshot_id: "ctx_asleep",
      evidence: [
        {
          kind: "vision",
          summary: "Person on the sofa, eyes closed, activity sleeping",
          camera: "living_room",
          frame_id: "frm_asleep",
        },
        {
          kind: "state",
          device_id: "appliance.tv",
          value: { on: true, in_use: false },
        },
      ],
      actions: [
        {
          action_id: "act_1",
          tool: "set_appliance_power",
          args: { appliance: "appliance.tv", on: false },
          sensitivity: "high",
          reversible: true,
        },
        {
          action_id: "act_2",
          tool: "set_appliance_power",
          args: { appliance: "appliance.console", on: false },
          sensitivity: "high",
          reversible: true,
        },
        {
          action_id: "act_3",
          tool: "set_appliance_power",
          args: { appliance: "appliance.heater", on: false },
          sensitivity: "high",
          reversible: true,
        },
      ],
    },
  },
];

export const EXAMPLE_CUES = SCENES.map((s) => s.examples[0]);

const MATCHERS: { id: string; test: RegExp }[] = [
  { id: "asleep", test: /asleep|everyone|still on/ },
  { id: "guest", test: /guest|someone|doorbell/ },
  { id: "leaving", test: /front door|heading out|left the house|lock the/ },
  { id: "bed", test: /bed|going to sleep|\bnight\b/ },
  { id: "glare", test: /bright|glare|dim|lamp|too much|\bugh\b/ },
];

export function matchScene(text: string): Scene | null {
  const t = text.toLowerCase();
  const hit = MATCHERS.find((m) => m.test.test(t));
  return SCENES.find((s) => s.id === hit?.id) ?? null;
}

export function roomLabel(room: string) {
  return room.replaceAll("_", " ");
}

export function toolLabel(tool: string) {
  return tool.replaceAll("_", " ");
}

export const EVIDENCE_LABEL: Record<Evidence["kind"], string> = {
  speech: "Speech",
  vision: "Vision",
  state: "Home state",
};
