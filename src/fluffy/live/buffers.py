"""Rolling buffers for the live feed: recent camera frames and recent mic audio."""

from __future__ import annotations

import array
import io
import math
import wave
from collections import deque
from dataclasses import dataclass

SAMPLE_RATE = 16_000


@dataclass(frozen=True)
class Frame:
    ts_ms: int  # browser timestamp
    jpeg: bytes
    motion: float  # 0..1, mean frame difference computed in the browser
    at: float  # server monotonic time it arrived


class FrameBuffer:
    def __init__(self, keep_s: float = 12.0) -> None:
        self.keep_ms = int(keep_s * 1000)
        self.frames: deque[Frame] = deque()

    def add(self, ts_ms: int, jpeg: bytes, motion: float, at: float) -> None:
        if self.frames and ts_ms < self.frames[-1].ts_ms:
            ts_ms = self.frames[-1].ts_ms  # Perceptron needs timestamps that never decrease
        self.frames.append(Frame(ts_ms, jpeg, motion, at))
        while self.frames and self.frames[0].ts_ms < ts_ms - self.keep_ms:
            self.frames.popleft()

    def clear(self) -> None:
        self.frames.clear()

    @property
    def latest(self) -> Frame | None:
        return self.frames[-1] if self.frames else None

    def window(self, span_s: float, count: int) -> list[tuple[bytes, int]]:
        """`count` frames spread evenly over the last `span_s` seconds, timestamps from 0."""
        if len(self.frames) < 2:
            return []
        end = self.frames[-1].ts_ms
        pool = [f for f in self.frames if f.ts_ms >= end - int(span_s * 1000)]
        if len(pool) < 2:
            pool = list(self.frames)[-2:]
        count = max(2, min(count, len(pool)))
        picks = [pool[round(i * (len(pool) - 1) / (count - 1))] for i in range(count)]
        base = picks[0].ts_ms
        return [(f.jpeg, f.ts_ms - base) for f in picks]

    def span_of(self, frames: list[tuple[bytes, int]]) -> float:
        return frames[-1][1] / 1000 if frames else 0.0

    def motion(self, since: float) -> float:
        """Largest motion score among frames that arrived after monotonic time `since`."""
        return max((f.motion for f in self.frames if f.at >= since), default=0.0)


class AudioBuffer:
    """PCM16 mono at 16 kHz. Sample positions are absolute since the stream started."""

    def __init__(self, keep_s: float = 15.0, rate: int = SAMPLE_RATE) -> None:
        self.rate = rate
        self.keep = int(keep_s * rate)
        self.samples = array.array("h")
        self.start = 0  # absolute position of samples[0]

    @property
    def end(self) -> int:
        return self.start + len(self.samples)

    def add(self, pcm: bytes) -> float:
        """Append a chunk and return its RMS level (0..32767)."""
        chunk = array.array("h")
        chunk.frombytes(pcm[: len(pcm) // 2 * 2])
        self.samples.extend(chunk)
        extra = len(self.samples) - self.keep
        if extra > 0:
            del self.samples[:extra]
            self.start += extra
        if not chunk:
            return 0.0
        return math.sqrt(sum(s * s for s in chunk) / len(chunk))

    def clear(self) -> None:
        self.start = self.end
        self.samples = array.array("h")

    def last(self, seconds: float) -> bytes:
        n = int(seconds * self.rate)
        return self.samples[-n:].tobytes() if n else b""

    def loudest(self, seconds: float, step_s: float = 0.1) -> float:
        """Highest 100 ms RMS level over the last `seconds` (0 when there is no audio)."""
        n = min(len(self.samples), int(seconds * self.rate))
        step = max(1, int(step_s * self.rate))
        tail = self.samples[len(self.samples) - n :]
        best = 0.0
        for i in range(0, len(tail), step):
            chunk = tail[i : i + step]
            if chunk:
                best = max(best, math.sqrt(sum(s * s for s in chunk) / len(chunk)))
        return best

    def between(self, start: int, end: int) -> bytes:
        lo = max(start, self.start) - self.start
        hi = max(lo, min(end, self.end) - self.start)
        return self.samples[lo:hi].tobytes()


def to_wav(pcm: bytes, rate: int = SAMPLE_RATE) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(pcm)
    return buf.getvalue()


class SpeechGate:
    """Finds the end of a burst of sound (speech, a thud, a doorbell) in the mic stream.

    Feed it each chunk's RMS and its end position. It returns (start, end) sample
    positions once the burst is followed by `hang_s` of quiet.
    """

    def __init__(
        self,
        rate: int = SAMPLE_RATE,
        threshold: float = 700.0,
        hang_s: float = 0.6,
        min_s: float = 0.15,
        max_s: float = 12.0,
        pre_roll_s: float = 0.3,
    ) -> None:
        self.rate = rate
        self.threshold = threshold
        self.hang = int(hang_s * rate)
        self.min = int(min_s * rate)
        self.max = int(max_s * rate)
        self.pre = int(pre_roll_s * rate)
        self.burst_start: int | None = None
        self.last_loud = 0

    def feed(self, rms: float, chunk_end: int, chunk_len: int) -> tuple[int, int] | None:
        loud = rms >= self.threshold
        if loud:
            if self.burst_start is None:
                self.burst_start = chunk_end - chunk_len
            self.last_loud = chunk_end
        if self.burst_start is None:
            return None
        quiet_for = chunk_end - self.last_loud
        too_long = chunk_end - self.burst_start >= self.max
        if quiet_for >= self.hang or too_long:
            start, end = self.burst_start, self.last_loud
            self.burst_start = None
            if end - start < self.min:
                return None
            return max(0, start - self.pre), min(chunk_end, end + self.hang // 2)
        return None
