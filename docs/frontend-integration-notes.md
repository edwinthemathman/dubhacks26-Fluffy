# Frontend integration notes

## Sources

| Source | Commit | What was taken |
|---|---|---|
| `cursor/execution-tool-calls-1eb6` | `bfae21969400280294e3ffe6fd7e4805fb9a1a35` | Next.js app in `frontend/`, harness, mock home, execution contracts |
| `room` | `79bdd8a57b29b486b04e80cb1347adb8a6f9688d` | `room3d/src/` and `room3d/test/reducer.test.ts` as reference; `web/src/live/pcm-worklet.js` and the capture/voice hooks as reference |
| `thinking` | `f5ab29e7fb7f58bd09369f8760cbf3bd7ba95e99` | Inspected. Not merged. It is a separate Vite app. |

No license file was attached to `room3d/`. The imported scene keeps its geometry and is rethemed for the white app.

Imported from `room3d/src/` into `frontend/features/room/` (same relative module layout):

- `FluffyRoom3D.tsx`, `announce.ts`, `events.ts`, `index.ts`, `reducer.ts`, `state.ts`, `toolMap.ts`, `useRoomFromEvents.ts`
- `scene/AskBubble.tsx`, `Door.tsx`, `FlashOverlay.tsx`, `Light.tsx`, `Phone.tsx`, `Room.tsx`, `Speaker.tsx`, `Stage.tsx`, `motion.ts`, `theme.ts`
- Added `scene/Blinds.tsx` and `projectHomeToRoom.ts`

`useRoomFromEvents` no longer opens a socket. The session provider owns the only UI connection. `events.ts` / `reducer.ts` in the room folder are stubs so a second event stream cannot drive the scene.

Worklet copied from `web/src/live/pcm-worklet.js` to `frontend/public/audio/pcm-worklet.js` and loaded as `/audio/pcm-worklet.js`.

Three.js packages match the donor lockfile range: `three@0.180.0`, `@react-three/fiber@9.3.0`, `@react-three/drei@10.7.6`, `@types/three@0.180.0`. React stays on the app's existing `19.2.8`. Next.js stays on `16.3.0`.

## Contract

Default mode is `execution-v2`.

| Setting | Default |
|---|---|
| `NEXT_PUBLIC_FLUFFY_HTTP` | `http://127.0.0.1:8000` |
| `NEXT_PUBLIC_FLUFFY_CONTRACT` | `execution-v2` |
| WebSocket | derived, `ws://127.0.0.1:8000/ws/ui` |

`legacy-live` is explicit only. It sends `{ "type": "typed", "text" }` on `/ws/ui` and JPEG/PCM on `/ws/live`. The app never tries both transports.

### execution-v2 routes this UI calls

| Request | Meaning |
|---|---|
| `GET /api/health` | Harness reachable. Implemented on the current branch. |
| `GET /api/tools` | Tool manifest. Implemented. |
| `POST /api/input/text` `{ session_id, request_id, text }` | Accept text. **Not implemented** on this harness. A 404 is shown as a missing route. A 2xx only means the input was accepted. |
| `POST /api/questions/{id}/answer` `{ session_id, choice, text }` | Answer a pending question. **Not implemented.** |
| `POST /api/session/stop` `{ session_id }` | Cancel pending automation. **Not implemented.** Local devices are still released. |
| `POST /api/media/frames` `{ session_id, epoch, ts_ms, motion, jpeg_base64 }` | One JPEG batch. **Not implemented.** Preview stays local and the card says the route is unavailable. |
| `POST /api/media/audio` `{ session_id, epoch, pcm16_base64 }` | PCM16 audio. **Not implemented.** Not sent once frames report 404. |
| `POST /api/speech/status` `{ session_id, message_id, status }` | Playback acknowledgement. **Not implemented.** Local playback failure is not reported as success. |
| `WS /ws/ui` | Plan, executor, and home events. Implemented. |

The harness now allows browser origins from `FLUFFY_UI_ORIGINS` (default `http://localhost:3000,http://127.0.0.1:3000`) on HTTP routes. That does not change plan validation.

## UI

- `/` is the white chatbot: title, prompt, camera, then thinking and execution panels.
- `/room` lazy-loads one WebGL canvas (`next/dynamic`, `ssr: false` from a client page) and a text status card.
- Sidebar is 232px, `#FAFAFA`, border `#E5E5E5`. Links are Fluffy and Room. Below 768px the sidebar becomes a dialog drawer (Escape, focus trap, focus returns to Menu).
- The font stack is still `Inter, ui-sans-serif, system-ui, …`. No font file is loaded; Inter is only a family name.
- Recorded demo is opt-in. It uses `frontend/lib/scenes.ts` and never writes those results into the live home. A failed connection does not turn it on.
- Brightness uses 0–100. `1` is 1%. The floor lamp and ceiling light are separate. Blinds use 0 closed / 100 open.
- `tool.status=done` without provider evidence stays unknown. Spotify is not shown as playing without confirmation. Photon acceptance ("Message submitted") is separate from delivery.

## Startup

```bash
# harness, from the repo root, when you want live plan/home events
uv run fluffy
# optional mock home and stub
uv run fluffy-home
uv run fluffy-stub

# UI
cd frontend
npm install
npm run dev
```

Open `http://127.0.0.1:3000`. Press Start before the camera is requested.

## Verification

Ran from `frontend/`:

- `npm test` — 22 vitest cases (normalization, reducer, room projection).
- `npx tsc --noEmit` and `npx eslint` on the app, components, features, lib, providers, and tests.
- `npx next build` — `/` and `/room` compiled.

Browser, against `next dev` with no harness running:

- 1440px and 1024px: 232px sidebar, no horizontal overflow, prompt then camera, backend-offline notice distinct from “Camera off”.
- 390px: sidebar hidden, Menu opens a modal drawer, Escape closes it, no horizontal overflow.
- Client navigation Fluffy → Room → Fluffy kept the unsent draft. A full reload does not.
- Recorded demo of “ugh, it's bright” showed the recorded scene and did not claim a live home change.
- `/room` drew one canvas (about 480px tall at 1440px) and labeled lights, blinds, and speaker Unknown before any snapshot.
- Computed brand font is the existing stack (`Inter, ui-sans-serif, system-ui, …`) at 26px. No font file is loaded.
- Live camera, microphone, and provider playback were not exercised. This environment has no camera, and the harness was not running. The UI socket stayed on “Reconnecting” and the page said the connection could not reach the backend.
