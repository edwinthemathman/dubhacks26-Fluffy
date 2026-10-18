# Live feed: spike results and how it works

## What Perceptron supports
Perceptron has **no streaming or live video input**. A request takes a finished video or 2–256 timestamped frames (`video_frames`). "Live" in Fluffy means sending a **rolling window** of recent webcam frames every few seconds.

Live audio can't ride inside frames (`enable_audio_in_video` only works on encoded video), so it goes in as a separate WAV part in the same request. The spike confirmed this works together with tools.

## Spike results
Measured on Oct 17, 2026 from this laptop on the Perceptron sample clip, using 640×360 JPEG at quality 70 and 4 s windows (`scripts/live_spike.py`):

| Call | Frames | Reasoning | p50 | p90 | Prompt tokens |
|---|---|---|---|---|---|
| scene read | 2 | none | 0.8 s | 1.5 s | 783 |
| scene read | 6 | none | 1.0 s | 1.3 s | 1,251 |
| scene read | 12 | none | 1.2 s | 1.4 s | 1,953 |
| scene read | 6 | low | 2.3 s | 2.7 s | 1,263 |
| decision call: frames + audio + tools | 6 | low | 0.8 s | 0.9 s | — |
| sound read, audio only | — | none | 0.3 s | 0.6 s | — |

- **About 117 tokens per frame.** An 8-frame read costs about $0.0002, so watching continuously stays well under $1/hour.
- Low reasoning roughly doubles scene-read time, so live scene reads use **none**. The decision call uses **low**, and it's still about 1 s.
- **Streaming isn't worth it:** the first chunk arrives at 0.6 s against 1.0 s for the whole reply, and tool calls only arrive at the end.

## End-to-end check
These runs used a fake browser streaming a clip into the live server:
- Scene reads arrive about every 3 s, and decision turns take about 1 s.
- A synthetic doorbell under the Deaf profile led to `flash_lights` blue about 2 s after the ring ended.
- The safety check-in ("Are you okay?") fires immediately on a fall cue and isn't repeated while the fall stays latched.

## Two fixes the live runs showed
1. **Sound labels invented from silence.** A scene read guessed sound labels from the picture (it read "impact" from a bouncing basketball while the audio was silent), which tripped the new "thud and nobody visible" fall rule. A silent window is sent as frames only, and any sound label or quote on it is dropped. Audible audio, including quiet speech, goes out with the frames the way a recording's soundtrack does. The words come back on that same read (`said`) instead of a second reasoning pass.
2. **Empty sound labels.** The sound read often describes the sound ("a doorbell rings") but returns an empty label list. The live loop now falls back to keywords in the description (`labels_from_text`). Short words such as "fall" and "help" have to be whole words, so "falling asleep" or "helpful" is not a fall. The clip demo (`fluffy/demo/analyze.py`) has the same weakness.

## Running it
```sh
./scripts/live                      # harness on :8000 (needs PERCEPTRON_API_KEY in .env)
cd web && npm run dev               # open http://localhost:5173/demo, then Start camera
                                    # open /mind in a second window to watch decisions
uv run python scripts/live_spike.py --watch --decide   # terminal-only live test (needs camera permission for the terminal)
```

## How the live loop works (`src/fluffy/live/`)
- **`useLiveCapture`** (web): sends JPEG frames 4 times a second, plus a motion score and 16 kHz audio, over `/ws/live`. It doesn't send mic audio while text-to-speech is speaking.
- **Watch lane:** after motion settles (0.5 s), after 3 s of continuous motion, or every 5 s, it runs a scene read on the last 4 s (8 frames, reasoning none). When the mic is not silent, that read includes the audio.
- **Hear lane:** at the end of each burst of sound, it runs a sound read. While a question is open it instead reads a yes, no or help answer. A burst that ends during a read waits, and a newer one replaces it.
- **Disconnect:** when the last camera socket closes, the loop pauses and drops the buffered frames instead of keeping the last picture.
- **Decide lane:** a new cue or speech triggers one decision call on the last 6 s plus the audio, with tools. It uses the words already heard. A newer trigger replaces a queued one, results older than 10 s are dropped, and a cue is latched for 30 s.
- **Safety:** a fall or a call for help starts the check-in right away. A model call to `alert_caregiver` also goes through the check-in instead of a second question.
- **Output:** every step is sent on `/ws/ui` as the web app's existing events, so the Demo and Mind pages show it live.

## Stage timings
`turn.end` and `GET /api/health` now carry `frame_age_ms`, `scene_read_ms`, `decide_wait_ms`, `decide_ms`, and `speech_to_tool_ms`. No camera session was run for this pass, so the knobs stay where they are: `watch_frames` is still 8, the heartbeat is unchanged, and audio still rides on every audible scene read.

| Stage | Measured |
|---|---|
| Frame age at read | not run |
| Scene read | not run |
| Decide queue wait | not run |
| Decide | not run |
| End of speech to tool.call | not run |

## Accuracy checklist
These need a person, a mat, and the live camera. None were run in this pass.

| Check | Result |
|---|---|
| Real fall on a mat | not run |
| Calm walk-out does not ask about a fall | not run |
| Dozing on the sofa | not run |
| Shielding eyes, lamp at 30% | not run |
| Shielding eyes, lamp at 80% | not run |
| Doorbell | not run |
| Spoken request | not run |

## Habit checklist
The habit code is already on this branch. These were not physically verified.

| Check | Result |
|---|---|
| Walk out at least 6 s shows Mind "left" and lights off, with no question | not run |
| Walk in shows "walked in" and lights on at 80% within about 2 s | not run |
| Dozing dims to 20 and warm | not run |
| Lamp at 30% with glare does nothing | not run |
| A second glare within 10 minutes does nothing | not run |

Speech recognition now keeps the latest send and speaking callbacks in refs, so a render does not restart Chrome's recognizer. `/ws/live` reconnects with the same capped backoff as `/ws/ui` and leaves the camera up. The banner says "Reconnecting…" while that retry is in progress.

## Instinct clips
These are the gesture clips for the deeper guess (face and pockets, cough, cupped ear, raised palm, and the look-alikes). None have been recorded yet. `scripts/label_clips.py` prints the list and, once the clips and `PERCEPTRON_API_KEY` are present, the action labels Perceptron returns.

| Clip | Result |
|---|---|
| Glasses search, water, cane, keys at the door | not run |
| Look-alikes: scratch, worn glasses, crumbs, standing up, pet, TV | not run |
| Cupped ear and raised palm, with and without music | not run |

## Still open
- Nod answers (answer mode) aren't wired yet. Answers come from buttons, typing or voice.
- No real fall has been tested on a live camera yet. Try it on the Demo page with a mat.
- The home state is in memory. The real executor plugs into `LiveLoop._run_plan`.

## Perceptron log
Every scene read, decision, and locate call appends one line to `logs/perception.jsonl` (frame count, raw text, parsed objects, tool calls, and whether a call was kept). The Raw drawer on the home page shows the latest of those. Save this moment writes `logs/moments/<time>/`.

## Object check
`scripts/check_objects.py` prints the layout to score: glasses on the left of the side table, keys on the right of the counter, phone in hand, cup on the coffee table, remote on the sofa. The camera pass has not been run.
