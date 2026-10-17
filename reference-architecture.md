# Fluffy: Instinctive Home Assistant Harness (Implementation Plan)

## Context
Fluffy is an always-on home assistant built for DubHacks '26. It works out what people want from loose cues: speech that isn't a clean command ("ugh, it's bright"), or nothing at all, such as someone lying down on the couch. It turns those cues into device actions.

The harness only **proposes** actions as structured plans. A separate AI, the **executor**, approves each plan and runs it. For the hackathon, devices are simulated in a mock home with a live dashboard.

**Decisions so far**
- Python 3.11+ on asyncio.
- Always-on and proactive.
- Mock home now, with the device layer swappable later.
- The executor connects to the harness over WebSocket.
- **Grok Voice is the single brain.** It decides every action, spoken or not.
- **Perceptron is the eyes.** It turns camera frames into structured scene descriptions and answers visual questions on demand.
- **Fluffy is mostly silent.** It acts quietly and plays a chime on success. It speaks only to ask a question, confirm something sensitive or explain a failure.

---

## 1. Verified API facts (Oct 2026) and what's still unknown

**Grok Voice Agent (xAI realtime)**
- Endpoint: `wss://api.x.ai/v1/realtime?model=grok-voice-latest`. That alias currently points to `grok-voice-3.0`. Auth is `Authorization: Bearer $XAI_API_KEY`.
- Protocol is close to OpenAI Realtime:
  - Client events: `session.update`, `conversation.item.create`, `response.create` (accepts per-response `instructions`), `input_audio_buffer.append/commit/clear`.
  - Server events: `response.output_audio.delta`, `response.function_call_arguments.done` (carries `name`, `call_id`, `arguments`), `response.done`, `conversation.item.input_audio_transcription.updated` (sent only when `audio.input.transcription.model = "grok-transcribe"`).
- Session options include:
  - `turn_detection.type = "server_vad" | null`, where null means manual turns
  - audio format PCM16 at 24 kHz by default
  - `transcription.keyterms`, which is where "Fluffy" goes
  - `tools`: JSON-Schema function tools
  - `reasoning.effort`
  - `resumption.enabled`: reconnect with `?conversation_id=` within 30 minutes of inactivity
- `force_message` is an item type that speaks exact text. Don't follow it with `response.create`.
- Tool results go back as a `function_call_output` item with the matching `call_id`. For parallel calls, send every output, then one `response.create`.
- **Grok cannot take images.** It can only see through a `look()` tool that calls Perceptron.
- Price (third-party figures, check the xAI pricing page): about **$0.08/min (~$4.80/hr)** while a session is open, plus about $0.004 for each injected text item. This is the main cost.

**Perceptron Mk1.5**
- SDK: `pip install "perceptron>=0.4.0"`, then `AsyncClient(api_key=..., provider="perceptron")` and `client.chat.completions.create(model="perceptron-mk1.5", ...)`. Raw endpoint: `https://api.perceptron.inc/v1/chat/completions`.
- `image()` takes a path, bytes or URL. Several images can go in one request, each counted as an asset with a 0-based `asset_idx`. A `video_frames` group can carry a short burst of frames.
- `response_format={"type":"json_schema","json_schema":{"name":..,"strict":True,"schema":..}}` constrains output to a schema. `regex=` constrains output to a pattern. **A request cannot combine tools with JSON-schema output.**
- `reasoning_effort` accepts `none | minimal | low | medium | high`. Use `none` for perception. Always check `finish_reason == "stop"`.
- Limits: 36,864-token context, 300 requests/min per org, 120 s per request.
- Price: $0.15 per million input tokens and $1.50 per million output tokens, so a frame costs fractions of a cent.

**Unknowns to check in the Milestone 0 spike** (each has a fallback):
1. Does Grok accept `role: "system"` items mid-conversation? Fallback: a `user` item prefixed with `[HOME EVENT]`.
2. Can a single response be made tool-only or text-only? Fallback: the harness throws away audio for event-triggered turns (§4.3).
3. Is Grok billed per connected minute or per audio minute? This decides whether the `on_speech` connection mode (§5) is needed.
4. Does `session.update` with new `instructions` work mid-session, and is it free? Fallback: inject context as an item at the start of each turn.
5. Is there a server event when speech starts? If so, it lets the harness refresh perception just in time.
6. Perceptron's actual latency and tokens per frame at 640×360 with `reasoning_effort="none"`.

