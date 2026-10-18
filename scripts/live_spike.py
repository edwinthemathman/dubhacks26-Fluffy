"""Measure Perceptron on live-style frame windows before building the live loop.

Usage (from the repo root):
  uv run python scripts/live_spike.py                 # timings on the cached sample clip
  uv run python scripts/live_spike.py --video my.mp4  # timings on your own clip
  uv run python scripts/live_spike.py --webcam        # timings on 8 s grabbed from the webcam
  uv run python scripts/live_spike.py --watch         # print a live read every few seconds (stage a fall, etc.)
  add --write to save the timing table to docs/live-spike-runs.md

It answers:
  1. latency and prompt tokens of a scene read vs number of frames and reasoning effort
  2. whether frames + an audio part + tools work in one decision call, and how fast
  3. latency of an audio-only sound read
  4. time to first streamed chunk vs full time for the decision call
"""

from __future__ import annotations

import argparse
import asyncio
import io
import math
import os
import statistics
import struct
import sys
import time
import urllib.request
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import cv2  # noqa: E402  (dev-only dependency)
from dotenv import load_dotenv  # noqa: E402
from perceptron import AsyncClient  # noqa: E402

from fluffy.brain.cues import derive  # noqa: E402
from fluffy.brain.decide import system_prompt  # noqa: E402
from fluffy.demo.analyze import MODEL, SAMPLE_CLIP_NAME, SAMPLE_CLIP_URL  # noqa: E402
from fluffy.live.perceive import Window, decide, read_scene, read_sound  # noqa: E402
from fluffy.models.tool_catalog import perceptron_tools  # noqa: E402
from fluffy.perception.scene import apply_sound_read  # noqa: E402
from fluffy.profile import load_profiles  # noqa: E402

WIDTH = 640
JPEG_QUALITY = 70


# ---------- frame sources ----------

