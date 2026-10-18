# Fluffy

Fluffy proposes home actions. This repository's execution path validates them, authorizes them, and applies them. The harness does not decide from a second model.

## Run

```bash
python3 -m pip install -e ".[dev]"
cp .env.example .env   # placeholders only; real keys stay out of git
python3 -m fluffy.cli dev --no-frontend
```

In another shell:

```bash
cd frontend
npm install
npm run dev
```

Open http://127.0.0.1:3000. The harness is http://127.0.0.1:8000 and the output service is http://127.0.0.1:8100.

`python3 -m fluffy.cli dev` also starts the frontend when `frontend/node_modules` is not required; it runs `npm run dev` if `package.json` is present.

A labeled developer request, which is not live perception:

```bash
python3 -m fluffy.cli call dim_lights room=living_room luminosity=35 light=lamp.living_floor
```

Export the version 2 schemas with `python3 -m fluffy.cli schemas`.

## Tests

```bash
python3 -m pytest
cd frontend && npm test && npm run typecheck && npm run build
```

Provider adapters are fake-tested unless the corresponding keys, Spotify device, and Photon sender are configured. Missing credentials stay `unavailable`. They do not return a fake success.
