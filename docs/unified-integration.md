# Unified integration

Branch `integration/fluffy-unified`, based on `cursor/fluffy-frontend-room-6ed8` at `c5ab2e66aa208d6b3dca11fb81b50bc620a98ea7`.

| Source | SHA |
|---|---|
| Frontend base | `c5ab2e66aa208d6b3dca11fb81b50bc620a98ea7` |
| Execution subsystem | `2ad1149421a99625f530af724a793bace0d1a4f9` |
| Main | `836062e1df76c9e6bc5d0b54cc5cf3371bced3c4` |

Not merged to main. Not pushed.

## Ownership

- The harness (`python -m fluffy.server.app`, port 8000) owns the session, inference entry, and ordered UI events.
- The executor (`python -m executor_stub.main`) is the only process that applies device actions.
- The output service (`python -m mockhome.app`, port 8100) owns home state and provider calls.
- The Next provider owns the camera, the UI socket, and playback. Browser speech recognition is not ported yet.
- `fluffy-live` no longer binds port 8000. Main's `run_tool` is not on the live path. The old helper remains in `src/fluffy/integrations/legacy_dispatch.py` for the offline device tests.
- Main's safety machine is `fluffy.brain.safety_legacy`. The v2 incident manager is the harness safety machine.
- Main plan types are `fluffy.models.inference`. V2 plans are `fluffy.models.contracts`.

## Start

From this worktree:

```bash
uv sync --group dev
uv run fluffy dev --no-frontend
```

In another shell:

```bash
cd frontend
npm ci
npm run dev
```

Harness `http://127.0.0.1:8000`. Output service `http://127.0.0.1:8100`. Frontend `http://127.0.0.1:3000`. Copy `.env.example` to `.env` and fill keys locally. Do not commit `.env`.

`PERCEPTRON_API_KEY` is optional for boot. Without it, `POST /api/input/text` accepts the words and then reports that Perceptron is not configured. It does not invent a device action.

## Routes added on the harness

- `GET /api/bootstrap` — session, event sequence, open questions, and the output-service snapshot.
- `POST /api/input/text` — `{session_id, request_id, text}`. Returns 202 when accepted. Interpretation is asynchronous.
- `POST /api/input/transcript` — same body plus `final: true`. Not an automatic authorization.
- `POST /api/session/stop` — marks the session stopped and pauses listening. It does not yet cancel an in-flight plan.
- `POST /api/questions/{id}/answer` expects `{answer, source: "button"|"model"}`.
- `POST /api/audio/ack` expects `started`, `ended`, `failed`, or `blocked`, plus `client_id`.

A model-supplied trigger kind of `speech` on `POST /api/plans/submit` is not authority. Typed text sets that flag in the server bridge after interpretation. Developer requests stay on `POST /api/dev/request`.

## What was verified

- `uv run pytest`: 169 passed.
- `tests/execution/test_e2e.py` includes a typed "dim the floor lamp to 30" request through `POST /api/input/text`, a test interpreter, the executor, and a fresh `GET /api/state` at luminosity 30. The test interpreter is a stand-in for Perceptron. No live Perceptron call was made.
- Frontend: `npm run typecheck`, `npm test` (23), `npm run lint`, `npm run build`.

## Not done

- No live Perceptron, ElevenLabs, Spotify, or Photon call has been verified. Missing credentials must stay visible failures.
- The live loop still builds its old plan and does not submit that plan to `Runtime`. Device effects from camera cues are not applied. Scene `said` is cleared and is not treated as speech. Audio answers and Perceptron transcripts are not used; browser text has to come through `/api/input/transcript` once recognition is ported.
- `play_music`, `stop_music`, `send_text`, `open_door`, `close_door`, and `call_person` are not on the v2 registry. The old helpers only serve `tests/integrations/test_devices.py`.
- Browser `SpeechRecognition`, frame upload, environmental PCM upload, profile switching, and routine execution through the executor are not wired.
- Stop does not revoke authorization or drop a late model result.
- Baseline screenshots of `/` and `/room` were not captured before these edits. Visual files (`globals.css`, brand, shell, sidebar, room geometry) were not redesigned. A side-by-side visual check is still required.
- V1 execution tests (`test_mockhome.py`, `test_plan.py`, `test_registry.py`, `test_stub.py`) were removed because they imported the replaced harness. The v2 suite in `tests/execution/` is the replacement.
