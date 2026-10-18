# Merge plan: inference, voice, thinking, and demo

`front-old-test` is a snapshot of the current front end, taken from `thinking` (`f5ab29e`). It keeps the blue-and-white React app in `web/` as the reference while the four features are joined into one screen.

## What the branches actually are

`inference`, `voice`, and `thinking` are already one line of history. Each later branch contains the earlier ones.

```
main (86cfce4)
  └─ inference (f7b2fb9)   decisions, consent, clip demo, live loop
       └─ voice (385ea42)  spoken questions, ElevenLabs, the React app
            └─ thinking (f5ab29e)  plan.md for showing the model's notes
                 └─ front-old-test   this branch, same commit
```

There is no `demo` branch yet. The closest room work is outside this history:

- `room3d/` in the parent folder: a reducer that turns tool calls into light, door, speaker, and phone state.
- `origin/cursor/execution-tool-calls-1eb6`: the executor, mock home, and a separate Next page. It split from `main` and does not contain inference.

Do not git-merge those three feature branches into each other. That merge is already done. The work is to give each feature one job on the screen, then plug demo into the action lane when it lands.

## One turn, three lanes

Every Perceptron turn can produce three different things. They stay on one WebSocket (`/ws/ui`) and go to three different places.

| Lane | What it is | Where it shows | Code today |
|---|---|---|---|
| **Thinking** | The model's notes while it works. Background only. Never spoken. | A quiet panel behind the room (the Mind screen, and the clip page's "How Fluffy decided"). | Clip path: `analyze.py` builds a `trace` with `completion.reasoning`, and `demo/static/index.html` renders it. Live path: short `think` notes in `live/loop.py`. `plan.md` is the spec for keeping that trace visible. |
| **Voice** | The line Fluffy actually decides to say, played as audio. | Speaker only. Captions on the companion screen for Deaf and speech profiles. | `Decider` returns `reply`. Questions use `_say(..., voice=True)`, which emits `say` with `say_id` and `voice`. `ElevenLabsVoice.stream` plays that line. `VoicePolicy` only decides whether an unprompted question may be asked. |
| **Inference** | What Fluffy decides to *do*: tool calls that change the home, after consent. | The room. | `Decider` splits meta tools from device tools and builds a `Plan`. The live loop emits `tool.call` and `tool.status`. Comfort actions wait for a yes. Notify and safety follow the profile. |
| **Demo** | The picture of the home. It consumes the inference lane. | The center of the judge screen, replacing the flat room drawing in `web/src/pages/Demo/Room.tsx`. | Not a branch yet. `room3d/src/toolMap.ts` already maps tool names onto light, door, speaker, and phone. |

A thought never changes the lamp and is never read aloud. A spoken line never changes the lamp. A tool call is the only thing the room applies.

## How a turn should travel

```
camera + mic
    → Perceptron
        → thinking lane:  reasoning text, cues, rejected calls     → background panel
        → voice lane:     reply or question, once                 → ElevenLabs audio + caption
        → inference lane: device tool calls + consent status      → demo room
```

The live loop already emits `think`, `say`, `ask`, `tool.call`, and `tool.status` (`web/src/stream/events.ts`). The gap is that the React app still treats them as one mixed timeline, and the live loop does not forward `completion.reasoning` the way the clip analyzer does.

## Build order

1. **Keep this branch as the old front.** `front-old-test` stays at `f5ab29e`. New join work happens on a later branch cut from here, so this UI can be run side by side.

2. **Mark each event with a lane** in `events.ts`: `thought`, `speech`, or `action`. Existing types map like this:
   - thought: `think`, `turn.raw`, `sense.see`, `sense.hear`
   - speech: `say` when `voice` is true, and the caption text that goes with it
   - action: `tool.call`, `tool.status`, `state`, `ask` / `ask.answer` (the room shows the question; the speaker plays it from the speech lane)

3. **Thinking stays in the background.** The Mind page renders the trace and the short `think` notes. It does not call ElevenLabs and it does not write room state. On the clip demo, "How Fluffy decided" is that same panel. For the live loop, copy the clip trace's `thinking` field onto `think` / `turn.raw` events so the background shows the model's own words, capped the way `analyze.py` already caps them.

4. **Voice plays only the decided line.** One player (`useVoice` + `ElevenLabsVoice`) speaks `say` events with `voice: true`. The companion screen shows the same text and does not also call `speechSynthesis` while ElevenLabs is connected. `VoicePolicy` stays the gate for unprompted questions. A held-back question is a `think` note (`would ask: …, holding off`), not audio.

5. **Inference is the only writer of the home.** The room reducer accepts `tool.call`, `tool.status`, and `state`. It ignores `think` and `say`. Consent still happens before a comfort tool is applied: `asking` shows the question, `done` moves the device.

6. **When the demo branch finishes, attach it to the action lane only.**
   - Diff its tool names against `tool_catalog.py` and `room3d/src/toolMap.ts`. Add aliases in the map. Do not rename tools inside `decide.py` to match the picture.
   - Replace `web/src/pages/Demo/Room.tsx` with the demo view. Feed it the action lane.
   - Leave the background thinking panel and the ElevenLabs player as they are.
   - If demo brings its own page (the execution branch's Next app, or `room3d`), it subscribes to the same `/ws/ui` action events. It does not open a second socket and it does not re-decide.

7. **Leave the execution branch for a separate join.** `origin/cursor/execution-tool-calls-1eb6` has the stub executor and mock home, and it diverged at `main`. Merge it after the lanes exist, and only to apply approved plans. It is not a source of thoughts or of spoken lines.

## Checks before calling the join done

- One recorded turn (the hands-free lamp scene) shows a thought in the background, speaks exactly one line, and dims the lamp only after yes.
- The doorbell scene speaks nothing for a Deaf profile, shows the caption, and pulses the lights from `flash_lights`.
- The fall scene speaks the check-in, then `alert_caregiver` reaches the caregiver view. The thinking panel shows the trace. The room does not treat the trace as a device change.
- A `say` event never changes lamp, door, speaker, or phone state.
- A `tool.call` never starts audio.
