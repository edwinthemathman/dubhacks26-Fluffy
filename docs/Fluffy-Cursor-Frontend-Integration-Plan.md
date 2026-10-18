# Cursor instructions: Fluffy frontend and room integration

## Task

Implement this plan in a **new integration branch** of `https://github.com/kpockmain/Fluffy-DubHacks-.git`. Preserve the white, minimal Next.js frontend from `cursor/execution-tool-calls-1eb6`: its working prompting bar, Fluffy typography, animated thinking labels, and execution panels. Add a persistent left sidebar with **Fluffy** and **Room**, a live camera preview directly below the prompting bar, and the existing 3D room from the `room` branch.

Both pages must reflect one shared Fluffy session. Display concise observations, processing status, questions, speech captions, and actual execution outcomes. Keep the existing aesthetic while connecting it to real backend events.

Use the attached `01-execution.md` / `execution.md` for tool behavior and verification. Use `04-integration-plan.md` for broader integration. This document supersedes their earlier single-page/SVG layout: the main page remains the chatbot and the dedicated Room page uses the imported 3D scene. Do not change execution policy as a side effect of changing the frontend.

These are implementation instructions. Inspect the repository, implement incrementally, and verify the result. Do not stop after returning another plan. This request authorizes a new local branch and code changes, but not a remote push or deployment.

## 1. Verified source branches and merge strategy

At inspection on October 17, 2026:

| Source | Observed commit | Relevant contents |
|---|---|---|
| `cursor/execution-tool-calls-1eb6` | `bfae219` | White Next.js app in `frontend/`; execution harness and output service |
| `room` | `79bdd8a` | Reusable `room3d/src/` package; another Vite app in `web/`; newer inference/live/voice code |
| `thinking` | `f5ab29e` | Blue-and-white Vite app and an existing live event/capture path |

Refresh the remote refs and record the commits you actually use. The `room` branch is not simply an alternative `frontend/` page. Its repository tree differs extensively from the Cursor branch. Use a **selective source integration** of the room component into the chosen Next.js app. A whole-branch merge would also introduce competing applications and backend contracts; that is outside this frontend task.

First inspect `git status`, local branches, and all relevant `AGENTS.md` files. Preserve all existing work. Create an isolated worktree from the Cursor branch so another teammate can keep working in their original checkout. Example commands, from the existing repository:

```bash
git fetch origin refs/heads/cursor/execution-tool-calls-1eb6:refs/remotes/origin/cursor/execution-tool-calls-1eb6 refs/heads/room:refs/remotes/origin/room
git worktree add -b integration/fluffy-frontend-room ../Fluffy-frontend-room origin/cursor/execution-tool-calls-1eb6
```

If that branch/path already exists, inspect and reuse the correct task worktree or choose a fresh suffix; do not reset it. Record the source SHAs in `docs/frontend-integration-notes.md`. If relevant newer local frontend work exists, preserve it and identify how it enters this branch instead of overwriting it. Put the supplied specifications in the worktree if they are not already committed; the starting branch may still contain older documentation.

Read `frontend/AGENTS.md` and the relevant documentation for its installed Next.js version before editing. Do not upgrade Next.js or React simply to carry out this integration.

### Import only the room source

Export `room3d/src/` and `room3d/test/` from the recorded room commit into a temporary directory. Copy the source into `frontend/features/room/` and adapt its tests into `frontend/tests/room/`. Keep its relative internal module structure. Record original paths and commit for traceability. Inspect any license files that accompany the source and preserve applicable notices.

Use the existing `frontend/package.json` as the application manifest. Add the compatible Three.js dependencies used by the room package: `three`, `@react-three/fiber`, `@react-three/drei`, and development types as needed. Inspect the donor lockfile to select compatible versions and update the frontend lockfile normally. Keep one React runtime. Do not copy the donor package manifest, Vite entry point, global theme, or standalone playground over the Next.js app.

