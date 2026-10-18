"""Narrow yes/no questions asked before Fluffy says an instinct out loud.

Each question is its own Perceptron call with a regex, so the answer is one
of a few words. Fluffy speaks only when those answers agree with the guess.
"""

from __future__ import annotations

from dataclasses import dataclass

from fluffy.brain.instincts import Guess, rewrite


@dataclass(frozen=True)
class CheckQuestion:
    key: str
    text: str
    regex: str


_WHERE = (
    "side table|coffee table|dining table|kitchen counter|shelf|sofa|armchair|"
    "bed|nightstand|floor|by the door|none"
)
_SIDE = "( (left|right|middle|front|back))?"

_QUESTIONS: dict[str, tuple[CheckQuestion, ...]] = {
    "glasses": (
        CheckQuestion("wearing", "Is the person wearing glasses right now?", "yes|no|unclear"),
        CheckQuestion("on_head", "Are there glasses on top of their head?", "yes|no|unclear"),
        CheckQuestion(
            "where",
            "Are glasses visible on any table, shelf or counter? Answer with the surface, or none.",
            f"{_WHERE}{_SIDE}",
        ),
        CheckQuestion(
            "looking",
            "Is the person touching their face or pockets as if looking for something, or for another reason?",
            "looking|other|unclear",
        ),
    ),
    "water": (
        CheckQuestion(
            "gripping",
            "Is the person gripping their throat with one or both hands, as if they cannot breathe or speak?",
            "yes|no|unclear",
        ),
        CheckQuestion(
            "coughing",
            "Is the person coughing or rubbing their throat, or neither?",
            "coughing|throat|neither|unclear",
        ),
        CheckQuestion(
            "where",
            "Where is a water bottle, glass, cup or mug? Answer with the surface and side, or none.",
            f"{_WHERE}{_SIDE}",
        ),
        CheckQuestion("holding", "Is the person holding a drink right now?", "yes|no|unclear"),
    ),
    "volume_up": (
        CheckQuestion("cupped", "Is a hand cupped behind the person's ear?", "yes|no|unclear"),
    ),
    "repeat": (
        CheckQuestion("cupped", "Is a hand cupped behind the person's ear?", "yes|no|unclear"),
    ),
    "stop_music": (
        CheckQuestion(
            "palm",
            "Is the person holding up an open palm, still, like a stop sign?",
            "yes|no|unclear",
        ),
    ),
    "keys": (
        CheckQuestion("looking", "Is the person patting pockets or a bag as if looking for something?", "looking|other|unclear"),
        CheckQuestion("where", "Where are the keys, wallet or phone? Answer with the surface, or none.", f"{_WHERE}{_SIDE}"),
    ),
    "hearing aids": (
        CheckQuestion("looking", "Is the person feeling an ear or looking around for a small object?", "looking|other|unclear"),
        CheckQuestion("where", "Where are the hearing aids? Answer with the surface, or none.", f"{_WHERE}{_SIDE}"),
    ),
    "cane": (
        CheckQuestion("reaching", "Is the person holding furniture for balance while standing or walking?", "yes|no|unclear"),
        CheckQuestion("where", "Where is the cane or walker? Answer with the surface, or none.", f"{_WHERE}{_SIDE}"),
    ),
    "dropped": (
        CheckQuestion("looking", "Is the person looking down beside where they were sitting?", "yes|no|unclear"),
        CheckQuestion("where", "What fell, and where is it? Answer with the surface, or none.", f"{_WHERE}{_SIDE}"),
    ),
    "remote": (
        CheckQuestion("looking", "Is the person pressing a remote or looking for one?", "looking|other|unclear"),
        CheckQuestion("where", "Where is the remote? Answer with the surface, or none.", f"{_WHERE}{_SIDE}"),
    ),
    "lost": (
        CheckQuestion("looking", "Did the person stop and turn back, as if they lost track?", "yes|no|unclear"),
    ),
}


def questions_for(need: str) -> tuple[CheckQuestion, ...]:
    return _QUESTIONS.get(need, ())


def confirm(guess: Guess, answers: dict[str, str]) -> Guess | None:
    """The guess to speak, or None when the double-check disagrees.

    A yes on gripping the throat replaces a water guess with the safety check-in.
    Glasses on the head replace a table sentence.
    """
    if not guess.check:
        return guess
    need = guess.need
    if need == "water" and answers.get("gripping") == "yes":
        return rewrite(Guess("safety", "choking", "Are you okay?", ("Yes", "No"), guess.chain, check=False), "Are you okay?")
    if not _agrees(need, answers):
        return None
    if need == "glasses" and answers.get("on_head") == "yes":
        return rewrite(guess, "I think your glasses might be on top of your head.")
    return _marked(guess, answers)


def _agrees(need: str, answers: dict[str, str]) -> bool:
    if need == "glasses":
        if answers.get("wearing") == "yes":
            return False
        if answers.get("on_head") == "yes":
            return True
        if answers.get("looking") != "looking":
            return False
        return bool(answers.get("where"))
    if need == "water":
        if answers.get("holding") == "yes" or answers.get("gripping") == "yes":
            return False
        if answers.get("coughing") not in {"coughing", "throat"}:
            return False
        return True
    if need in {"volume_up", "repeat"}:
        return answers.get("cupped") == "yes"
    if need == "stop_music":
        return answers.get("palm") == "yes"
    if need == "cane":
        return answers.get("reaching") == "yes"
    if need == "lost":
        return answers.get("looking") == "yes"
    if need == "dropped":
        return answers.get("looking") == "yes"
    looking = answers.get("looking")
    if looking not in {None, "looking", "yes"}:
        return False
    return True


def _marked(guess: Guess, answers: dict[str, str]) -> Guess:
    if not answers or "→ said:" not in guess.chain:
        return guess
    marks = ", ".join(f"{key} {value}" for key, value in answers.items())
    head, said = guess.chain.rsplit("→ said:", 1)
    return Guess(
        guess.kind,
        guess.need,
        guess.text,
        guess.choices,
        f"{head}→ check: {marks} → said:{said}",
        guess.check,
    )