def _encode(frame) -> bytes:
    height = int(frame.shape[0] * WIDTH / frame.shape[1]) // 2 * 2
    small = cv2.resize(frame, (WIDTH, height), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
    if not ok:
        raise RuntimeError("JPEG encode failed")
    return buf.tobytes()


def frames_from_video(path: Path, max_seconds: float = 12.0) -> list[tuple[bytes, int]]:
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise SystemExit(f"Could not open {path}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    out: list[tuple[bytes, int]] = []
    index = 0
    step = max(1, round(fps / 8))  # keep about 8 frames per second of source
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        ms = int(index * 1000 / fps)
        if ms > max_seconds * 1000:
            break
        if index % step == 0:
            out.append((_encode(frame), ms))
        index += 1
    cap.release()
    if len(out) < 2:
        raise SystemExit("The clip gave fewer than two frames.")
    return out


def frames_from_webcam(seconds: float, fps: float = 4.0) -> list[tuple[bytes, int]]:
    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        raise SystemExit("Could not open the webcam. On macOS, allow camera access for your terminal app.")
    out: list[tuple[bytes, int]] = []
    start = time.monotonic()
    next_at = start
    while time.monotonic() - start < seconds:
        ok, frame = cap.read()
        if not ok:
            continue
        now = time.monotonic()
        if now >= next_at:
            out.append((_encode(frame), int((now - start) * 1000)))
            next_at += 1 / fps
    cap.release()
    return out


def pick(frames: list[tuple[bytes, int]], count: int, span_s: float) -> Window:
    """count frames evenly spread over the last span_s seconds, timestamps rebased to 0."""
    end_ms = frames[-1][1]
    start_ms = max(frames[0][1], end_ms - int(span_s * 1000))
    pool = [f for f in frames if f[1] >= start_ms]
    if len(pool) < count:
        pool = frames[-count:]
    idx = [round(i * (len(pool) - 1) / max(1, count - 1)) for i in range(count)]
    chosen = [pool[i] for i in idx]
    base = chosen[0][1]
    rebased = [(jpeg, ms - base) for jpeg, ms in chosen]
    # timestamps must never decrease; equal is allowed
    return Window(frames=rebased, seconds=(chosen[-1][1] - base) / 1000)


def synthetic_wav(seconds: float = 4.0, rate: int = 16000) -> bytes:
    """Quiet room tone with a short thump, just to test that an audio part is accepted."""
    samples = []
    for n in range(int(seconds * rate)):
        t = n / rate
        value = 0.01 * math.sin(2 * math.pi * 120 * t)
        if 1.5 < t < 1.6:
            value += 0.6 * math.sin(2 * math.pi * 60 * t) * math.exp(-(t - 1.5) * 30)
        samples.append(int(max(-1, min(1, value)) * 32767))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(struct.pack(f"<{len(samples)}h", *samples))
    return buf.getvalue()


# ---------- measurements ----------

def _p(values: list[int], q: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(q * (len(ordered) - 1)))]


async def measure(client: AsyncClient, frames, args) -> list[str]:
    rows = ["| test | frames | effort | p50 ms | p90 ms | prompt tokens | ok |", "|---|---|---|---|---|---|---|"]
    tokens_by_count: dict[int, int] = {}
    sizes = [int(s) for s in args.sizes.split(",")]
    for effort in args.efforts.split(","):
        for count in sizes:
            times, toks, errors = [], [], []
            for _ in range(args.runs):
                window = pick(frames, count, args.span)
                result = await read_scene(client, window, effort=effort)
                times.append(result.ms)
                toks.append(result.usage.get("prompt_tokens", 0))
                if result.error:
                    errors.append(result.error)
            tokens_by_count[count] = statistics.median(toks) if toks else 0
            ok = "yes" if not errors else f"no: {errors[0][:60]}"
            rows.append(f"| scene read | {count} | {effort} | {_p(times, .5)} | {_p(times, .9)} | {tokens_by_count[count]:.0f} | {ok} |")
            print(rows[-1], flush=True)

    if len(tokens_by_count) >= 2:
        counts = sorted(tokens_by_count)
        lo, hi = counts[0], counts[-1]
        per_frame = (tokens_by_count[hi] - tokens_by_count[lo]) / max(1, hi - lo)
        rows.append(f"\nEstimated tokens per {WIDTH}px frame: **{per_frame:.0f}**\n")
        print(rows[-1], flush=True)

    wav = synthetic_wav()
    profile = load_profiles(ROOT / "config" / "profiles")["blind"]

    window = pick(frames, 6, args.span)
    window.wav = wav
    times, errors, calls = [], [], []
    for _ in range(args.runs):
        result = await decide(client, window, profile=profile, utterance="", scene=None, cues=set())
        times.append(result.ms)
        if result.error:
            errors.append(result.error)
        calls.append(",".join(c.name for c in result.device) or "none")
    ok = "yes" if not errors else f"no: {errors[0][:60]}"
    rows.append("| test | frames | effort | p50 ms | p90 ms | tool calls | ok |")
    rows.append("|---|---|---|---|---|---|---|")
    rows.append(f"| decide: frames + audio + tools | 6 | low | {_p(times, .5)} | {_p(times, .9)} | {' / '.join(calls)} | {ok} |")
    print(rows[-1], flush=True)

    times, errors = [], []
    for _ in range(args.runs):
        result = await read_sound(client, wav)
        times.append(result.ms)
        if result.error:
            errors.append(result.error)
    ok = "yes" if not errors else f"no: {errors[0][:60]}"
    rows.append(f"| sound read: audio only | 0 | none | {_p(times, .5)} | {_p(times, .9)} | - | {ok} |")
    print(rows[-1], flush=True)

    first, total, err = await streamed_first_chunk(client, window, profile)
    rows.append(f"\nStreaming decision call: first chunk after **{first} ms**, complete after **{total} ms**{' (' + err + ')' if err else ''}.\n")
    print(rows[-1], flush=True)
    return rows


async def streamed_first_chunk(client: AsyncClient, window: Window, profile) -> tuple[int, int, str]:
    started = time.perf_counter()
    first = 0
    try:
        stream = await client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": system_prompt(profile)},
                {"role": "user", "content": [*window.media(), {"type": "text", "text": "Decide from the clip."}]},
            ],
            tools=perceptron_tools(),
            reasoning_effort="low",
            max_completion_tokens=512,
            stream=True,
            stream_options={"include_usage": True},
            timeout=30,
        )
        async for _chunk in stream:
            if not first:
                first = int((time.perf_counter() - started) * 1000)
    except Exception as exc:  # the spike reports anything that goes wrong
        return first, int((time.perf_counter() - started) * 1000), f"{type(exc).__name__}: {exc}"[:120]
    return first, int((time.perf_counter() - started) * 1000), ""


