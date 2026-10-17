import array

from fluffy.perception.utterance import SAMPLE_RATE, slice_utterances


def _tone(seconds: float, amplitude: int = 8000) -> array.array:
    count = int(seconds * SAMPLE_RATE)
    return array.array("h", [amplitude if (index // 20) % 2 == 0 else -amplitude for index in range(count)])


def _silence(seconds: float) -> array.array:
    return array.array("h", [0] * int(seconds * SAMPLE_RATE))


def _pcm(*parts: array.array) -> bytes:
    merged = array.array("h")
    for part in parts:
        merged.extend(part)
    return merged.tobytes()


def test_silence_produces_no_clip():
    assert slice_utterances(_pcm(_silence(2))) == []


def test_a_burst_keeps_pre_roll_and_stays_one_clip():
    clips = slice_utterances(_pcm(_silence(0.2), _tone(0.5), _silence(0.6)))
    assert len(clips) == 1
    assert len(clips[0]) > int(0.5 * SAMPLE_RATE) * 2


def test_a_pause_splits_two_utterances():
    clips = slice_utterances(_pcm(_tone(0.3), _silence(1.0), _tone(0.3), _silence(0.6)))
    assert len(clips) == 2


def test_clips_never_exceed_fifteen_seconds():
    clips = slice_utterances(_pcm(_tone(16), _silence(0.5)))
    assert len(clips) >= 2
    assert all(len(clip) <= SAMPLE_RATE * 15 * 2 for clip in clips)
