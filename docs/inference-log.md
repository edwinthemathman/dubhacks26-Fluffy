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

## Not started

- The Perceptron spike: latency, how often tool arguments parse, atypical-speech transcription, nods, sound labels, OCR, on-floor detection.
- A real Perceptron client. Decision calls go through `DecisionProvider`.
- Nod answer-mode (1.5 s bursts) and the sound-label gate.
- Wiring plans to the executor WebSocket. This package only builds plans.
- Scene change detection. That trigger sits on Part 3's camera.

## Open questions

1. Part 1 does not exist in code yet. This branch has its own copy of the plan model and tool catalog. Should those be deleted once the execution branch lands, or kept as the import inference uses?
2. Is there a `PERCEPTRON_API_KEY` I should use for the spike, and is that the next step?
3. Are the silence, distress, darkness, and "no means help" choices above the ones you want?
4. The staged README deletion on `main` was restored here. Should that file stay deleted?
5. Say when this slice should be committed.