The room branch's `web/src/live/` hooks are reference implementations to adapt where useful. Importing them does not establish compatibility with the Cursor backend. Handle that explicitly in sections 5–7.

**Gate:** the new worktree runs the original white frontend, and the imported room source type-checks after dependency integration.

## 2. Visual specification

Use the supplied ElevenLabs screenshot as a reference for sidebar placement, spacing, and hierarchy. Use Fluffy's own typography and existing restrained black/white design throughout.

| Element | Specification |
|---|---|
| Sidebar | 232 px wide on desktop, full viewport height, background `#FAFAFA`, right border `#E5E5E5`, 20 px inner padding |
| Brand | “Fluffy” at top left; reuse existing `font-semibold tracking-tighter` styling and the actual computed font; approximately 26 px |
| Navigation | Two primary links: Fluffy with a chat icon, Room with a home/cube icon; 40–44 px row height; 18 px outline icons; 10–12 px corners |
| Active row | Neutral light-gray fill, dark label/icon; use `aria-current="page"` |
| Inactive row | Muted gray, subtle hover fill; visible keyboard focus |
| Sidebar footer | Actual connection status, sensing status, and compact Start/Pause control; no invented account menu or unrelated navigation |
| Main canvas | White; desktop left offset equal to sidebar width; 24–32 px padding; content vertically scrolls |
| Chat content | Centered within the remaining main width; maximum 768 px for prompt and video; execution panels can expand to 896 px |
| Surfaces | Existing white cards, thin neutral borders, rounded corners; retain `NotchedPanel`, `AutoHeight`, and restrained entrance animations |
| Room page | Same sidebar and font; neutral panel around the 3D scene; compact state/action details beside or below it |

The current CSS lists `Inter` first but does not visibly load it through `next/font` in the inspected layout. Check the browser's computed font before changing anything. Reuse the existing stack and weight so the brand matches the working frontend; do not claim that a font file is loaded when it is only a fallback name.

On narrow screens below approximately 768 px, use a compact top bar and an accessible navigation drawer. Keep touch targets at least 44 px, trap focus while the drawer is open, close on Escape, and return focus to its trigger. Do not keep a fixed 232 px sidebar that squeezes out the camera.

Keep `BackgroundLines` and `FlowLines` where they support the existing design. Restrict decorative positioning to the main content, not the whole viewport, so lines and the pinned title never cover the sidebar, prompt, or camera. Recalculate geometry on main-container resize. Respect reduced motion.

## 3. Routes and persistent ownership

Create these routes:

- `/`: Fluffy chatbot, prompting bar, video, thinking and execution panels.
- `/room`: full room visualization, actual device state, latest relevant action/incident status.

Mount a client-side `FluffySessionProvider` under the shared root layout, above both routes. Put `AppShell`, persistent capture ownership, and a single audio controller at this level. Use Next navigation so route changes preserve the shared layout/provider. Lazy-load the WebGL scene within a client component; check installed Next docs for `next/dynamic` and client-only rendering. [R1–R2]

The provider owns:

- One UI event connection and its reconnect/backoff lifecycle.
- Canonical home state, current turns/messages, active question and incident, and per-action statuses.
- Session and message IDs, event deduplication and snapshot ordering.
- One camera/microphone stream, media transport, capture status and permissions.
- One speaker playback queue and already-played message IDs.
- The composer draft, so switching to Room and back does not erase unsent text.

Room rendering is a projection of shared state. It never owns an independent backend connection, capture stream, or audio player. It may unmount when leaving the page; canonical state remains in the provider.

## 4. Preserve the chatbot and make the labels truthful

The main page order is fixed:

1. Existing Fluffy welcome/title treatment, adapted to the new main-column width.
2. Working prompting bar and Send control.
3. Live video card immediately below the complete prompting bar/form controls.
4. Thinking/observation panel, retaining the shimmer and transition style.
5. Execution panels and recent results, using actual backend events.