async def watch(client: AsyncClient, args) -> None:
    """Grab the webcam continuously and run a scene read on the last window every few seconds."""
    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        raise SystemExit("Could not open the webcam. On macOS, allow camera access for your terminal app.")
    profile = load_profiles(ROOT / "config" / "profiles")[args.profile]
    frames: list[tuple[bytes, int]] = []
    start = time.monotonic()
    last_grab = 0.0
    last_read = 0.0
    print("Watching. Ctrl+C to stop.", flush=True)
    try:
        while True:
            ok, frame = cap.read()
            now = time.monotonic()
            if ok and now - last_grab >= 0.25:
                frames.append((_encode(frame), int((now - start) * 1000)))
                frames = [f for f in frames if f[1] >= frames[-1][1] - 12000]
                last_grab = now
            if now - last_read >= args.every and len(frames) >= 4:
                last_read = now
                window = pick(frames, args.frames, args.span)
                scene_read = await read_scene(client, window, effort=args.effort)
                scene = scene_read.value
                cues = derive(apply_sound_read(scene, None)) if scene else set()
                timeline = " -> ".join(scene.timeline) if scene else scene_read.error
                print(f"[{scene_read.ms:>5} ms] {timeline}  cues={sorted(cues) or '-'}", flush=True)
                if cues and args.decide:
                    choice = await decide(client, window, profile=profile, utterance="", scene=scene, cues=cues)
                    calls = ", ".join(f"{c.name}({c.reason})" for c in choice.device) or "no tools"
                    print(f"           decide [{choice.ms} ms]: {calls} {choice.error or ''}", flush=True)
            await asyncio.sleep(0.01)
    except KeyboardInterrupt:
        pass
    finally:
        cap.release()


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--video", type=Path)
    parser.add_argument("--webcam", action="store_true")
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--decide", action="store_true", help="with --watch, also run a decision call when a cue appears")
    parser.add_argument("--profile", default="blind")
    parser.add_argument("--sizes", default="2,4,6,8,12")
    parser.add_argument("--efforts", default="none,low")
    parser.add_argument("--effort", default="none", help="effort for --watch")
    parser.add_argument("--frames", type=int, default=6, help="frames per window for --watch")
    parser.add_argument("--every", type=float, default=3.0, help="seconds between reads for --watch")
    parser.add_argument("--span", type=float, default=4.0, help="seconds covered by a window")
    parser.add_argument("--runs", type=int, default=2)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()

    load_dotenv(ROOT / ".env")
    key = os.environ.get("PERCEPTRON_API_KEY", "").strip()
    if not key:
        raise SystemExit("PERCEPTRON_API_KEY is not set (put it in .env).")

    async with AsyncClient(api_key=key, provider="perceptron") as client:
        if args.watch:
            await watch(client, args)
            return
        if args.webcam:
            source = "webcam (8 s)"
            frames = frames_from_webcam(8.0)
        else:
            path = args.video or Path(os.environ.get("TMPDIR", "/tmp")) / SAMPLE_CLIP_NAME
            if not path.exists():
                urllib.request.urlretrieve(SAMPLE_CLIP_URL, path)
            source = path.name
            frames = frames_from_video(path)
        height = cv2.imdecode(
            __import__("numpy").frombuffer(frames[0][0], dtype="uint8"), cv2.IMREAD_COLOR
        ).shape[0]
        print(f"Source: {source}, {len(frames)} frames at {WIDTH}x{height}, JPEG q{JPEG_QUALITY}", flush=True)
        rows = await measure(client, frames, args)

    if args.write:
        out = ROOT / "docs" / "live-spike-runs.md"
        header = [
            "# Live spike results",
            "",
            f"Run {time.strftime('%Y-%m-%d %H:%M')} on {source}, {WIDTH}x{height} JPEG q{JPEG_QUALITY}, "
            f"{args.span:.0f} s windows, {args.runs} runs each. Times are wall clock from this laptop.",
            "",
        ]
        out.write_text("\n".join(header + rows) + "\n")
        print(f"Wrote {out.relative_to(ROOT)}")


if __name__ == "__main__":
    asyncio.run(main())
