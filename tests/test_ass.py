from reels import ass

STYLE = {"font": "Atkinson Hyperlegible", "fontSize": 84, "textColor": "#FFFFFF",
         "highlightColor": "#FF6B4A", "outlineColor": "#1C1C1C", "outlineWidth": 10,
         "captionCenterY": 0.5}


def test_one_event_per_word_with_highlight():
    phrases = [{"start": 0.0, "end": 1.5, "words": [
        {"w": "rest", "start": 0.0, "end": 0.4}, {"w": "is", "start": 0.5, "end": 0.7},
        {"w": "work.", "start": 0.8, "end": 1.2}]}]
    out = ass.build(phrases, STYLE)
    events = [l for l in out.splitlines() if l.startswith("Dialogue")]
    assert len(events) == 3
    assert "0:00:00.00,0:00:00.50" in events[0]
    assert "0:00:00.80,0:00:01.50" in events[2]  # last word holds to phrase end
    assert r"{\c&H004A6BFF&}is{\r}" in events[1]  # coral in ASS BGR order
    assert r"\pos(540,960)" in events[0] and r"\fad(130,0)" in events[0]
    assert r"\fad" not in events[1]


def test_braces_escaped():
    phrases = [{"start": 0, "end": 1, "words": [{"w": "{x}", "start": 0, "end": 1}]}]
    assert "(x)" in ass.build(phrases, STYLE)