Keep Enter to submit and Shift+Enter for a new line. Do not submit Enter while an IME composition is active. Keep the draft if submission fails. Disable only the operation that is unavailable; an active ordinary turn must not prevent the user from answering a safety question or stopping Fluffy. Correlate submissions and replies by request/turn ID.

Replace `matchScene`, `PROPOSE_MS`, and canned completion delays in live mode. Keep `frontend/lib/scenes.ts` only behind a clearly labeled Recorded demo mode that the user explicitly selects. Connection failure must not automatically switch to success-returning fixtures.

Adapt `ThinkingLabel` from rotating arbitrary phrases to displaying the current known stage with its existing shimmer. Animation timing may drive fades, but it must not advance execution state. Handle empty labels and rapid turn changes without showing stale Done text.

| Backend fact | Main-page wording |
|---|---|
| Session active and perception available | “Watching and listening” |
| Local preview available, inference unavailable | “Camera preview only” |
| Observation request in progress | “Checking the room” |
| Text request received and inference running | “Considering your request” |
| Plan created, not authorized/executed | “Proposed action” |
| Explicit pending question | Display that question and usable response controls |
| Executor says action started | “Adjusting the lights” / “Starting your music,” based on that tool |
| Verified local state change | “Lights set to 35%” |
| Spotify playback confirmed | “Music is playing” |
| Photon accepted but delivery unconfirmed | “Message submitted” |
| Provider delivery confirmed | “Message delivered” |
| Unknown/failed outcome | “Could not verify playback” / specific failure, with retry only when safe |

“Thinking” means short observations, stage labels, and user-facing explanations supplied by the backend. Do not fabricate reasoning or expose raw internal model traces. The room branch contains an older document proposing raw reasoning display; follow the current integration specification instead.

Preserve Intent, Actions, Evidence, and JSON panels if useful, with JSON tucked into details. Refactor `PlanBody` and `HomeOutcome` to accept typed live view models instead of the fixture `Scene` type. A local `setConfirmed(true)` must no longer execute or display a successful action: send the answer to the backend and await its response.

Allow audio/video-triggered turns to populate the same panels without a text submission. Keep recent turns in session memory, with a bounded list and timestamps; this is not long-term routine learning. Avoid forced scrolling while the user is inspecting earlier results.

## 5. One event adapter between backend and UI

The inspected branches have incompatible event shapes. Build `frontend/lib/fluffy/normalizeEvent.ts` and test it. Do not scatter compatibility conditions across components.

| Inspected room/live event | Canonical UI meaning |
|---|---|
| `turn.start`, `turn.end` | Turn lifecycle |
| `think`, `sense.see`, `sense.hear` | Concise observation/status/transcript where appropriate |
| `say` with `say_id` | Captioned output message; audio only when the event permits it |
| `ask`, `ask.answer` | Pending question and resolution |
| `tool.call`, `tool.status` | Proposed action and reported status; not automatically proof of external execution |
| `state` | Legacy simulated-home snapshot |
| Version 2 `plan.*`, `action.*`, `home.state`, `output.message`, `speech.status`, `incident.updated` | The corresponding execution-spec state and lifecycle |

Inspect whichever backend is actually running. Configure one explicit contract mode (`execution-v2` or an intentionally supported legacy mode) and one backend base URL. Do not broadcast requests to both backends or guess a transport from a failed request. Current code in the Cursor branch has execution endpoints but lacks the full inference/media routes; show capability availability accurately.

For version 2, use `POST /api/input/text` and the question routes defined in the integration plan when available. The donor live server instead accepts `{"type":"typed","text":"..."}` on `/ws/ui`; its UI stream supports different question messages. Translate only when that legacy server is deliberately selected and document unsupported features. A label of `tool.status=done` without provider evidence cannot become “Spotify playing” or “Message delivered.”

Validate event shape, IDs and finite numbers at the boundary. Scope deduplication and ordering to a server/session instance. On reconnect replace state from a fresh snapshot before applying newer events. If the server restarts its sequence, establish a new stream epoch instead of ignoring all its messages as old. Ignore old home revisions and avoid replaying historical effects or speech.

