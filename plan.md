# Show Perceptron's thinking on the demo page

## Context
The demo page only shows the final verdict ("Nothing to do", heard, scene summary). When it gets a clip wrong (e.g. the off-camera fall read as "walked out of the frame"), there is no way to see *why*. The Perceptron SDK already returns the model's reasoning on every response as `completion.reasoning` (`ChatCompletion.reasoning` → `message.reasoning_content`, `.venv/.../perceptron/chat.py:310`), and `perceptron-mk1.5` supports reasoning, but [analyze.py](src/fluffy/demo/analyze.py) discards it. Goal: a "How Fluffy decided" section that walks through each step with the model's own thinking.

## Step 0
Save this plan as `Fluffy-DubHacks-/plan.md`. Not in the parent folder: macOS treats `plan.md` and the existing `../PLAN.md` as the same file.

## Step 1 — Check reasoning comes back (before building UI)
A one-off script in the scratchpad runs `_read_scene` style call on the sample clip (`SAMPLE_CLIP_URL`) with `reasoning_effort="low"` and prints `completion.reasoning`. If it is empty, the trace below still ships, just without the model's words (thinking fields show "not returned").

## Step 2 — Collect a trace in [analyze.py](src/fluffy/demo/analyze.py)
- `_read_scene` returns `(scene, error, reasoning)`. Capture `completion.reasoning` **before** `_parse_model`, so a failed parse still shows the thinking. `_hear` / `_transcribe` stay `reasoning_effort="none"` (fast) and log `reasoning: None`.
- In `_analyze_uploaded`, build `trace: list[dict]`, one entry per step:
  1. `{"step": "heard", "summary": heard.heard, "labels": sounds}`
  2. `{"step": "transcript", "summary": transcript}`
  3. `{"step": "scene", "timeline": scene.timeline, "summary": scene.summary, "thinking": reasoning}`
  4. `{"step": "cues", "cues": sorted(cues)}`
  5. one per decision round: `{"step": "decide", "round": n, "thinking": completion.reasoning, "reply": reply, "tool_calls": [{name, args, reason, confidence}], "rejected": bad}`
  6. `{"step": "fallback", ...}` only when `analyze.py`'s `plan is None and "fall" in cues` branch builds the help plan
  7. `{"step": "consent", "status", "reason", "question"}`
- Pass `trace` through `_result()` into the JSON response. Reasoning strings capped at ~4k chars to keep responses small.

## Step 3 — Render it in [index.html](src/fluffy/demo/static/index.html)
- In `render(body)`, after the existing sections and before "Call details", add `<details><summary>How Fluffy decided</summary>…</details>`. Opened automatically when outcome is `quiet` but `heard` contains `impact`/`pain`/`help` (a likely miss).
- Each step is a small numbered block: a kicker label (Heard / Saw / Cues / Decision round N / Fallback / Consent), a one-line summary, and the thinking in a `<pre class="thinking">` (`white-space: pre-wrap`, muted color, max-height with scroll).
- Everything goes through the existing `escapeHtml`.

## Step 4 — Tests
- [test_demo_page.py](tests/inference/test_demo_page.py): assert "How Fluffy decided" is in the page.
- New test calling `_analyze_uploaded` with a fake client (returns canned completions with `.reasoning`, `.text`, `.tool_calls`), asserting `trace` has scene + decide entries with the thinking text, and that the fallback step appears when cues contain `fall` and the model proposed nothing.

## Out of scope
- The live camera loop ([src/fluffy/live/](src/fluffy/live/)) has its own calls; not changed here.
- Turning reasoning on for sound/transcript calls (slower; can follow later).

## Verification
1. `uv run pytest` passes.
2. `./scripts/demo` → http://127.0.0.1:8091 → **Try the sample clip**: "How Fluffy decided" lists the steps with thinking text under Saw and Decision.
3. Re-upload the fall clip: the trace shows what the scene read thought and whether the help question came from the model or the fallback rule.

Note: the model's thinking is its own notes, a strong hint about how it decided, not an exact replay.
