"""Fixed phrases run before the model, and a television does not."""

from fluffy.brain.commands import parse, route


def test_a_wake_word_finds_glasses_even_if_nobody_is_in_view():
    command = route("Fluffy, where are my glasses", person_in_view=False)
    assert command is not None
    assert command.name == "find"
    assert command.detail == "glasses"


def test_lights_off_needs_the_person_unless_fluffy_is_named():
    assert route("turn the lights off", person_in_view=False).name == "ignore"
    command = route("Fluffy, lights off", person_in_view=False)
    assert command is not None and command.name == "lights"
    assert command.detail == "off"


def test_unmatched_speech_goes_to_the_model():
    assert route("tell me about the weather", person_in_view=True) is None


def test_help_starts_a_check_in_and_finding_does_not():
    assert parse("help").name == "help"
    assert parse("I fell").name == "help"
    found = parse("help me find my glasses")
    assert found is not None and found.name == "find"
    assert found.detail == "glasses"
