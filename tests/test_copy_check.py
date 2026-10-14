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


@pytest.mark.parametrize("text", [
    "Comment YES if this was you",
    "comment the word GUIDE and I'll send it",
    "Drop a 🔥 if you agree",
    "comment \"rest\" below",
    "Tag a friend who needs this",
    "tag 3 founders",
    "Like if you've been there",
    "double tap if this is you",
    "follow for follow",
    "Like, comment and share!",
    "share this to your story",
    "vote with an emoji",
])
def test_copy_rejects_engagement_bait(text):
    hits = check_copy(text)
    assert hits and hits[0].section == "bait", text


@pytest.mark.parametrize("text", [
    "Send this to the friend who works past midnight.",
    "Follow if you want the next one, I post every day.",
    "What's the one thing that actually helped you rest?",
    "Save this for the next time you want to quit.",
    "I nearly gave up — no joke. Here's what changed.",
    "Nobody would reply, no matter what I tried.",
    "I like it if the code is boring.",
])
def test_copy_allows_plain_asks(text):
    assert check_copy(text) == [], text


def test_private_rules_cannot_drop_bait():
    C.PRIVATE.mkdir()
    (C.PRIVATE / "banned-phrases.txt").write_text("[copy]\n\\bfoo\\b\n")
    assert check_copy("Tag a friend who needs this", load_rules())
