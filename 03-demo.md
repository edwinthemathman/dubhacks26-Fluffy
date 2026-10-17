# Part 3: Live Demo and Environment

**Goal:** run Fluffy live in front of judges, with a real webcam, mic and speaker, and make each decision visible. Judges should see three things: the person on camera, Fluffy's room changing, and the list of tool calls appearing and getting checked off. Every scene also needs a reliable fallback.

**Owns**
- `src/fluffy/sensors/`: live capture and the clip fallback
- `src/fluffy/audio_out/`
- `src/fluffy/server/static/`: the room, companion and caregiver pages
- the physical set
- scene scripts
- `demo/fallbacks/`

**Uses**
- Part 1's `/ws/ui` and `/ws/caregiver` streams and its `POST` endpoints
- Part 2's decisions, delivered as captions, questions and plans over `/ws/ui`

---

## 1. Sensors (`src/fluffy/sensors/`)
- **`FrameSource`** is the interface Part 2 consumes:
  - `LiveCamera`: OpenCV webcam at 15 fps, with a RAM ring buffer of about 10 s
  - `ClipCamera`: a recorded `.mp4` with the same interface, used as the fallback
- **`AudioSource`** is the matching audio interface:
  - `LiveMic`: `sounddevice` capture as PCM16 at 16 kHz. Part 2 cuts it into utterance and sound clips for Perceptron.
  - `ClipAudio`: audio from the recorded clip
- Both are chosen with one flag: `fluffy demo --source live|clip:<name>`.
- **Fluffy's voice:** spoken replies use the browser's built-in text-to-speech (`speechSynthesis`) on the demo screen, at the rate set in the profile. No voice model or API is involved.
- **Half-duplex audio:** the browser reports when speech starts and ends over `/ws/ui`. The mic is muted for that time plus a 300 ms tail, so Fluffy doesn't hear itself.
- **Privacy:** frames and audio stay in RAM. The UI always shows a "camera/mic live" indicator and a pause control that works by button or by saying "Fluffy, pause".

## 2. Screens (vanilla HTML/JS served by the harness, no build step)
The **demo screen (`/`)** is what judges watch: three columns side by side.
1. **Live camera** with Perceptron overlays, such as the box around the pointed-at lamp or the found keys, plus the latest scene summary.
2. **Fluffy's room:** an illustrated living room. The lamp glow follows `luminosity` and `warmth`, the blinds follow `position`, the speaker shows a sound wave and plays the sound, and the walls pulse in the flash color. A caption banner sits across the bottom.
3. **Tool-call list:** each call shows its arguments and trigger (spoken, inferred, sound or safety). Its status moves from proposed, to awaiting consent ("asked: Dim the floor lamp?"), to approved, to done. A chime plays when a plan is done.

There's also a profile switcher (blind, deaf, speech_cognitive), a "Deaf user" or "Blind user" badge, and a small "Fluffy is thinking" indicator while a Perceptron decision call is running.

The **companion screen (`/companion`)** is what the *person* uses. It runs on a tablet or second laptop next to them.
- xl high-contrast captions
- big YES / NO / NEED HELP buttons, 64 px or larger
- a text box for typed input
- screen-reader friendly: ARIA live regions read the captions aloud, it works fully by keyboard, and nothing is conveyed by color or sound alone

The **caregiver screen (`/caregiver`)** is for the caregiver, on a phone or a corner of the demo screen.
- status: "Sam: living room, okay"
- alerts with a timeline and an **Acknowledge** button
- a snapshot only if the profile allows it

## 3. Set and environment
- **One staged living room corner:** a sofa or chair, a real floor lamp as a prop (Fluffy's room shows the "smart" version), a table with keys and a letter, and a clear patch of floor for the fall scene. Put a yoga mat down so the fall can be done safely.
- **Camera:** a webcam on a tripod at about 1.5 m high, framing the sofa, table and floor. Light the set evenly, not from behind.
- **Audio:**
  - a lapel or directional USB mic for the person, since hackathon venues are loud
  - a small speaker for Fluffy's voice
  - a Bluetooth speaker or phone playing a real doorbell sound for scene 3
- **Noise fallbacks**, all switchable from the demo screen:
  - push-to-talk (hold space)
  - typed input on the companion screen
  - the mock-home doorbell button (`POST /api/sensors/doorbell`)

## 4. Scenes (live, about 60–90 s each)
| # | Profile | What the actor does | What Fluffy does (calls shown in the list) | Fallback |
|---|---|---|---|---|
| 1 | **blind** | "Fluffy, where are my keys?" Then holds up a letter: "What does this say?" | `find_object(keys)`, then speaks "On the table, to the left of your mug. I saw them two minutes ago." Then `read_text()` with coaching ("a little closer") and reads the sender and main points aloud. A box appears on the live camera. | clip: `describe_find` |
| 2 | **speech_cognitive** | Points at the lamp and says, unclearly, "that one… dimmer" | Spoken turn with `attention_target = lamp.living_floor`, giving a `dim_lights` plan marked awaiting consent. Fluffy asks "Dim the floor lamp?" by voice and xl caption. The actor **nods**, consent is given by nod, and the lamp in Fluffy's room dims. | clip: `hands_free`; YES button |
| 3 | **deaf** | Watching TV, back to the door. A doorbell plays from the speaker. | A sound label of doorbell triggers a notify turn: `flash_lights(blue, pulse3)` and `show_caption("Doorbell. Someone is at the front door.")`. Fluffy's room pulses blue, the companion shows the caption, and the lights return to their earlier state. | mock doorbell button |
| 4 | **any** | Sits down on the floor mat and stays still | `possible_fall` starts a check-in: "Are you okay?" by speech, caption and amber pulse. No answer, then a second, louder check-in. No answer again, then `alert_caregiver(high)`. The caregiver view shows the alert and the person is told "Alex has been notified." | the "Test alert" button |

Before scene 4, say out loud: "This is a simulation. Fluffy isn't a medical alert device."

## 5. Fallbacks and recordings
- Each scene has a clip (`demo/clips/<scene>.mp4`) that can be fed through the **live pipeline** with `--source clip:<scene>` when the room is too noisy or the camera framing fails.
- If the APIs fail, the last successful run's plan and messages for each scene (`demo/fallbacks/<scene>.json`) can be replayed into the screens. The UI labels this **"Recorded run"**. No second brain is built to produce it.
- A **Reset room** button returns devices, captions and caregiver alerts to their starting state between scenes.

## 6. Rehearsal checklist
- Run each scene live 3 times in a row, and record the latency shown on the screen:
  - spoken request to question asked: 3 s or less (Perceptron hears and decides in one call)
  - doorbell to flash: 3 s or less
  - floor dwell to check-in: 2 s or less
- Run each scene on its clip fallback and its recorded fallback.
- Test the venue's lighting and noise in the hour before judging, and set the push-to-talk default to match.
- Check `/companion` with VoiceOver: captions are announced and the buttons are labeled.
- Keep a 30-second pitch line ready for each scene, explaining who it helps and why asking first matters.

## 7. Build order
1. The demo screen's room column and tool-call list, wired to `/ws/ui`. This reaches **I1** together with Part 1. (3 h)
2. Live camera and mic sources, half-duplex speaker, profile switcher, `/companion` with buttons and typing. This supports **I2**. (3 h)
3. Camera overlays (boxes and points from Perceptron), the caregiver screen. This supports **I3** and **I4**. (2.5 h)
4. Build the set, record the fallback clips, run scenes end to end, save recorded runs. Reaches **I5**. (3 h)
5. Accessibility pass on `/companion` and rehearsal. (1.5 h)
