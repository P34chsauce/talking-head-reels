from reels import words as W


def wd(w, s, e):
    return {"w": w, "start": s, "end": e}


def test_from_whisper_cpp_merges_tokens_and_uses_dtw():
    data = {"transcription": [{"tokens": [
        {"text": "[_BEG_]", "offsets": {"from": 0, "to": 0}, "t_dtw": -1},
        {"text": " Hel", "offsets": {"from": 0, "to": 300}, "t_dtw": 12},
        {"text": "lo", "offsets": {"from": 300, "to": 500}, "t_dtw": 30},
        {"text": " world.", "offsets": {"from": 500, "to": 1200}, "t_dtw": 70},
    ]}]}
    out = W.from_whisper_cpp(data)
    assert [w["w"] for w in out] == ["Hello", "world."]
    assert out[0]["start"] == 0.12
    assert out[0]["end"] == 0.5  # capped by token end, before next word's start
    assert out[1]["start"] == 0.7


def test_swear_matching_stems_exact_and_censored():
    sw = (["fuck", "shit"], {"ass", "piss"})
    assert W.is_swear("fucking,", sw)
    assert W.is_swear("Shitty", sw)
    assert W.is_swear("ass.", sw)
    assert not W.is_swear("assume", sw)
    assert not W.is_swear("passion", sw)
    assert W.is_swear("f***ing", sw)
    assert W.mask("fucking,") == "f******,"


def test_default_swear_list_leaves_mild_words():
    assert W.is_swear("fucking")
    assert not W.is_swear("bloody")
    assert not W.is_swear("prickly")


def test_subtract_and_remap_drop_cut_word():
    words = [wd("a", 0.0, 0.5), wd("fucking", 1.0, 1.4), wd("b", 1.5, 2.0), wd("c", 5.0, 5.5)]
    keep = [(0.0, 2.0), (5.0, 5.6)]  # silence 2.0-5.0 removed
    keep = W.subtract(keep, [(1.0, 1.4)])
    assert keep == [(0.0, 1.0), (1.4, 2.0), (5.0, 5.6)]
    out = W.remap(words, keep)
    assert [w["w"] for w in out] == ["a", "b", "c"]
    assert out[1]["start"] == 1.1  # 1.5 - 0.4 cut
    assert out[2]["start"] == 1.6  # 1.0 + 0.6 kept before it
    assert round(W.total(keep), 2) == 2.2


def test_speech_seconds_caps_long_gaps():
    words = [wd("a", 0, 1), wd("b", 4, 5)]
    assert W.speech_seconds(words, 0, 5, max_gap=0.35) == 2.35


def test_phrases_break_on_count_pause_and_sentence():
    ws = [wd("one", 0, .2), wd("two", .2, .4), wd("three.", .4, .6), wd("four", .7, .9),
          wd("five", .9, 1.1), wd("six", 1.1, 1.3), wd("seven", 1.3, 1.5), wd("eight", 1.5, 1.7),
          wd("nine", 3.0, 3.2)]
    ps = W.phrases(ws, max_words=4, gap=0.45)
    assert [[w["w"] for w in p["words"]] for p in ps] == [
        ["one", "two", "three."], ["four", "five", "six", "seven"], ["eight"], ["nine"]]
    assert ps[0]["end"] == 0.7  # held until the next phrase


def test_trim_leading_fillers_keeps_the_hook():
    ws = [wd("So,", 0, .2), wd("um", .3, .5), wd("I", .6, .7), wd("nearly", .7, 1)]
    assert [w["w"] for w in W.trim_leading_fillers(ws)] == ["I", "nearly"]
    assert W.trim_leading_fillers([wd("so", 0, 1)])[0]["w"] == "so"  # never empty


def test_from_whisperx_fills_gaps_and_clamps_to_silence():
    result = {"segments": [{"words": [
        {"word": "about", "start": 1.0, "end": 1.3},
        {"word": "6", },                                   # digits don't align
        {"word": "hours.", "start": 1.6, "end": 2.0},
        {"word": "Then", "start": 2.1, "end": 3.4},         # pulled early into the pause
    ]}]}
    out = W.from_whisperx(result, silences=[(2.0, 3.1)])
    assert out[1] == {"w": "6", "start": 1.3, "end": 1.6}
    assert out[3]["start"] == 3.1


def test_swear_span_snaps_to_quietest_audio():
    # quiet dips at 75.47 (before the f-) and 76.02 (before "disaster")
    dips = {75.47: 0.0, 76.02: 0.0}
    quietest = lambda a, b: min((t for t in dips if a <= t <= b), default=a)
    assert W.swear_spans([wd("fucking", 75.58, 75.94)], quietest=quietest) == [(75.44, 76.05)]
    assert W.swear_spans([wd("fucking", 75.58, 75.94)]) == [(75.46, 76.06)]  # no audio: padding


def test_fix_quote_timestamps_uses_transcript():
    ws = [wd("Every", 11.5, 11.6), wd("test", 11.6, 11.8), wd("I", 11.8, 11.9), wd("fixed", 11.9, 12.3),
          wd("broke", 12.3, 12.7)]
    md = '- "Every test I fixed broke 2 more." [00:14]\n- "Not in transcript" [01:00]'
    out = W.fix_quote_timestamps(md, ws)
    assert '- "Every test I fixed broke 2 more." [00:11]' in out
    assert '"Not in transcript" [01:00]' in out


def test_cut_span_never_reaches_into_the_next_word():
    # quiet point found inside "and" (the bug from the stress test) must be clamped
    ws = [wd("shit", 1.0, 1.3), wd("and", 1.35, 1.5), wd("bloody", 1.5, 1.9)]
    quietest = lambda a, b: b  # pretend the quietest moment is always at the far end
    (s, e), = W.swear_spans(ws, quietest=quietest)
    assert e <= 1.37


def test_fillers_get_cut_spans():
    ws = [wd("I", 0, .2), wd("um,", .3, .6), wd("think", .8, 1.0)]
    assert W.cut_spans(ws, W.is_filler) == [(0.18, 0.72)]
    assert not W.is_filler("so") and not W.is_filler("like")