---

## 2. Architecture

```
 ┌──────────── laptop ────────────────────────────────────────────────────────────┐
 │                                                                                │
 │  Webcam ─► CameraSource ─► ChangeDetector ──trigger──► PerceptionWorker         │
 │            (OpenCV 15fps)  (2fps diff+settle,          (Perceptron Mk1.5,       │
 │                             heartbeat, budget)          JSON-schema SceneState) │
 │                                                              │                 │
 │                                                              ▼                 │
 │  Mock Home ──state WS (source-tagged)──────────────►   WORLD MODEL             │
 │  (FastAPI :8100)                                     rooms·people·devices·     │
 │      ▲                                               events(dwell)·actions·    │
 │      │ POST /api/tools/{tool}                        transcript·feedback       │
 │      │                                                  │            │        │
 │  ┌───┴──────────┐  ws /ws/executor   ┌──────────┐  salient events  context    │
 │  │ EXECUTOR AI  │◄──plan.proposed────│ ARBITER  │       │        snapshot     │
 │  │ (separate;   │───decision/result─►│ validate,│       ▼            │        │
 │  │  stub in repo)│                   │ guardrail│◄─ BRAIN ORCHESTRATOR ◄─┘    │
 │  └──────────────┘                    │ group →  │  tool calls  │  ▲            │
 │                                      │ Plan     │              ▼  │            │
 │  Mic ─► AudioIO (half-duplex) ──PCM16 24k──────────────► GROK VOICE SESSION    │
 │  Speaker ◄── chime / speech ◄──────────────────────────  (single brain, tools) │
 │                                                              │ look(q)         │
 │                                                              └──► Perceptron   │
 │  Dashboard (browser) ◄── ws /ws/ui: scene, events, transcript, plans, state    │
 └────────────────────────────────────────────────────────────────────────────────┘
```

**Processes**
- **harness** on `:8000`: the brain, perception, world model, executor link and dashboard.
- **mockhome** on `:8100`: device state and the device/tool API.
- **executor_stub**: a WebSocket client that approves plans and calls the mockhome API.

The three components talk inside the harness through a small asyncio pub/sub bus (`bus.py`).

**Key rule:** the harness never changes devices itself. Only the executor calls the mock home. The harness watches the mock home's state stream, and every change carries its `source`: `executor:<plan_id>`, `manual` (dashboard click) or `scenario`.

---

## 3. Perception and the context model

