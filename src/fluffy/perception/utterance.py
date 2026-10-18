"""Cut a PCM16 mic buffer into utterance clips.

The live microphone (Part 3) owns capture. This module only decides which
spans of samples are speech. Push-to-talk bypasses the energy gate and
hands the whole buffer over, trimmed to the maximum clip length.
"""

import array
from collections import deque

SAMPLE_RATE = 16_000
WINDOW_S = 0.02
PRE_ROLL_S = 0.3
# Trailing silence kept so a short pause does not split one sentence.
HANGOVER_S = 0.4
MAX_CLIP_S = 15.0


def slice_utterances(
    pcm: bytes,
    *,
    sample_rate: int = SAMPLE_RATE,
    threshold: float = 500.0,
) -> list[bytes]:
    """Return one PCM16 clip per burst of speech.

    Each clip includes up to 300 ms of audio from before the burst and is
    never longer than 15 s.
    """
    samples = _samples(pcm)
    if not samples:
        return []

    window = max(1, int(sample_rate * WINDOW_S))
    pre_roll = max(1, int(PRE_ROLL_S / WINDOW_S))
    hangover = max(1, int(HANGOVER_S / WINDOW_S))
    max_windows = max(1, int(MAX_CLIP_S / WINDOW_S))

    recent: deque[array.array] = deque(maxlen=pre_roll)
    clips: list[bytes] = []
    active: list[array.array] = []
    silent_run = 0
    recording = False

    for start in range(0, len(samples) - window + 1, window):
        piece = samples[start : start + window]
        loud = _rms(piece) >= threshold
        if not recording:
            if loud:
                recording = True
                active = list(recent)
                active.append(piece)
                silent_run = 0
            else:
                recent.append(piece)
            continue

        active.append(piece)
        silent_run = 0 if loud else silent_run + 1
        hit_limit = len(active) >= max_windows
        trailed_off = silent_run >= hangover
        if hit_limit or trailed_off:
            clips.append(_join(active))
            recording = False
            active = []
            silent_run = 0
            recent.clear()

    if active:
        clips.append(_join(active))
    return [clip for clip in clips if clip]


def clip_push_to_talk(pcm: bytes, *, sample_rate: int = SAMPLE_RATE) -> bytes:
    """Trim a held-button recording to the maximum clip length."""
    samples = _samples(pcm)
    keep = sample_rate * int(MAX_CLIP_S)
    return samples[:keep].tobytes()


def _samples(pcm: bytes) -> array.array:
    if len(pcm) < 2:
        return array.array("h")
    if len(pcm) % 2:
        pcm = pcm[:-1]
    samples = array.array("h")
    samples.frombytes(pcm)
    return samples


def _rms(window: array.array) -> float:
    if not window:
        return 0.0
    total = sum(sample * sample for sample in window)
    return (total / len(window)) ** 0.5


def _join(parts: list[array.array]) -> bytes:
    out = array.array("h")
    for part in parts:
        out.extend(part)
    return out.tobytes()
