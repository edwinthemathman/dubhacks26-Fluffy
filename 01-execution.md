# Part 1: Execution and Tool Calls

**Goal:** define what Fluffy can do, how proposals are written down, and how approved plans change the home. This part owns the contracts the other two parts build against. It must never make decisions. It only validates, approves under fixed rules, and applies.

**Owns**
- `config/home.toml`
- `src/fluffy/tools/`
- `src/fluffy/models/plan.py`
- `src/fluffy/link/executor_ws.py`
- `executor_stub/`
- `mockhome/`
- `schemas/`
- the `/ws/ui` state stream

---

## 1. Home config (`config/home.toml`)
A single living room for the demo. All names here become enums in the tools, so the models can only pick things that exist.
```toml
[room.living_room]
lights  = { "lamp.living_floor" = "floor lamp next to the sofa", "light.living_ceiling" = "ceiling light" }
blinds  = ["blinds.living_window"]
speaker = "speaker.living"
display = "companion.living"           # the companion screen (Part 3 renders it)

[sounds]   names = ["rain", "chime", "white_noise", "doorbell"]
[alerts]   colors = ["blue", "red", "amber", "white"]   patterns = ["pulse1", "pulse3", "strobe"]
[caregiver] name = "Alex"
```

## 2. Tool registry (`src/fluffy/tools/registry.py`)
Each tool is a Pydantic argument model plus metadata, declared with `@tool(...)`. From one definition the registry produces:
1. the function (tool) schema that Part 2 sends to Perceptron. Every device tool also gets `confidence` (0–1) and `reason` fields, which are removed before the plan is built. Perceptron can return arguments that aren't valid JSON, so the registry parses and validates them before anything else happens.
2. argument validation
3. the `GET /api/tools` manifest
4. the mock-home handler that applies it

Each tool has an **action class**, which decides whether consent is needed:

| Class | Meaning | Consent |
|---|---|---|
| `comfort` | changes the home (lights, blinds, sound) | always required, unless the person asked for it out loud |
| `notify` | temporary communication; leaves nothing changed | pre-agreed in the profile |
| `safety` | protects the person | pre-agreed in the profile |

| Tool | Args | Class |
|---|---|---|
| `dim_lights` | `room`, `luminosity` (0–100, where 0 is off), `light?` | comfort |
| `set_light_warmth` | `room`, `warmth` (warm, neutral or cool) | comfort |
| `set_blinds` | `room`, `position` (0–100) | comfort |
| `play_sound` / `stop_sound` | `sound`, `room`, `volume` (0–100) | comfort |
| `flash_lights` | `room`, `color`, `pattern`, `duration_s` (at most 10) | notify. Restores the previous light state afterward |
| `show_caption` | `text` (at most 140 characters), `size` (large or xl), `priority` | notify |
| `announce` | `text`, `room` | notify (spoken with browser text-to-speech) |
| `alert_caregiver` | `severity` (info or high), `message`, `include_snapshot` (false by default) | safety |

## 3. Plan contract (`src/fluffy/models/plan.py`, exported to `schemas/plan.schema.json`)
```json
{ "type": "plan.proposed", "schema_version": "1.0",
  "plan": {
    "plan_id": "pln_…", "created_at": "…", "expires_at": "…+30s",
    "trigger": { "kind": "spoken|inferred|sound|safety", "utterance": "…", "event": "possible_fall", "room": "living_room" },
    "action_class": "comfort|notify|safety",
    "intent": "Dim the floor lamp the user pointed at",
    "actions": [ { "action_id": "act_1", "tool": "dim_lights",
                   "args": { "room": "living_room", "luminosity": 35, "light": "lamp.living_floor" } } ],
    "confidence": 0.88,
    "consent": { "required": true, "given_by": "nod|voice|button|typing|spoken_request|profile", "at": "…", "question": "Dim the floor lamp?" },
    "profile": { "needs": ["speech"], "delivered_via": ["caption", "speech"] },
    "reasoning_summary": "…",
    "evidence": [ { "kind": "speech|vision|sound|state", "…": "…" } ] } }
```
The executor replies with `plan.decision` (`approved` or `rejected`, plus a reason), then `plan.result` (a `status` for each action, with `previous_state` and `new_state`). The harness can send `plan.cancel`. A plan expires after 30 s if it hasn't been decided.

## 4. Executor link (`src/fluffy/link/executor_ws.py`)
- The harness hosts `ws://localhost:8000/ws/executor`. The executor connects and sends `hello`, then receives plans.
- If no executor is connected, plans wait until they expire, and the UI shows "executor offline".
- Every message is also copied to `/ws/ui`, so the tool-call list (Part 3) shows *proposed, then approved, then done*.

## 5. Stub executor (`executor_stub/main.py`)
This stands in for the real executor AI. Its fixed rules:
- **reject** any `comfort` plan without `consent.given_by` (a second line of defense for "always ask first")
- **approve** `notify` and `safety` plans immediately
- for each action, call `POST localhost:8100/api/tools/{tool}` with `X-Plan-Id`, then report `plan.result`
- route `alert_caregiver` to the caregiver channel (`/ws/caregiver`)

## 6. Mock home (`mockhome/`, port 8100)
- **State:** lamp and ceiling (`luminosity`, `warmth`), blinds `position`, speaker (`sound`, `volume`), flash (`color`, `pattern`, `until`), caption (`text`, `until`), and caregiver alerts (a list with acknowledgment).
- **`POST /api/tools/{tool}`:** validates against the registry, applies the change, and records `source` (`executor:<plan_id>` or `manual`).
  - `flash_lights` saves the previous light state and restores it when the flash ends.
  - `show_caption` clears itself after 15 s, or stays until dismissed if `priority=high`.
- **`/ws/state`:** streams every change. The harness copies it to `/ws/ui`.
- **`POST /api/sensors/doorbell`:** a doorbell "button". It emits a `doorbell` sensor event as a reliable backup for the live doorbell sound in scene 3.
- **`POST /api/caregiver/ack`:** used by the caregiver view.

## 7. Developer tools
- `fluffy call dim_lights room=living_room luminosity=30 --consent=typing` sends a plan through the real link and stub, which is how milestone I1 is checked.
- `fluffy schemas` exports `plan`, `tool` and `scene` JSON Schemas to `schemas/` for the real executor team.

## 8. Tests (`tests/execution/`)
- **Registry:** enums come from `home.toml`; out-of-range, unknown or malformed arguments are rejected; the tool schema matches a saved snapshot.
- **Plan model:** round-trips cleanly and matches the exported schema.
- **Stub executor:** a comfort plan without consent is rejected; notify and safety plans are approved; results include before and after state.
- **Mock home:** a flash restores the previous state; a caption expires; a caregiver alert can be acknowledged.

## 9. Build order
1. Write `home.toml`, the registry and the plan models, and export the schemas. **These contracts are frozen first** so Parts 2 and 3 can start. (2 h)
2. Mock home state, tool handlers, `/ws/state` and the doorbell sensor endpoint. (2 h)
3. The executor WebSocket link, the stub executor and the `fluffy call` CLI. This reaches milestone **I1** together with Part 3's room UI. (2 h)
4. The caregiver channel and acknowledgment, plus the tests. (1.5 h)
