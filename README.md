# Fluffy

Execution subsystem for the Fluffy home assistant. It validates plans, applies fixed consent rules, and changes a mock living room. It does not infer what a person needs.

The harness listens on port 8000. The mock home listens on port 8100.

## Setup

```bash
uv sync --group dev
```

## Configuration

Copy nothing by hand if you only want the app to boot. The first backend start creates `.env` from `.env.example` when `.env` is missing, and it will not overwrite a file that is already there. Fill in the empty secret lines locally. Each teammate uses their own `.env`. A deployed host should set the same variable names in its secret store.

The file is read from the repository root, even if you start a command from another directory. A variable already exported in the shell wins over `.env`.

| Provider | Required for “configured” | Also required before it can work |
|---|---|---|
| Perceptron | `PERCEPTRON_API_KEY` | Optional non-secret `PERCEPTRON_MODEL` |
| ElevenLabs | `ELEVENLABS_API_KEY`, `ELEVENLABS_VOICE_ID`, `ELEVENLABS_MODEL_ID` | A successful synthesis, which is separate from configuration |
| Spotify | `SPOTIFY_CLIENT_ID`, `SPOTIFY_CLIENT_SECRET` | One browser sign-in. The refresh token is stored in `data/spotify_oauth.json` |
| Photon | `PHOTON_API_KEY`, `PHOTON_PROJECT_ID`, `PHOTON_SENDER_ID`, `CAREGIVER_DESTINATION` | Provider delivery, which is separate from configuration |

Missing provider values disable that integration and leave the site and simulated room usable. Missing Perceptron does not mark a typed request as a completed device action. Restart the harness, output service, and executor after changing `.env`.

## Run

```bash
uv run fluffy dev --no-frontend
```

That starts the output service, the harness, and the executor. They inherit the loaded environment. In another shell:

```bash
cd frontend
npm run dev
```

The same backend processes can be started separately:

```bash
uv run fluffy home
uv run fluffy harness
uv run fluffy executor
```

Streams:

- `ws://127.0.0.1:8000/ws/executor`
- `ws://127.0.0.1:8000/ws/ui`
- `ws://127.0.0.1:8000/ws/caregiver`
- `ws://127.0.0.1:8100/ws/state`

## Demonstrate

```bash
fluffy schemas
fluffy call dim_lights room=living_room luminosity=30 --consent=typing
```

`fluffy schemas` writes `schemas/plan.schema.json`, `schemas/tool.schema.json`, and `schemas/scene.schema.json`.

`fluffy call` submits one plan through the harness. The stub rejects a comfort plan that has no `--consent`. Notify and safety plans are approved without it. A rejected plan exits non-zero.

```bash
curl -s -X POST http://127.0.0.1:8100/api/sensors/doorbell
curl -s -X POST http://127.0.0.1:8100/api/caregiver/ack -H 'content-type: application/json' -d '{"alert_id":"alt_…"}'
curl -s http://127.0.0.1:8000/api/tools
```

## Test

```bash
pytest tests/execution
```

## Consent

Comfort actions require `consent.given_by` (`nod`, `voice`, `button`, `typing`, `spoken_request`, or `profile`). Notify and safety actions are pre-agreed and run immediately. A plan that is still undecided expires 30 seconds after `created_at`. While no executor is connected, the UI stream reports `executor offline`.
