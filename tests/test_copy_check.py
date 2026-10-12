import pytest

from reels import config as C
from reels.copy_check import check_copy, check_speech, load_rules


@pytest.mark.parametrize("text", [
    "This app treats burnout",
    "It cured my insomnia",
    "It reduces anxiety",
    "helps you manage your stress",
    "clinically backed habits",
    "therapeutic journaling",
])
def test_copy_rejects(text):
    assert check_copy(text), text


@pytest.mark.parametrize("text", [
    "I shipped the thing. #buildinpublic #founderlife",
    "Rest is part of the work 🍑",
    "I nearly gave up on this app. Here's what changed.",
    "Healthy habits, one small step at a time",
])
def test_copy_allows(text):
    assert check_copy(text) == []


def test_speech_allows_personal_mental_health_but_not_claims():
    assert check_speech("my mental health was in a bad place that week") == []
    assert check_speech("and the app basically cured my anxiety")


def test_private_rules_replace_the_defaults():
    C.PRIVATE.mkdir()
    (C.PRIVATE / "banned-phrases.txt").write_text("[copy]\n\\bmental[- ]wellness\\b\n")
    rules = load_rules()
    assert check_copy("a mental wellness app", rules)
    assert check_copy("this treats burnout", rules) == []