Expose missing endpoint/capability information to the backend owner with exact request and response types. This frontend task may add a small bridge to an existing server when needed, but must not invent successful inference or rebuild the whole execution subsystem. A successful HTTP submission means accepted input; an action result is still required.

## 6. Live video below the prompting bar

Create `CameraPreview.tsx` with a 16:9 visual container, 16–18 px corners, a thin neutral border and `object-fit: contain` so the scene edges remain visible. Use the actual stream in `<video autoPlay muted playsInline>`. Keep video unmirrored by default so model-relative directions and any future overlays match the preview.

The card header shows three independent facts: Camera on/off, whether media is reaching the backend, and Last analyzed time. Never infer that Perceptron is seeing the scene solely because the webcam preview works. Show permission denied, camera absent, transport offline, and processing delayed as different states.

Start capture only after the user's Start Fluffy/camera action. Allow camera-only preview if microphone permission is denied, and retain text input. Camera off stops its track; muting the microphone disables audio input while leaving video running. Stop Fluffy releases tracks, AudioContext, timers, and media connections and cancels pending automation through the backend where supported.

Important donor-hook change: `web/src/live/useLiveCapture.ts` draws frames from a route-owned `videoRef` and stops on unmount. That would stop sampling when navigating to Room. Adapt it so the provider owns capture and a persistent `CaptureHost` video element or equivalent frame source. The visible chat video simply attaches the shared MediaStream. Detaching that preview must not stop its tracks.

The room branch sends JPEG frames and PCM audio over `/ws/live`; the original integration plan proposes HTTP media batches. Choose the transport supported by the active backend and adapt it once. A media socket is separate from the one UI-event socket; “one UI socket” does not forbid a distinct media connection. Do not send both formats simultaneously.

If reusing the donor AudioWorklet, inspect its resampling and framing against the server contract. Serve its adapted script from `frontend/public/audio/pcm-worklet.js` and load it via an explicit asset URL; don't copy a Vite-relative import without checking Next build behavior. Bound audio/frame buffering, recheck socket/session state after asynchronous JPEG encoding, and cancel stale callbacks after Stop.

Maintain a speech-active flag in the session owner; suppress microphone forwarding during Fluffy speech and the specified 300 ms tail. Show permission/audio failures honestly. Keep camera sampling and output status independent of route navigation.

## 7. Import and correct the room component

Render `FluffyRoom3D` on `/room` in a client-only wrapper with a fixed/minimum height (approximately 480 px desktop, 320 px mobile). Keep its demand-rendering approach, use a moderate pixel-ratio cap, and mount only one WebGL canvas. Provide a readable device-state card if WebGL is unavailable or the canvas fails.

Retheme its light palette in `scene/theme.ts` to match the white app: off-white canvas, warm neutral walls/floor, dark-gray outlines, restrained accents for actual light state and alerts. Keep the model geometry and visual personality. Do not import `web/src/theme.css` or its Nunito/blue global theme.

Create a pure `projectHomeToRoom()` selector. It maps canonical backend state to the renderer's state. Render a waiting/unknown state before the first snapshot; the donor `initialRoom()` values must not masquerade as actual device state.

Required compatibility fixes found in the donor:

