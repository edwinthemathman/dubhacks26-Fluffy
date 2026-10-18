"""Deterministic wellbeing score. No model call.

A request is normal. A check-in needs corroboration. Only a debilitated
assessment may text the caregiver, and only the safety state machine does that.
"""

from __future__ import annotations

import logging
import re
import tomllib
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal

from fluffy.perception.scene import PersonState, SceneState

log = logging.getLogger("fluffy.wellbeing")

Status = Literal["fine", "request", "uncertain", "possibly_down", "debilitated"]

_REQUEST = re.compile(
    r"("
    r"\bhelp me\s+(get|find|with|turn|open|reach|grab|bring|pick|close|play|stop|dim|put|make|do|move)\b"
    r"|(\bcan you\b|\bcould you\b).{0,40}\bhelp\b"
    r"|\bhelp with\b"
    r"|\b(get|bring|find|grab) me\b"
    r")",
    re.IGNORECASE,
)
_STRONG = re.compile(
    r"("
    r"\bi fell\b"
    r"|\bi can(?:not|'t) get up\b"
    r"|\bi can(?:not|'t) move\b"
    r"|\bcall (?:for help|an ambulance|911)\b"
    r"|\bi need help\b"
    r"|\bneed help\b"
    r")",
    re.IGNORECASE,
)
_OKAY = re.compile(
    r"\b(i'm fine|im fine|i am fine|i'm okay|im okay|i am okay|i'm ok|im ok)\b|^(yes|yeah|ok|okay|nod)$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class SafetyThresholds:
    checkin_min_confidence: float = 0.85
    fall_dwell_s: float = 20
    scene_min_confidence: float = 0.7
    alert_min_confidence: float = 0.9
    checkin_cooldown_s: float = 600
    on_floor_weight: float = 0.6
    eyes_closed_weight: float = 0.2
    no_speech_weight: float = 0.1
    strong_phrase_weight: float = 0.9


@dataclass(frozen=True)
class EvidenceItem:
    kind: str
    weight: float
    summary: str


@dataclass(frozen=True)
class Assessment:
    status: Status
    confidence: float
    evidence: list[EvidenceItem] = field(default_factory=list)
    counter_evidence: list[EvidenceItem] = field(default_factory=list)


@dataclass
class WellbeingInput:
    transcript: str = ""
    scenes: list[tuple[datetime, SceneState]] = field(default_factory=list)
    now: datetime | None = None
    sos: bool = False
    last_speech_at: datetime | None = None
    answered: bool = False
    checkin_unanswered_s: float | None = None
    still_down: bool = False
    thresholds: SafetyThresholds = field(default_factory=SafetyThresholds)


def load_thresholds(path: Path | None = None) -> SafetyThresholds:
    root = Path(__file__).resolve().parents[3]
    file = path or (root / "config" / "fluffy.toml")
    if not file.is_file():
        return SafetyThresholds()
    raw = tomllib.loads(file.read_text(encoding="utf-8")).get("safety", {})
    defaults = SafetyThresholds()
    values = {}
    for name in defaults.__dataclass_fields__:
        if name in raw:
            values[name] = type(getattr(defaults, name))(raw[name])
    return SafetyThresholds(**values) if values else defaults


def is_request(text: str) -> bool:
    cleaned = _clean(text)
    if not cleaned or is_strong_phrase(cleaned):
        return False
    return _REQUEST.search(cleaned) is not None


def is_strong_phrase(text: str) -> bool:
    cleaned = _clean(text)
    if not cleaned or _REQUEST.search(cleaned):
        return False
    return _STRONG.search(cleaned) is not None


def is_okay_phrase(text: str) -> bool:
    return _OKAY.search(_clean(text)) is not None


def assess(context: WellbeingInput) -> Assessment:
    """Score one moment. Requests never become a safety trigger."""

    thresholds = context.thresholds
    text = _clean(context.transcript)
    evidence: list[EvidenceItem] = []
    counter: list[EvidenceItem] = []
    signals = 0
    score = 0.0
    decisive = False

    if context.sos:
        decisive = True
        signals += 1
        score = 1.0
        evidence.append(EvidenceItem("button", 1.0, "SOS button"))

    if text and is_request(text):
        result = Assessment(
            "request",
            0.95,
            [],
            [EvidenceItem("speech", 0.9, "asked for something")],
        )
        _log(result, thresholds, "request")
        return result

    if text and is_strong_phrase(text):
        decisive = True
        signals += 1
        score = max(score, thresholds.strong_phrase_weight)
        evidence.append(EvidenceItem("speech", thresholds.strong_phrase_weight, text))

    floor = _floor(context, thresholds)
    if floor is not None:
        signals += 1
        score += thresholds.on_floor_weight
        evidence.append(EvidenceItem("vision", thresholds.on_floor_weight, floor.summary))
        if floor.eyes_closed:
            signals += 1
            score += thresholds.eyes_closed_weight
            evidence.append(EvidenceItem("vision", thresholds.eyes_closed_weight, "eyes closed on the floor"))
        if floor.silent:
            signals += 1
            score += thresholds.no_speech_weight
            evidence.append(EvidenceItem("speech", thresholds.no_speech_weight, "no speech since going down"))

    if _recent_talk(context, thresholds) and not (text and is_strong_phrase(text)):
        counter.append(EvidenceItem("speech", 0.6, "coherent speech in the last minute"))
        score -= 0.6
    if context.answered:
        counter.append(EvidenceItem("speech", 0.5, "answered a question"))
        score -= 0.5
    if _upright_or_busy(context):
        counter.append(EvidenceItem("vision", 0.8, "upright, moving, or on furniture"))
        score = min(score, 0.2)
    if _changed(context):
        counter.append(EvidenceItem("vision", 0.3, "posture or motion changed"))
        score -= 0.3

    score = max(0.0, min(1.0, score))
    if context.sos:
        status: Status = "debilitated"
    elif (
        context.checkin_unanswered_s is not None
        and context.checkin_unanswered_s >= 60
        and context.still_down
        and score >= thresholds.alert_min_confidence
    ):
        status = "debilitated"
    elif not decisive and signals < 2:
        status = "uncertain" if evidence else "fine"
        score = min(score, thresholds.checkin_min_confidence - 0.01) if status == "uncertain" else score
    elif score >= thresholds.alert_min_confidence and context.sos:
        status = "debilitated"
    elif score >= thresholds.checkin_min_confidence:
        status = "possibly_down"
    elif evidence:
        status = "uncertain"
    else:
        status = "fine"

    result = Assessment(status, round(score, 3), evidence, counter)
    _log(result, thresholds, "scored")
    return result


@dataclass
class _Floor:
    summary: str
    eyes_closed: bool
    silent: bool


def _floor(context: WellbeingInput, thresholds: SafetyThresholds) -> _Floor | None:
    if not context.scenes or context.now is None:
        return None
    if not _confident_floor(context.scenes[-1][1], thresholds):
        return None
    run_scenes: list[tuple[datetime, SceneState]] = []
    for at, scene in reversed(context.scenes):
        if not _confident_floor(scene, thresholds):
            break
        run_scenes.append((at, scene))
    run_scenes.reverse()
    started = run_scenes[0][0]
    latest = run_scenes[-1][1]
    dwell = (context.now - started).total_seconds()
    person = _person(latest)
    still_burst = len(run_scenes) == 1 and person is not None and person.motion == "none"
    if dwell < thresholds.fall_dwell_s:
        return None
    if len(run_scenes) < 2 and not still_burst:
        return None
    person = _person(latest)
    eyes = person is not None and person.eyes == "closed"
    silent = context.last_speech_at is None or context.last_speech_at < started
    return _Floor("still on the floor", eyes, silent)


def _confident_floor(scene: SceneState, thresholds: SafetyThresholds) -> bool:
    if scene.confidence < thresholds.scene_min_confidence:
        return False
    person = _person(scene)
    if person is None:
        return False
    if person.location_surface in {"sofa", "bed"}:
        return False
    if not person.on_floor:
        return False
    if person.posture in {"standing", "walking"}:
        return False
    if person.motion == "purposeful":
        return False
    if person.activity_hint in {"reaching", "picking_up", "exercising", "playing_on_floor"}:
        return False
    if person.gesture in {"pointing", "waving"}:
        return False
    return True


def _person(scene: SceneState) -> PersonState | None:
    for person in scene.people:
        if not person.on_screen:
            return person
    return None


def _upright_or_busy(context: WellbeingInput) -> bool:
    if not context.scenes:
        return False
    scene = context.scenes[-1][1]
    person = _person(scene)
    if person is None:
        return False
    if person.location_surface in {"sofa", "bed"}:
        return True
    if person.posture in {"standing", "walking"}:
        return True
    if person.motion == "purposeful" or person.activity_hint in {"reaching", "picking_up", "exercising", "playing_on_floor"}:
        return True
    return False


def _changed(context: WellbeingInput) -> bool:
    if len(context.scenes) < 2:
        return False
    previous = _person(context.scenes[-2][1])
    current = _person(context.scenes[-1][1])
    if previous is None or current is None:
        return False
    return previous.posture != current.posture or (
        previous.motion == "none" and current.motion in {"small", "purposeful"}
    )


def _recent_talk(context: WellbeingInput, thresholds: SafetyThresholds) -> bool:
    del thresholds
    if context.now is None or context.last_speech_at is None:
        return False
    return context.now - context.last_speech_at <= timedelta(seconds=60)


def _clean(text: str) -> str:
    return " ".join(text.strip().lower().replace("\u2019", "'").split())


def _log(result: Assessment, thresholds: SafetyThresholds, why: str) -> None:
    log.info(
        "wellbeing %s status=%s confidence=%.2f checkin>=%.2f alert>=%.2f dwell>=%.0fs scene>=%.2f evidence=%s counter=%s",
        why,
        result.status,
        result.confidence,
        thresholds.checkin_min_confidence,
        thresholds.alert_min_confidence,
        thresholds.fall_dwell_s,
        thresholds.scene_min_confidence,
        [item.summary for item in result.evidence],
        [item.summary for item in result.counter_evidence],
    )