### 3.1 Vision pipeline (cost-controlled)
- **CameraSource:** OpenCV capture at about 15 fps, kept in an in-memory ring buffer of about 10 s. Frames are never written to disk unless `--record` is set. Each camera is mapped to a room. The laptop webcam defaults to `living_room`, and a dashboard dropdown can reassign it ("the camera is now the bedroom"). Scenario replay adds file-backed cameras for other rooms.
- **ChangeDetector:** runs at 2 fps on a 160×90 grayscale copy, using a mean absolute difference plus the moving area from an MOG2 background mask. It triggers Perceptron when:
  1. the change score crosses a threshold **and** the scene then stays still for about 0.8 s (so frames aren't blurry or caught mid-motion), or
  2. a heartbeat fires every 60 s, to catch slow changes such as dusk or someone falling asleep, or
  3. there's a priority request: a `look()` call, or speech starting if the API reports it.
- **Budget:** a token bucket allows 6 calls/min per camera and 15/min globally. Priority requests skip the queue but still count. Requests go out at most 2 at a time, with backoff that honors `Retry-After`.
- **PerceptionWorker:** sends a *before* frame (asset 0) and a *settled* frame (asset 1) at 640×360, downscaled before encoding. It also sends the list of devices visible to this camera from `home.toml`, so Perceptron can name what people are looking at. It uses `reasoning_effort="none"`, a JSON-schema response, `max_completion_tokens≈1024`, and checks `finish_reason`.

The `SceneState` schema (Pydantic, exported as JSON Schema):
```json
{ "room": "living_room", "occupied": true,
  "people": [{"track_hint": "person in grey hoodie", "location": "on sofa",
              "posture": "lying|sitting|standing|walking", "activity": "sleeping|reading|watching_tv|cooking|leaving|arriving|idle|other",
              "eyes": "open|closed|unknown", "gesture": "none|pointing|shielding_eyes|waving",
              "attention_target": "lamp.living_floor | null"}],
  "lighting": "dark|dim|normal|bright", "glare_signs": false,
  "doors": [{"name": "front_door", "state": "open|closed|unknown"}],
  "new_arrival": false, "summary": "One person lying on the sofa, eyes closed, floor lamp on.",
  "confidence": 0.0 }
```

### 3.2 World model (`world/`)
- **HomeState:** a mirror of device states from the mockhome WebSocket, with `last_changed_at`, `last_source` and, for appliances, an `in_use` flag (from simulated power draw).
- **RoomContext:** for each room: occupancy (count, and since when), people with posture and activity **and how long they've been in it**, lighting, the latest `SceneState`, and the timestamp of the latest frame.
- **Event derivation:** compares each new SceneState with the previous one, applies dwell timers, and emits typed events. Dwell means an event only fires once a state has held long enough, so the system doesn't flip-flop.

| Event | Fires when | Salient (wakes the brain)? |
|---|---|---|
| `person_entered` / `person_left` | occupancy count changes | yes |
| `room_empty` | room empty for 3 min with something left on | yes |
| `lying_down` | lying for 45 s or more | yes |
| `fell_asleep` | eyes closed or activity "sleeping" for 2 min or more | yes |
| `glare_signal` | `shielding_eyes`, or `glare_signs` with lighting "bright" | yes |
| `door_left_open` | door open and no one in the room for 60 s or more | yes |
| `guest_at_entry` | `new_arrival` in entryway, or a person count above the household size | yes |
| `all_asleep` | every occupied room sleeping for 5 min, no motion on other cameras for 10 min, after 21:00 | yes |
| `appliance_idle` | appliance on and not in use for 10 min or more | only during `all_asleep` or `room_empty` |
| `lighting_changed`, `activity_changed` | — | no (context only) |

- **Transcript memory:** keeps the last 10 utterances or 5 minutes, taken from Grok transcription events.
- **Action history:** keeps plans and their results for 30 minutes. It's used for cooldowns, for context, and for spotting corrections.
- **Forgetting:** events expire after 30 minutes. Older scenes collapse into one line per room, for example "since 20:10: one person reading on sofa". Durations are written as "since" instead of repeating the same state.
- **ContextSnapshot:** a compact text block of about 600 tokens or less, with an id, covering:
  - time and part of day
  - quiet hours on or off
  - one status line per room
  - devices that aren't in their default state
  - the last 5 events and last 3 actions
  - any pending questions
  - session corrections
  - known scenes ("the usual")

  The same snapshot feeds Grok's instructions and the Perceptron `look()` prompts. Its id is recorded in every plan so decisions can be audited.

---

## 4. The brain: Grok as the single decision-maker

### 4.1 Session setup (`voice/grok_session.py`)
- Raw `websockets` client, so binary audio transport and xAI extensions are available.
- Uses `server_vad`, `grok-transcribe` with keyterms `["Fluffy", room names, device names]`, voice `eve` (configurable), and `resumption.enabled=true`.
- `instructions` = a base persona prompt plus the live ContextSnapshot, refreshed by `session.update` (debounced to at most one every 3 s) if the spike confirms this works. Otherwise the snapshot goes in as an item at the start of each turn.
- **Persona rules in the prompt:**
  - You are Fluffy and you act on instinct.
  - Never narrate your actions; prefer doing over talking.
  - Respond only to speech aimed at you (by name, or an obvious need about the home). For everything else, call `stay_silent`.
  - Every device call must include `confidence` and `reason`.
  - Ask only when it's truly ambiguous or the action is sensitive.

### 4.2 Tools exposed to Grok
- **Device tools** come from the registry (§6) and always become plan proposals. They're never run directly.
- **Meta tools** are handled inside the harness. Their results return to Grok as `function_call_output`.
  - `look(question, room?)`: a fresh Perceptron call on the latest frame. It returns a short answer plus the target device id if one matches. This is how Grok resolves "that light" or "this one".
  - `ask_user(question)`: the harness speaks the question with `force_message`. This is the only way Fluffy talks during an event-triggered turn.
  - `confirm_plan(plan_id, approved)`: resolves a plan waiting on the user's answer.
  - `stay_silent(reason)`: an explicit no-op that gets logged on the dashboard.
  - `undo_last()`: builds a plan that restores the `previous_state` values from the last result.
  - `get_home_state(room?)`: returns the full state when the snapshot isn't enough.

### 4.3 Turn types
**A. Spoken turn** (someone talks)
1. Server VAD detects the end of speech, and Grok answers on its own.
2. All device tool calls in one response are collected until `response.done` and **grouped into one plan** with `trigger.kind = "spoken"`.
3. The arbiter sends the plan to the executor and waits up to 1.5 s for a decision.
   - If it's **approved**, the harness returns `{"status":"approved","plan_id":...}`, does **not** send `response.create` (so Fluffy stays silent), and plays a chime.
   - If it's **rejected, failed, or needs confirmation**, the harness returns the reason and sends `response.create`, so Grok explains or asks in a few words.

**B. Event turn** (proactive, nothing was said)
1. A salient event passes the gates (§5 and §7).
2. The harness injects `[HOME EVENT living_room 22:41] lying_down 60s; eyes closed; floor lamp 100%, TV on (watching: no)…`, then sends `response.create` with `instructions: "Decide using tool calls only: propose device actions, ask_user, or stay_silent. Do not speak."`
3. The harness tags that response id as an event turn and **throws away its audio** as a safety net. Only `ask_user` or chimes reach the speaker.
4. Tool calls become one plan with `trigger.kind = "inferred"`.
5. Event bursts are merged: anything arriving within 2 s goes into one injected item, and at most one event turn runs at a time (others queue, and newer events replace stale ones).

### 4.4 How vague requests are resolved
| Signal | How it's resolved |
|---|---|
| "in here" | the mic's room. The mic is mapped to the webcam's room. |
| "that light", "this one" | `attention_target` from the latest SceneState if it's under 3 s old; otherwise Grok calls `look()` |
| "it's too much" while squinting | the snapshot already contains `glare_signal` and the lamp's state, so Grok fuses them into `dim_lights` |
| "the usual" | the named scenes from `home.toml` plus the most frequent past action for this room and time of day, both in the snapshot |
| "no, turn it back on" | Grok calls `undo_last()`, and the feedback module records a correction (§8) |

**Confidence gates (applied by the arbiter):**
- **Spoken:** 0.6 or higher sends the plan; below that, Grok should already have asked.
- **Inferred:** 0.8 or higher sends the plan. Between 0.55 and 0.8, the plan is held and the harness asks Grok to `ask_user` (only if someone is present and it isn't quiet hours). Below 0.55, it's dropped and shown on the dashboard as "considered".

---

## 5. Cost and latency
- **Grok connection mode** (`voice.connection`):
  - `always` (the default during demos).
  - `on_speech`: a local energy-based voice detector with a 300 ms pre-roll buffer opens or resumes the session, which closes after 2 minutes of silence. Event turns also open it. Use this during development if billing turns out to be per connected minute.
- **Event turns:** limited to 1 per room per 2 minutes, plus 6 per 10 minutes overall, so about $0.004 or less per injection.
- **Perception:** controlled by change detection, the settle wait, the 60 s heartbeat and the token bucket. Expect about 2–6 Perceptron calls per minute while there's activity and 1 per minute when idle.
- **Latency targets:**
  - Spoken command to plan proposed: **1.5 s or less**. To device state changed (with the stub executor): **2.5 s or less**.
  - A spoken command that needs `look()`: **4.5 s or less**.
  - Proactive: plan within **6 s or less after the dwell completes**.
  - Every stage is timed and shown on the dashboard.
- **Echo:** half-duplex audio. The mic is muted while Fluffy's audio plays, plus a 300 ms tail. Headphones are optional.
- **Noisy venue fallback:** a dashboard toggle switches to push-to-talk (`turn_detection: null`, a held key commits the audio). A text box sends typed input as an item plus `response.create`.

---

## 6. Tool registry (`tools/`)
Each tool is a Pydantic argument model plus metadata, declared with `@tool(...)`. Room, door, light, sound and appliance enums are **generated from `config/home.toml`**, so the model can only choose names that exist. One definition produces:
1. the Grok `tools` JSON schema (common fields `confidence: 0–1` and `reason: str` are added automatically, then removed before the plan is built)
2. arbiter validation
3. the `GET /api/tools` manifest for the executor
4. the mockhome handler that applies the tool to device state

Metadata on each tool: `sensitivity` (low, high or critical), `confirm` (never, when_inferred or always), `audible`, `reversible`, `allowed_inferred`.

| Tool | Args | Flags |
|---|---|---|
| `dim_lights` | `room`, `luminosity` (0–100, where 0 is off), `light?` | low |
| `set_light_warmth` | `room`, `warmth` (warm, neutral or cool) | low |
| `shut_door` / `open_door` | `door_name` | low / high, confirm when_inferred |
| `lock_door` | `door_name` | high, confirm when_inferred |
| `unlock_door` | `door_name` | critical, confirm always, not allowed inferred |
| `play_sound` | `sound` (rain, chime, doorbell, white_noise…), `room`, `volume` | audible |
| `stop_sound` | `room` | low |
| `set_blinds` | `room`, `position` (0–100) | low |
| `set_thermostat` | `temperature_f` (60–80), `mode?` | low |
| `set_appliance_power` | `appliance`, `on` | high. Turning an appliance off is blocked if it's `essential` (fridge) or `in_use` |
| `announce` | `text`, `room` | audible |
| `activate_scene` | `scene` (bedtime, movie, away, morning…), which expands into a multi-action plan from `home.toml` | inherits the strictest flag of its parts |

---

## 7. Plan contract (`models/plan.py`, exported to `schemas/*.schema.json`)
The executor connects to `ws://<harness>:8000/ws/executor`, sends `hello {name, version}`, and receives:

```json
{ "type": "plan.proposed", "schema_version": "1.0",
  "plan": {
    "plan_id": "pln_01JB…", "created_at": "2026-10-18T22:41:05Z", "expires_at": "…+30s",
    "trigger": { "kind": "spoken|inferred", "utterance": "ugh, it's so bright", "event_ids": ["evt_…"], "room": "living_room" },
    "intent": "Reduce glare for the person reading on the sofa",
    "actions": [ { "action_id": "act_1", "tool": "dim_lights",
                   "args": { "room": "living_room", "luminosity": 35, "light": "lamp.living_floor" },
                   "sensitivity": "low", "reversible": true } ],
    "ordering": "sequential",
    "confidence": 0.86,
    "requires_confirmation": false,
    "confirmation": { "asked": true, "answer": "yes", "via": "voice" },
    "reasoning_summary": "User said it's bright while shielding eyes toward the floor lamp (100%).",
    "evidence": [ { "kind": "speech", "text": "…", "at": "…" },
                  { "kind": "vision", "summary": "person on sofa shielding eyes", "camera": "living_room", "frame_id": "frm_…" },
                  { "kind": "state", "device_id": "lamp.living_floor", "value": { "on": true, "luminosity": 100 } } ],
    "context_snapshot_id": "ctx_…", "supersedes": null } }
```

The executor sends back:
```json
{ "type": "plan.decision", "plan_id": "…", "decision": "approved|rejected|modified|deferred", "reason": "…", "modified_actions": null }
{ "type": "plan.result",   "plan_id": "…", "status": "succeeded|partial|failed",
  "action_results": [ { "action_id": "act_1", "status": "ok|error|skipped", "error": null,
                        "previous_state": { "luminosity": 100 }, "new_state": { "luminosity": 35 } } ] }
```

The harness can also send `plan.cancel {plan_id, reason}`, for example when a plan is superseded or the user says "never mind". If no executor is connected, plans expire after 30 s and the dashboard shows "executor offline".

The stub executor (`executor_stub/`) approves anything under its policy (it rejects `critical` actions unless confirmed), calls `POST :8100/api/tools/{tool}` with `X-Plan-Id`, and reports the result. The real executor team builds against the exported JSON Schemas and `GET /api/tools`.

---

## 8. Guardrails and feedback
**Guardrails** live in `brain/arbiter.py`. They're deterministic, run before anything is sent, and only apply to inferred plans unless noted:
- validation against the registry, including enums, ranges and `allowed_inferred`
- a **5-minute cooldown** per device after any action
- **manual-override hold:** a device changed with `source=manual` in the last 15 minutes can't be touched by inferred plans
- duplicate check: an identical plan within 10 minutes is dropped
- rate limits (§5)
- **quiet hours** (22:30–07:00, configurable): no audible tools and no `ask_user` (questions are dropped; only plans at 0.9 or higher go through)
- appliance safety: never turn off anything `essential` or `in_use` (applies to **all** plans)
- confirmation for sensitive tools per the `confirm` flag. A spoken request counts as confirmed.

**Feedback** (`brain/feedback.py`):
- A correction is recorded when either of these happens within 10 minutes of an inferred action on the same device:
  - a manual reversal (`source=manual`), or
  - a spoken reversal (`undo_last`, or a spoken plan that sets the device back).
- Each correction is stored as `(event_type, tool, device)`. It suppresses that pairing for 30 minutes and raises its confidence gate by 0.1 for the session.
- Corrections appear in the snapshot, for example "User undid the auto-dim at 22:44; don't auto-dim the living room again tonight", so Grok adjusts too.
- Executor results update the action history and the evidence available for the next turn.
- **Stretch goal:** save preferences such as "reading → lamp 60%" to `data/preferences.json` and load them into the snapshot.

**Privacy**
- Frames stay in RAM only. Perceptron receives frames (cloud), which the dashboard states.
- The dashboard has a **Pause camera + mic** button and a "live" indicator.
- No face recognition: people are anonymous descriptions.
- Keys come from `.env` and never reach the browser.

---

## 9. Project layout
```
dubhacks26/
  pyproject.toml            # python>=3.11; console script `fluffy`
  .env.example              # XAI_API_KEY, PERCEPTRON_API_KEY
  config/home.toml          # rooms, devices, cameras→rooms, visible_devices, scenes, household_size, essentials
  config/fluffy.toml        # thresholds, dwell, rate limits, quiet hours, voice mode, ack style
  schemas/                  # exported plan/scene/tool JSON Schemas (for the executor team)
  src/fluffy/
    cli.py                  # `fluffy dev` (all 3 procs) | harness | home | executor-stub | replay <scenario>
    config.py  bus.py  clock.py      # clock.py lets tests freeze or advance time
    models/   scene.py plan.py events.py tool_defs.py
    tools/    registry.py home_tools.py
    perception/ camera.py change_detect.py vision.py(VisionProvider, PerceptronVision, FakeVision) prompts.py
    voice/    audio_io.py grok_session.py(VoiceProvider) fake_voice.py persona.py
    world/    model.py events.py context.py
    brain/    orchestrator.py arbiter.py feedback.py
    link/     executor_ws.py
    server/   app.py static/{index.html,app.js,style.css}   # vanilla JS dashboard, no build step
    replay/   scenario.py runner.py
  mockhome/   app.py state.py handlers.py     # FastAPI :8100, state WS, POST /api/tools/{tool}, sounds play in browser
  executor_stub/ main.py
  scenarios/<name>/scenario.toml frames/ audio/
  tests/
```
**Dependencies** (each justified):
- `perceptron`: the vision SDK
- `websockets`: the Grok realtime client
- `fastapi` + `uvicorn`: the harness and mockhome servers (Pydantic v2 comes with them)
- `httpx`: executor stub → mockhome
- `opencv-python`: capture, change detection and JPEG encoding (numpy comes with it)
- `sounddevice`: mic input and speaker output
- `python-dotenv`: loads `.env`
- dev only: `pytest`, `pytest-asyncio`

Config uses the standard library's `tomllib`.

**Run:** `uv sync && cp .env.example .env && uv run fluffy dev`, then open `http://localhost:8000`. Use `uv run fluffy dev --offline` to run without API keys, with fake providers.

---

## 10. Testing and demo
**Unit tests (offline, deterministic, frozen clock)**
- Registry: enum generation from `home.toml`, a JSON-schema snapshot for Grok tools, and rejection of out-of-range or unknown arguments.
- Plan models: round-trip serialization, exported schema snapshot, and action grouping per response.
- World model: SceneState sequences produce the right events, including dwell (no event at 30 s, event at 46 s) and `all_asleep`.
- Arbiter: each guardrail (cooldown, manual hold, quiet hours, essential appliances, confidence bands, confirmation).
- Executor link: a fake executor over a real WebSocket covering approve, reject, modify and timeout, plus `plan.cancel`.
- Feedback: a manual reversal triggers suppression.

**Scenario replay** (`fluffy replay <name> [--live]`). Each `scenario.toml` defines the initial home state, a timeline of frame folders per camera, audio clips, manual dashboard actions and expected plans:
```toml
[expect]
plans = [{ tool = "dim_lights", room = "living_room", within_s = 15, trigger = "inferred" }]
forbid = [{ tool = "unlock_door" }]
```
- **Offline mode:** `FakeVision` reads `scenes/*.json` fixtures, and `FakeVoice` replays scripted tool calls. This tests every part except the models, and it's fast and runs in CI.
- **Live mode:** sends recorded frames and audio (PCM16 streamed in real time) to the real APIs. Assertions are looser, and the run records a latency and cost report.

**Demo script: five scenes.** Each runs live, with a replay fallback one click away on the dashboard.
1. **Reading glare:** the user reads on the sofa, shields their eyes toward the lamp and says "ugh, it's bright". A spoken turn plus vision resolves which lamp, `dim_lights(living_room, 35, lamp.living_floor)` runs, and a chime plays.
2. **Winding down for bed:** "I'm heading to bed", or lying down with eyes closed for 45 s or more. Bedtime actions run: dim, shut the bedroom door, play rain at low volume.
3. **Leaving the house:** the room empties with the front door open. `shut_door` runs, then `ask_user("Lock the front door?")`, and "yes" leads to `confirm_plan` and `lock_door`.
4. **Guest arrives:** a new person appears at the entry (replay camera or webcam reassigned to `entryway`). `play_sound(doorbell)` plays and the entry lights brighten.
5. **Everyone's asleep:** all occupied rooms are sleeping and it's after 21:00. Fluffy turns off the TV, game console and space heater, which are on but not in use. It skips the fridge (`essential`) and the charging phone (`in_use`). The dashboard shows the power saved.
6. **Correction (bonus):** after scene 2, the user turns the light back on in the dashboard. A correction is logged, and Fluffy doesn't dim that room again tonight.

---

## 11. Build order (rough effort; aims for a working thin slice early)
| # | Milestone | Effort | Done when |
|---|---|---|---|
| 0 | **API spike:** a script for each provider answering the §1 unknowns; measure Perceptron latency and tokens | 2 h | spike notes written, fallbacks chosen |
| 1 | **Mock home, registry, plan contract, executor link, stub executor, dashboard;** typed text goes straight to a fake brain | 5 h | a typed request produces a plan, the stub executes it and the dashboard light changes |
| 2 | **Grok spoken path:** audio I/O, session, tools, grouping, chime and silence behavior, push-to-talk and text fallback | 5 h | "Fluffy, dim the lights" works live in 2.5 s or less |
| 3 | **Vision:** camera, change detection, PerceptionWorker, world model, snapshot into Grok, `look()` | 6 h | the dashboard shows live scene and events; "dim *that* light" works |
| 4 | **Proactive event turns, full arbiter guardrails, ask_user/confirm, quiet hours, appliance safety** | 5 h | demo scenes 2–5 trigger from vision alone |
| 5 | **Feedback, scenario replay (offline and live), recording the 5 scenes, polish** | 5 h | `pytest` passes, all scenes replay, demo rehearsed |
| — | Stretch: saved preferences, a Home Assistant backend for mockhome's tool API, an HTTP adapter for the executor link | — | — |

## 12. Verification checklist
- `uv run pytest` passes, covering unit tests and offline scenario replays.
- `uv run fluffy dev --offline`: typed commands give plan, then result, then a state change on the dashboard.
- `uv run fluffy dev` with keys: live speech and webcam for scenes 1–5. Dashboard latency readings stay within the §5 targets.
- `uv run fluffy replay all --live` gives a pass/fail table, latency and estimated cost.
- The executor team validates against `schemas/plan.schema.json` and connects to `/ws/executor` using a sample client.
