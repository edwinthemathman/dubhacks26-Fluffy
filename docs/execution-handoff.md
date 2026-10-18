# Execution handoff

Schema version is `2.0`. A `1.0` plan is rejected with `unsupported_schema_version`.

## What is live-tested

| Provider | Status |
|---|---|
| Simulated lights, blinds, flash, captions | Tested through the harness, executor, and output service |
| ElevenLabs | Fake-tested only. No API key in this environment |
| Spotify | Fake-tested only. No client credentials or device |
| Photon 2.0 | Fake-tested only. No project, sender, or destination |

## Startup

```bash
python3 -m pip install -e ".[dev]"
python3 -m fluffy.cli dev --no-frontend
cd frontend && npm install && npm run dev
```

Ports: harness `8000`, output service `8100`, frontend `3000`. Spotify's redirect URI must be exactly `http://127.0.0.1:8100/api/integrations/spotify/callback`.

## Inference interface

Submit `POST /api/plans/submit` with `schema_version: "2.0"`. Do not send `authorization_id` or `confidence`. Record observations first with `POST /api/observations` (`source` is `live` or `fixture`). Tool arguments are validated by `GET /api/tools`. Disabled tools are omitted from use until that provider is at least configured; `set_volume` stays disabled until Spotify is `ready`.

`POST /api/incidents/possible-fall` opens a check-in only when `floor_at` is at least two seconds after `descent_at`. `POST /api/dev/incidents/possible-fall` is the labeled developer trigger. `POST /api/help` opens an incident and makes one alert eligible.

Questions are `question.opened` events. Button answers are `POST /api/questions/{id}/answer` with `source: "button"`. A model answer uses `source: "model"` and fresh speech evidence ids. `"I'm not okay"` does not count as okay.

An eligible caregiver alert is an `alert_caregiver` action whose `incident_id` is in phase `alert_pending`. The executor claims it immediately before send. Five-of-five evidence cannot send a message by itself.

Playback acknowledgements are `POST /api/audio/ack` with `message_id`, `session_id`, `client_id`, and `started`, `ended`, `failed`, or `blocked`. Generation is not playback success.

## Frontend events

`/ws/ui` messages are `{event_id, session_id, sequence, timestamp, type, payload}`. Home state arrives as `home.snapshot` and is also available from `GET /api/state`. Apply a snapshot only when `revision` is newer. Do not play `audio_url` values that were already present on the first snapshot after connect.

Activity status comes from `plan.proposed`, `action.started`, and `action.result`. `verification: provider_accepted` is shown as Submitted. `provider_delivered` is Delivered. `unknown` and `unverified` are not success.

## Spotify setup

1. Create an app in the Spotify developer dashboard and copy the client id and secret into the environment, not into git.
2. Register the redirect URI above exactly. Spotify requires the `127.0.0.1` loopback host.
3. Open Connect Spotify, approve `user-modify-playback-state` and `user-read-playback-state`, then choose the laptop device. Fluffy will not switch devices on its own.
4. Set `SPOTIFY_ARRIVAL_TRACK_URI` to a `spotify:track:` URI. The placeholder in config is not a playable track.

## Photon setup

Send uses `POST https://api.photon.codes/v1/projects/{project_id}/messages` with `idempotency-key`, `senderId`, and `spaceId`. Delivery lookup is `GET /v1/projects/{project_id}/messages/{messageId}`. Configure `PHOTON_API_KEY`, `PHOTON_PROJECT_ID`, `PHOTON_SENDER_ID`, and `CAREGIVER_DESTINATION` (a space id). The model never supplies a phone number. A 202 with status `dispatched` or `accepted` stays Submitted until a later read says `delivered`.

## ElevenLabs setup

`POST https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream` with `xi-api-key` and `model_id`. Set `ELEVENLABS_API_KEY`, `ELEVENLABS_VOICE_ID`, and `ELEVENLABS_MODEL_ID`. `announce` creates one caption and, when synthesis works, one clip. `show_caption` never calls ElevenLabs.