| Finding | Required change |
|---|---|
| `asLevel()` treats numbers in `(0,1]` as normalized fractions | Use the execution contract's 0–100 scale. A value of 1 means 1%, not 100%. Reject nonfinite values. |
| The room has one `light` although execution has a lamp and ceiling light | Represent the configured light IDs separately, or map one explicitly and show the other in a state card. Do not let a ceiling event overwrite the floor lamp. Prefer separate meshes for the two configured lights. |
| No blinds in `RoomState` or the scene | Add `Blinds.tsx`, a window/blinds state projection, and `set_blinds` visualization; zero closed, 100 open. |
| No canonical `alert_caregiver` mapping | Map the configured contact alert to phone presentation; preserve accepted/delivered/acknowledged/failed/unknown distinctions. Never invent a recipient. |
| Donor tool aliases include unsupported door and calling actions | Keep the door as scenery unless the real manifest supports control. Don't expose unsupported actions just because an animation exists. |
| `done` can apply original tool arguments without a result | Update actual device state from authoritative snapshots or verified result state. Proposed/approved/unknown actions show pending status only. |
| Flash logic uses duration and restarts expiry from receipt time | Use overlay ID, start/end time and pulse count. Two blue pulses must mean two cycles; expired overlays stay expired on reconnect. |
| Speaker playback is inferred from sound name/volume | Show playing only with the proper confirmed state; a muted track can still be playing, and a pending track is not playing. |
| Phone status auto-clears after four seconds | Animation may end, but alert delivery/error/acknowledgment remains in canonical state and the adjacent status card. |
| Room exposes an ARIA change announcer | Keep it concise and coordinate with other live regions so captions and room changes do not produce duplicate announcements. It must not invoke ElevenLabs itself. |

`show_caption` updates the shared output display; `announce` uses the shared speaker service. Neither requires an independent room speech engine. Volume, track, light levels and alert status should also have readable text alongside the illustration.

Do not use `useRoomFromEvents` with a second socket or replay a changing full event list from `initialRoom()` on every render. The provider holds canonical state; the room selector is a pure projection. Keep a small clock-driven animation layer only for active temporary visuals, with no execution side effects.

## 8. File responsibilities

Adapt names to avoid duplicating files that already exist, but maintain these ownership boundaries:

| File / area | Work |
|---|---|
| `frontend/app/layout.tsx` | Shared provider and shell; retain metadata and typography |
| `frontend/app/page.tsx` | Chat page composition; prompt, then camera, then thinking/execution |
| `frontend/app/room/page.tsx` | Room route and status details |
| `frontend/components/layout/AppShell.tsx`, `Sidebar.tsx` | Desktop sidebar and mobile navigation |
| `frontend/components/FluffyBrand.tsx` | One shared brand style used in sidebar/mobile/header |
| `frontend/components/CameraPreview.tsx` | Visible camera card, no ownership of capture lifecycle |
| `frontend/components/ThinkingLabel.tsx` | Event-driven stage text with existing shimmer |
| `frontend/components/PlanBody.tsx`, `HomeOutcome.tsx` | Typed live plan/result view models; real question responses |
| `frontend/providers/FluffySessionProvider.tsx` | Session, canonical state, UI socket, capture and audio owners |
| `frontend/lib/fluffy/types.ts`, `normalizeEvent.ts`, `reducer.ts`, `client.ts` | Contracts, compatibility boundary, state transitions, backend requests |
| `frontend/lib/media/`, `frontend/public/audio/pcm-worklet.js` | Adapted capture controller, persistent frame source, audio worklet |
| `frontend/lib/audio/` | One queue, message-ID deduplication, playback acknowledgments |
| `frontend/features/room/` | Imported room source, projection adapter, geometry/theme fixes |
| `frontend/tests/` | Focused event, room projection, and navigation lifecycle tests |
| `docs/frontend-integration-notes.md` | Source commits, import list, actual backend mode, startup and verification |

Adapt donor `useVoice` carefully: its browser-speech fallback is silent, acknowledgments lack message IDs, and effect cleanup can end speech. Follow execution.md's explicit playback status and no-replay behavior. Missing ElevenLabs audio must not be reported as successful ElevenLabs playback. Route changes must not recreate the player.

Keep provider keys out of `NEXT_PUBLIC_*`, browser requests, logs and client bundles. Public config may contain only nonsecret backend URLs and the selected protocol mode. Use explicit configured HTTP/WS backend addresses during local development; the donor assumes Vite proxies `/ws/live`, which this Next app does not automatically provide. Match HTTP origin permissions and WebSocket origin validation with the backend. Use HTTPS/WSS together if later hosted.

