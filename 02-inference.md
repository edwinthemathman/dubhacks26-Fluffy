# Part 2: Inference and Decision-Making

**Goal:** turn what the camera sees and the mic hears into the right help for *this* person. **Perceptron Mk1.5 is the only brain.** One model sees frames, hears audio clips and decides through tool calls. (Grok Voice was dropped because of API problems.) This part produces plans (Part 1's contract) and messages for the person (captions, spoken text, questions). It never touches devices.

**Owns**
- `src/fluffy/perception/`
- `src/fluffy/world/`
- `src/fluffy/brain/` (`decide`, `consent`, `safety`, `arbiter`)
- `src/fluffy/profile/`
- `config/profiles/`

**Uses**
- `FrameSource` and `AudioSource` from Part 3
- the registry and plan models from Part 1

**Perceptron facts this part relies on**
- Takes images, `video_frames` bursts and audio clips (WAV, MP3 or FLAC) in a single request.
- Supports tool calling, JSON-schema output and `regex` output, but **a request can't combine tools with a schema or regex**.
- Tool-call arguments can be malformed JSON, so they must always be validated.
- **The API keeps no memory between calls.** Context has to be resent each time.
- Limits: 36,864-token context and 300 requests/min. Price: $0.15 per million input tokens and $1.50 per million output tokens.

---

## 1. Profiles (`config/profiles/*.toml`)
There are three presets, switched from the demo UI. A profile controls how Fluffy talks *and* how it listens for answers.

| | `blind` | `deaf` | `speech_cognitive` |
|---|---|---|---|
| Output | speech (browser text-to-speech), detailed spatial descriptions | captions and light cues, **no audio only** | speech at 0.85× rate plus captions, short sentences |
| Input | voice | typing, buttons, nod | voice (lenient), nod, pointing, buttons |
| Confirm back | low-confidence requests only | low-confidence requests only | **always** |
| Questions | combined | combined | **one at a time**, repeated once after 20 s |

Every profile agrees in advance to notify and safety actions, and names the caregiver. Each profile also lists a few of the person's own common words, which go into transcription prompts as vocabulary hints.

## 2. Perception calls (Perceptron)
- **SceneState** comes from a settled frame plus the frame before it, using a JSON schema and `reasoning_effort="none"` at 640×360. It's triggered by the change detector, a 60 s heartbeat, or a priority request, with at most 6 calls per minute.

  Fields:
  - **per person:** posture, `on_floor`, `location_surface` (floor, sofa or bed), eyes, gesture (pointing, shielding eyes or waving), `attention_target` (a device id from `home.toml`)
  - `objects_seen` for the tracked objects (keys, phone, glasses, wallet, pill organizer, cane, hearing aids), each with `location_desc`
  - `text_present`, `lighting`, `summary` and `confidence`
- **Speech:** a local voice-activity gate (energy based, with 300 ms of audio kept before the trigger) cuts each utterance into a WAV clip of at most 15 s. Push-to-talk is the fallback. Each clip goes to **two Perceptron calls in parallel**:
  1. **Transcription** (`reasoning_effort="none"`, with the vocabulary hints). It feeds captions, memory and the `distress_call` keyword check.
  2. **The decision call** (§4).
- **Answer mode (nods):** while a question is open, send a 1.5 s `video_frames` burst every 2 s with `regex="nod|shake|none"`. Stop at the first clear answer, with a 30 s limit.
- **Sound labels:** a local loudness gate grabs 4 s of non-speech audio and sends it to Perceptron with `regex="doorbell|knock|smoke_alarm|name_called|none"`, at most once every 3 s. A doorbell sensor event from Part 1 skips this step.
- **On-demand meta tools** (run when the decision call asks for them):
  - `describe_scene(detail)`: describes positions relative to the person and nearby landmarks
  - `find_object(name)`: checks object memory first, then looks live if the last sighting is over 15 minutes old
  - `read_text()`: OCR. If the text is cut off, blurry or off-center, the grounded box is used to coach the person ("move it a little left", "closer"), up to 3 tries.
  - `look(question)`: a general visual question

## 3. World model (`src/fluffy/world/`)
- **Room context:** who is present, their posture and activity, how long each has lasted, and the latest SceneState.
- **Object memory:** `{object: last_seen_at, location_desc}`.
- **Device state:** mirrored from Part 1's state stream.
- **Recent memory:** the last 10 utterances (from transcription), plus plans and results from the last 30 minutes.
- **Events** (fire only after the dwell time; these are what start a decision call):

| Event | Fires when | Handled as |
|---|---|---|
| `possible_fall` | `on_floor` (not sofa or bed) for 10 s or more | **safety**, handled by the safety state machine, not the model |
| `distress_call` | "help", "I fell" or "can't get up" in a transcript, or the SOS button | **safety**, immediately |
| `doorbell`, `knock`, `smoke_alarm`, `name_called` | sound label or sensor | **notify** turn |
| `pointing_at_device` | gesture `pointing` with an `attention_target`, while someone is speaking | context for the spoken turn |
| `glare_signal`, `getting_dark` | dwell 10 s | **offer** turn (always asks) |

- **ContextSnapshot:** about 600 tokens with an id. It covers the profile summary, room status, device states, recent events, recent utterances, open questions and object memory. Because Perceptron keeps no memory, **the snapshot is sent with every decision call**.

## 4. The brain: Perceptron decision calls (`src/fluffy/brain/decide.py`)
Every decision is one Perceptron chat completion. It includes:
- **System prompt:** persona plus profile rules.
  - Never change the home without consent.
  - Describe space relative to the person.
  - For atypical speech, guess generously, then confirm.
  - Never tell a blind person to "look", or a Deaf person to "listen".
  - Don't narrate; stay brief.
  - If nothing is needed, call no tools and reply with an empty message.
- **User content:** the settled frame (plus the one before it), the utterance audio clip or the event text, and the ContextSnapshot.
- **Tools:**
  - Part 1's device tools. These always become **plans**; nothing runs directly.
  - Meta tools: `describe_scene`, `find_object`, `read_text`, `look`, `ask_user(question, choices?)` and `confirm_plan(plan_id, yes|no)`.
- **Settings:** `reasoning_effort="low"`, with `max_completion_tokens` around 1024.

**Tool loop**
1. Meta tools run, and their results go back in a follow-up request with the history resent. This is limited to 3 rounds.
2. Device calls from the final round are validated and grouped into one plan.
3. The final text reply is what Fluffy says. It's captioned, and spoken with text-to-speech when the profile allows.

**Turn types**

| Turn | Starts when | What happens |
|---|---|---|
| **Spoken** | an utterance clip | A spoken request counts as consent, *except* under the `speech_cognitive` profile or with an unclear transcript, where Fluffy confirms back first. |
| **Notify** | a sound event | Input is `[HOME EVENT] doorbell; profile deaf`. The model picks the alert (for example `flash_lights` blue plus `show_caption`). |
| **Offer** | a comfort event | Same kind of input. The model's device calls become plans marked `awaiting_consent`, handled by the consent manager (§5). |

The model returns text instead of audio, so captions come for free, and the harness decides whether to speak the reply. No audio has to be thrown away.

**Expected latency:** about 2–4 s per spoken turn, since there's no streaming voice model. While a turn is running, the companion screen shows a small "thinking" indicator.

## 5. Consent manager (`src/fluffy/brain/consent.py`)
1. A plan marked `awaiting_consent` is turned into a question in the person's format. For example, "Dim the floor lamp?" is spoken, and/or captioned with big YES/NO, and/or preceded by a soft light pulse to get attention (Deaf profile).
2. The first clear answer wins. It can come from:
   - a voice answer: the open question is in the snapshot, so the decision call returns `confirm_plan`. A quick yes/no check on the transcript can also resolve it without waiting.
   - a nod or shake in answer mode
   - a YES/NO button on the companion screen
   - typing
   - pointing at the device in question
3. No answer means one repeat (cognitive profile), then **no**. The same offer isn't made again for 30 minutes.
4. Once approved, the plan goes to Part 1 with `consent.given_by` filled in.

## 6. Safety state machine (`src/fluffy/brain/safety.py`)
This is deterministic, so it's fast and predictable, and it doesn't wait on a model call. Its check-in messages are fixed text.
```
possible_fall | distress_call
 → check-in: "Are you okay?" (speech + xl caption with YES / NEED HELP + amber light pulse)
     okay / YES / nod ........... cancel, log, watch closely for 5 min
     help / NEED HELP / shake ... alert_caregiver(high) now
     no answer 30 s ............. second check-in, louder, strobe
     no answer 60 s ............. alert_caregiver(high, "Sam may have fallen in the living room; no response")
 → tell the person "Alex has been notified"; check in every 60 s until the caregiver acknowledges
```

## 7. Arbiter (`src/fluffy/brain/arbiter.py`)
This stays small. It checks that:
- the tool is known and the arguments parse and are in range
- `comfort` plans carry consent
- inferred offers are only *asked* if confidence is 0.7 or higher (lower ones are logged as "considered")
- there are at most 1 offer per 2 minutes and no repeat of a declined offer

## 8. Tests (`tests/inference/`, offline)
Tests use a fake Perceptron provider and a frozen clock. They cover:
- **Consent:** comfort plans never leave without consent; each kind of answer resolves the question; silence means no.
- **Safety:** every branch of the state machine.
- **Events:** SceneState sequences produce the right events after their dwell times.
- **Profiles:** routing is right (the Deaf profile never relies on audio only).
- **Object memory:** freshness rules work.
- **Tool loop:** the 3-round limit, malformed arguments are rejected, and device calls are grouped into one plan.

## 9. Build order
1. **API spike** (2 h). Measure:
   - end-to-end latency of a decision call with frame, audio clip and tools
   - how often tool-call arguments are valid
   - transcription of atypical speech, with and without vocabulary hints
   - nod detection, sound labels, OCR and on-floor detection on webcam frames
2. **Utterance capture, transcription and decision calls, confirm-back and the consent manager** (buttons and voice first). Reaches milestone **I2**. (4 h)
3. **SceneState, world model and snapshot, object memory, `describe_scene`, `find_object` and `read_text`.** Reaches **I3** for scenes 1 and 2. (5 h)
4. **Answer mode (nods), sound labels, notify turns.** (3 h)
5. **The safety state machine.** Reaches **I4**. (2.5 h)
