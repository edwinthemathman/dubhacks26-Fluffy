# Object tagging: help people keep track of their things

## Context
Fluffy is pivoting toward everyday accessibility for elderly and disabled people: keeping important objects close and finding their way around the house. Fall detection, comfort and safety all stay as they are. This adds one new idea, **tags**: every scene read notes the portable objects it can see, where each one is, and whether the person has it on them. Tags are remembered over time and drive four new kinds of notification:

1. **Searching:** the person looks like they're hunting for something. Fluffy asks "Which are you looking for?" with tagged objects as choices, then says where the one they pick is, relative to them.
2. **Left behind:** they leave without an important object (wallet, keys, phone…). Fluffy tells them right away by voice and/or caption. If they've already left the room, it also texts them.
3. **Dropped:** an important object (cane, glasses, phone) falls to the floor. "Your cane fell by the sofa, to your right."
4. **Out of reach:** they're sitting or lying down and something they rely on (cane, walker, phone) is across the room. Fluffy offers to say where it is.

### What already exists and gets reused
- `SceneState.objects_seen: list[ObjectSighting(name, location_desc)]` in [scene.py](src/fluffy/perception/scene.py). The prompt limits it to 7 items, and the live loop ignores it.
- `ObjectMemory` / `Recall` in [world/model.py](src/fluffy/world/model.py) (fresh for 15 min, stale after). Tested, but not wired into the live loop.
- `find_object` meta tool. Its `_meta_result` in [analyze.py](src/fluffy/demo/analyze.py) currently always says "no saved sighting".
- `ask_user` with `choices`, plus `_asked_choices`, and the web `ask` event already renders choice buttons.
- `RoomPresence` in [presence.py](src/fluffy/live/presence.py) already emits `left_room`.
- Notify-class plans are pre-agreed: `Arbiter.review` allows them with no consent question. `Profile.delivery_channels()` picks voice, caption or light per profile.
- [photon.py](src/fluffy/notify/photon.py) sends SMS, but only one fixed fall text to one number.

## Changes

### 1. Tag schema in [scene.py](src/fluffy/perception/scene.py)
These changes are backwards-compatible: the field names stay, so `ObjectMemory` and the existing tests keep working.
- `ObjectSighting` gains:
  - `with_person: bool = False`: in hand, pocket, bag or worn.
  - `on_floor: bool = False`
  - `near_person: bool = False`: within arm's reach.
- `PersonState` gains:
  - `searching: bool = False`: scanning surfaces, patting pockets, lifting cushions, opening and closing drawers, retracing steps.
  - `looking_for: str | None = None`: set only if they say or clearly show what they want.

### 2. Scene prompt in [analyze.py](src/fluffy/demo/analyze.py) `SCENE_PROMPT` / `scene_prompt`
This prompt is shared with the live loop through [perceive.py](src/fluffy/live/perceive.py).
- Replace the 7-item `objects_seen` line with a tagging instruction:
  - Tag up to 8 small, portable things a person might reach for or lose: keys, phone, wallet, glasses, hearing aids, cane, walker, pill organizer, remote, cup, bag, book, charger.
  - `location_desc` is a fixed landmark ("on the side table by the sofa"), not left or right.
  - Fill in the three new flags.
  - Tag an object even if it's only partly visible.
- Add `searching` / `looking_for` guidance, including examples that don't count: tidying, cooking, reading a label.
- Ask for a timeline beat when an object changes hands, e.g. "puts wallet on table", "walks off without it".

### 2b. Retagging: room scan plus live tags
Two sources feed one memory:
- **Room scan (pre-tag):**
  - What it is: a tag-only call (`TAG_PROMPT` → `TagRead(objects: list[ObjectSighting])`) at `reasoning_effort="low"` with more frames than a normal live read. It's slower but catches small items.
  - When it runs: on camera start, when presence reports the room empty for 60 s or more, on `entered_room`, and on demand. On demand means the phrase "Fluffy, scan the room" or a new `room.scan` client message from a button on the Companion page.
  - What it sets: each tag's **usual spot** (`home_desc`), and, the first time, its last-seen spot as well.
- **Live reads (post-tag):** every `_watch` scene read updates **last seen**, `with_person`, `on_floor` and `near_person`.
- **How the two are combined:**
  - An object not seen in a read is marked `unconfirmed`, never deleted.
  - Only a sighting somewhere else, or `with_person`, moves it.
  - `find_object` answers with the last seen spot. If that's stale, it says "usually <home_desc>".
- **Profile names:** a `[objects]` table in the profile toml can name items, e.g. `"pill organizer" = "blue pill organizer"`. The scan prompt includes these names so the tags use the person's own words.

