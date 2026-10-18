# Fluffy

Execution subsystem for the Fluffy home assistant. It validates plans, applies fixed consent rules, and changes a mock living room. It does not infer what a person needs.

The harness listens on port 8000. The mock home listens on port 8100.

## Setup

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"
```

Run the commands below from the repository root so `config/home.toml` is found.

## Run

```bash
fluffy dev
```

That starts the mock home, the harness, and the stub executor. The same processes can be started separately:

```bash
fluffy home
fluffy harness
fluffy executor-stub
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
