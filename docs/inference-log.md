# Inference work log

Branch: `inference`, cut from `main` at `86cfce4` in `Fluffy-DubHacks-`.
Nothing is committed yet.

## What was already in the repo

The git repo held the plan only: `01-execution.md`, `02-inference.md`, `03-demo.md`, and `reference-architecture.md`. There was no Python package, no Part 1 registry, and no Part 3 sensors.

`main` had a staged deletion of `README.md`. That file was only the title line `# Fluffy-DubHacks-`. I restored it before creating this branch so the inference work would not carry an unrelated deletion.

## What this slice is

Part 2's deterministic core, tested offline with a frozen clock and a scripted Perceptron stand-in. The live API spike has not been run.

`uv run pytest` — 45 passed.

### Layout

| Path | Role |
|---|---|
| `config/profiles/{blind,deaf,speech_cognitive}.toml` | How Fluffy speaks and listens |
| `src/fluffy/profile/` | Profile loader |
| `src/fluffy/perception/scene.py` | `SceneState` and its JSON schema |
| `src/fluffy/perception/utterance.py` | Energy gate: 300 ms pre-roll, clips capped at 15 s |
| `src/fluffy/perception/provider.py` | Decision-call interface plus `ScriptedProvider` |
| `src/fluffy/world/model.py` | Object memory, dwell events, context snapshot |
| `src/fluffy/brain/decide.py` | Tool loop, at most 3 rounds, one plan from the last round |
| `src/fluffy/brain/consent.py` | Questions, answers, silence |
| `src/fluffy/brain/safety.py` | Fall and distress state machine |
| `src/fluffy/brain/arbiter.py` | Consent, 0.7 confidence, offer rate, declined cooldown |
| `src/fluffy/models/` | Plan model and the tool catalog copied from `01-execution.md` |
| `tests/inference/` | The offline tests from section 8 of the inference plan |

Run with `uv sync` then `uv run pytest` (Python 3.12).

## Decisions I made while the plan was silent

- A comfort plan is released only when `consent.given_by` is set. Spoken requests count as consent for blind and deaf when the transcript is clear and confidence is at least 0.7. The speech/cognitive profile always gets a confirm-back.
- Silence on a consent question is a no. Blind and deaf wait 30 seconds. Speech/cognitive repeats once at 20 seconds, then treats silence as no 20 seconds after that.
- "Are you okay?" treats "no" as needing help.
- Distress phrases are "I fell", "I can't get up", "I need help", and a short "help" / "help me". "Help me find my keys" is not a distress call.
- `getting_dark` is lighting `dark` held for 10 seconds. `dim` does not fire it. `glare_signal` is `shielding_eyes` held for 10 seconds.
- Only device calls from the final tool-loop round become the plan. Earlier device calls are dropped. If one response mixes classes, the plan's class is the strictest: comfort, then safety, then notify.
- A second comfort request while a speech/cognitive question is open is ignored, not queued.
- Utterances end after 400 ms of silence. That hang time is not in the plan.
- Object memory is fresh for 15 minutes, then stale. Stale and unknown objects are for a later live `find_object` call.
- Profiles use the demo names Sam and Alex, and a starter vocabulary list.
- `given_by` includes `pointing`, and plans carry `context_snapshot_id`. Neither field is in the Part 1 schema yet.

## Demo

`./scripts/demo` serves http://127.0.0.1:8091. Upload an mp4, webm, or mov. The server uploads it to Perceptron, reads the scene, then runs a decision call. The key stays in `.env`.

## Prompt pass: vision first, fewer missed actions

Live clips mostly produced no action when one was needed, for example a fall with groaning but no "help". Changes:

- **The decision call always runs.** The demo used to skip it whenever the structured scene read found no cue. Any miss in the scene read became a silent "quiet", which was the biggest source of missed actions.
- **The decision prompt is rewritten** (`brain/decide.py`):
  - watch first, then use sound as backup, then treat words as one more cue
  - a fall or a person who looks hurt calls `alert_caregiver` even with no words
  - a table of situations and actions, with the brightness ranges the cue matcher expects
  - **tool calls are proposals, because the app asks for consent.** The old "never change the home without consent" line made the model hold back.
- **The scene read follows the clip over time.** A `timeline` field is written first, plus `fell`, `distress_signs` and a `fallen` activity. Reasoning is raised from `none` to `low`.
- **New sound labels:** `pain` (groan, cry, "ow", no words needed) and `impact` (thud, crash).
- **Fall cues** (`brain/cues.py`) fire on any of:
  - the person fell
  - on the floor
  - lying off furniture with pain, an impact sound or distress signs
  - visibly hurt and making a pain sound
- **Tool descriptions** now say when to use each tool, and `confidence` and `reason` have guidance.

## Still open

- Nod answer-mode and the sound-label gate.
- Wiring approved plans to the executor WebSocket.
- Scene change detection on the live camera.


## Open questions

1. Part 1 does not exist in code yet. This branch has its own copy of the plan model and tool catalog. Should those be deleted once the execution branch lands, or kept as the import inference uses?
2. Is there a `PERCEPTRON_API_KEY` I should use for the spike, and is that the next step?
3. Are the silence, distress, darkness, and "no means help" choices above the ones you want?
4. The staged README deletion on `main` was restored here. Should that file stay deleted?
5. Say when this slice should be committed.