### 3. Object rules: new [brain/objects.py](src/fluffy/brain/objects.py)
A small rules module with no model calls, the same pattern as `cues.py` and the fall fallback.
- `IMPORTANT_DEFAULT = {wallet, keys, phone, glasses, hearing aids, cane, walker, pill organizer}`. Each profile can override it through a new `important_objects` list in `config/profiles/*.toml`, read by [profile/model.py](src/fluffy/profile/model.py).
- `ObjectMemory` in [world/model.py](src/fluffy/world/model.py) also stores `with_person`, `on_floor` and `near_person`. It gains `tags(now)`, which lists fresh tags with the person's important ones first.
- Rule functions, each returning a list of `ObjectNotice(kind, name, location, text)`:
  - `searching(scene, memory)`: anyone with `searching` → one `ask` with up to 4 tagged names plus "Something else". If `looking_for` matches a tag, skip the question and say where it is.
  - `left_behind(scene, memory, departing)`: the person is leaving (`activity == "leaving"` or presence `left_room`), and an important object was seen not with them in the last 2 minutes and is still in view → "Your wallet is still on the side table."
  - `dropped(scene, memory)`: an important tag goes from `on_floor` false to true → "Your cane fell, it's on the floor by the sofa."
  - `out_of_reach(scene, memory)`: the person is sitting or lying, and a mobility or phone tag (cane, walker, phone) is in view, not near them and not with them → an offer: "Your cane is by the door. Want me to tell Alex?" (no alert unless they say yes)
- Each kind fires at most once per object per 2 minutes.
- Wording follows the profile: spatial for blind, short and plain for speech/cognitive.

### 4. Decision prompts
- [decide.py](src/fluffy/brain/decide.py) `system_prompt`, new section **KEEPING THINGS CLOSE** with bullets for the four cases:
  - Searching with no word said → `ask_user` with choices taken from the tags.
  - They name or choose an object → `find_object`, then `announce`/`show_caption` saying where it is relative to them now.
  - Left behind / dropped → `announce` + `show_caption` (+ `flash_lights` for Deaf).
  - Never describe an object the camera hasn't tagged or can't see.
- `_user_text` in analyze.py gains:
  - a "Tagged objects" block (name, place, with/near/floor, age)
  - "Important to this person: …"
- `_meta_result("find_object")` answers from the tags ("wallet: on the side table by the sofa, seen 1 min ago, not with them") instead of "no saved sighting".

### 5. Live loop: [loop.py](src/fluffy/live/loop.py)
- Hold a `WorldModel.objects` memory on the loop, and update it after each `_watch` scene read.
- After the existing presence and cue handling, run the object rules:
  - Searching notices go through the existing `_ask_user(..., choices)` path, so the answer comes back as a decide request.
  - Left-behind and dropped notices go through `_tool_call` with `announce` / `show_caption` / `flash_lights`, chosen by `profile.delivery_channels()`.
  - Out-of-reach goes through `_ask_user`.
- Fall and safety always win: object rules are skipped while a `fall` cue or safety check-in is active.
- `_chips` adds tag chips such as "wallet · table" and "cane · floor". `sense.see` gains an optional `tags` list. The [events.ts](web/src/stream/events.ts) type is updated, and the [Mind](web/src/pages/Mind/Mind.tsx) page renders a small "Tagged" list.

### 6. Text after leaving: [photon.py](src/fluffy/notify/photon.py)
- Generalize `PhotonSms.send(text=ALERT_TEXT, to=ALERT_TO)`. The defaults keep the fall alert exactly as it is.
- Add `notify_person(text)`. It reads the person's number from a new `PERSON_PHONE` env var, added to `.env.example`, and does nothing quietly if that's unset.
- In the loop, when a left-behind notice is still unanswered and presence reports `left_room`, send one text per object per departure: "You left your wallet on the side table."

### 7. Clip demo: [analyze.py](src/fluffy/demo/analyze.py) + [index.html](src/fluffy/demo/static/index.html)
- One clip has no history, so the rules run on that clip's tags plus the timeline. "Puts wallet down … walks out" counts as left behind.
- The result JSON gains `tags` and `notices`.
- The page shows a **Tagged** section listing each object and where it is, and a notice card ("Fluffy would tell them: Your wallet is still on the table").
- Fall still overrides everything.

## Tests
New `tests/inference/test_objects.py`:
- searching produces an ask whose choices come from tags
- `looking_for` matching a tag skips the question
- leaving without a not-carried wallet produces a notice; carrying it produces none
- a cane going to the floor produces a dropped notice
- sitting with the cane across the room produces an out-of-reach offer
- the 2-minute cooldown holds
- fall suppresses object notices

Also:
- `find_object` answers from memory
- `PhotonSms.send` still sends the fall text by default
- `notify_person` with no number does nothing
- retagging: a scan sets `home_desc`; a live read that misses the object marks it unconfirmed but keeps it; a sighting elsewhere moves last seen; a stale sighting makes `find_object` fall back to "usually …"
- a room scan is triggered after 60 s empty, on `entered_room`, and on "scan the room"
- the [test_object_memory.py](tests/inference/test_object_memory.py) cases still pass
- the demo page contains "Tagged"

## Verification
1. `uv run pytest` passes, including the new tests and all existing ones.
2. Clip demo (`./scripts/demo`):
   - a clip of putting a wallet on a table and walking out → Tagged list plus a "left your wallet" notice
   - a clip of looking around and patting pockets → "Which are you looking for?" with choices
3. Live app: with a cane leaning on a chair, knock it over → spoken or captioned "Your cane fell…". Sit on the sofa with the cane across the room → the out-of-reach offer.
4. With `PERSON_PHONE` set: leave the room without the wallet → one SMS. Leave again within 2 minutes → no second SMS.

## Notes
- Object detection is only as good as Perceptron's read. Small items such as hearing aids or a pill organizer may be missed at a distance.
- Step 0 on approval: save this plan as `Fluffy-DubHacks-/plan.md`.