## 9. Implementation sequence and checkpoints

1. **New worktree and source audit:** record branches/SHAs, identify current backend capabilities, establish the Next app as the only UI host.
2. **Shell and navigation:** build sidebar, brand and two routes while preserving current chat appearance. Confirm keyboard/mobile navigation.
3. **Persistent provider:** centralize canonical state, text submission, UI connection and error states. Replace fixture-driven live progress with event-driven panels.
4. **Camera:** attach shared preview directly below prompt; implement/port capture against the selected backend transport. Verify it continues on Room and reappears on return.
5. **Room import:** display the 3D scene through a lazy client wrapper, align theme, then implement canonical-state projection and the listed fixes.
6. **Audio and questions:** reuse one speech controller, captions, active-question controls and incident status. Confirm no navigation-induced replay or duplicate speech.
7. **End-to-end wiring:** typed and media-triggered turns update the same panels and room. External outcomes remain distinct from requested effects.
8. **Validation and handoff:** run build/type checks and targeted tests, inspect the actual browser layouts, record verified/blocked capabilities and exact startup commands.

Prioritize a complete light-state update across both routes before adding decorative detail. If the backend is incomplete, finish and verify the frontend against a labeled contract fixture, document the precise missing route/event, and continue work that does not depend on it. Never present fixture validation as a successful live integration.

## 10. Acceptance checks

**Visual and navigation**

- The app still looks like the original white Fluffy chatbot, with the same brand treatment and recognizable panels.
- Sidebar contains Fluffy and Room with correct active state; mobile layout has no horizontal overflow.
- The working text input is followed immediately by live video, before thinking/execution panels.
- The 3D room uses the shared neutral theme and remains readable if rendering fails.

**Truthful state**

- A text request posts to the configured backend; no keyword match substitutes for a response.
- A camera/audio event can create a turn without typing.
- Proposed and approved actions do not show as completed. Failure and unknown outcomes stay visible.
- A brightness of 1 displays 1%; light IDs do not overwrite each other; blinds and two-pulse flashes map correctly.
- Photon acceptance differs from delivery, and successful speech generation differs from playback.

**Lifecycle**

- Start capture, navigate Fluffy → Room → Fluffy repeatedly: one MediaStream, one UI socket, one speaker owner, and no lost draft/current question.
- Verify frame uploads continue on Room even though the chat preview unmounted.
- Switching pages while Fluffy speaks neither restarts nor interrupts speech.
- Refresh resynchronizes state without replaying speech, flashes, songs or messages.
- Stop releases browser devices and clears pending sends/timers; reconnect cannot dispatch stale media from the previous session.
- Permission denial and backend outage have distinct, useful UI states.

Use focused reducer/projection tests for meaningful edge cases, plus a browser check for navigation/camera/audio lifecycle. Run `npm run build`, the configured lint command, and TypeScript checks from `frontend/`; add a test runner only if needed for the new behavior. Inspect widths around 1440, 1024 and 390 px, keyboard focus, reduced motion, and at least one provider-failure example. Do not claim a live camera/provider check was completed if the environment cannot perform it.

At completion, report the new branch, imported source commits/files, UI changes, selected backend contract and URLs, tests performed, and any backend/provider blockers. Leave a reviewable diff; do not push or deploy without a request.

## References

Primary evidence: the inspected source files in the repository branches listed above; the user's supplied ElevenLabs sidebar image; `01-execution.md` and `04-integration-plan.md`.

- R1: https://nextjs.org/docs/app/guides/lazy-loading — client-only lazy loading; use the installed version's guidance when it differs.
- R2: https://nextjs.org/docs/app/getting-started/layouts-and-pages — shared layout and navigation structure.

This document is a plan for Cursor. No integration branch, source merge, dependency installation, or frontend implementation was performed while preparing it.
