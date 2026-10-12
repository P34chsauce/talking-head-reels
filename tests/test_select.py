import pytest

from reels import select as S


def make_words(n_sentences=12, words_per=12, wdur=0.3):
    """Sentences of `words_per` words, 0.3 s each, 1 s pause between sentences."""
    out, t = [], 0.0
    for s in range(n_sentences):
        for i in range(words_per):
            w = f"w{s}_{i}" + ("." if i == words_per - 1 else "")
            out.append({"w": w, "start": round(t, 3), "end": round(t + wdur, 3)})
            t += wdur
        t += 1.0
    return out


def clip(fl, ll, **kw):
    base = {"title": "t", "first_line": fl, "last_line": ll, "why": "", "emojis": [],
            "cut_lines": [], "caption_fixes": [],
            "caption": "I nearly gave up. Here's what changed.",
            "hashtags": ["#buildinpublic", "#founderlife", "#holisticwellness"]}
    return {**base, **kw}


def test_lines_split_on_sentences():
    ls = S.lines(make_words(3))
    assert len(ls) == 3
    assert ls[0]["first"] == 0 and ls[0]["last"] == 11
    assert ls[0]["speech"] == 3.6
    assert "L2 [00:04.6-" in S.render_lines(ls)


def test_validate_accepts_good_plan_and_rejects_bad_ones():
    words = make_words(30)  # 30 lines of 3.6 s speech
    ls = S.lines(words)
    ok = {"slug": "a-b", "notes_md": "# x", "skipped": [], "clips": [clip(1, 10), clip(11, 22)]}
    assert S.validate(ok, words, ls) == []

    bad = {"slug": "a-b", "notes_md": "", "skipped": [], "clips": [
        clip(1, 3),                                    # too short
        clip(3, 14),                                   # overlaps clip 1
        clip(15, 25, caption="An app that treats stress"),
        clip(26, 30, emojis=[{"line": 2, "word": "x", "emoji": "🍑"}]),
    ]}
    errs = "\n".join(S.validate(bad, words, ls))
    assert "clip 1" in errs and "must be 30-60s" in errs
    assert "overlaps" in errs
    assert "'treats'" in errs
    assert "not on a kept line of the clip" in errs


def test_validate_rejects_spoken_claims():
    words = make_words(30)
    words[5]["w"] = "cured"
    ls = S.lines(words)
    plan = {"slug": "a-b", "notes_md": "", "skipped": [], "clips": [clip(1, 10)]}
    assert "reads as a claim" in "\n".join(S.validate(plan, words, ls))


def test_plan_clips_feeds_errors_back_then_succeeds():
    words = make_words(30)
    prompts = []
    plans = iter([
        {"slug": "a-b", "notes_md": "", "skipped": [], "clips": [clip(1, 2)]},
        {"slug": "a-b", "notes_md": "", "skipped": [], "clips": [clip(1, 10)]},
    ])

    def fake(prompt):
        prompts.append(prompt)
        return next(plans)

    plan, _ = S.plan_clips(words, {"name": "v.mp4", "date": "2026-10-01", "duration": 140}, ask=fake)
    assert plan["clips"][0]["last_line"] == 10
    assert prompts[0].startswith("/make-reels")
    assert "rejected by the validator" in prompts[1] and "must be 30-60s" in prompts[1]


def test_too_little_speech_is_permanent():
    with pytest.raises(S.PermanentError):
        S.plan_clips(make_words(2), {"name": "v", "date": "d", "duration": 9}, ask=lambda p: {})


def test_child_env_strips_session_and_api_vars(monkeypatch):
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-never-pass")
    env = S._child_env()
    assert "CLAUDECODE" not in env and "ANTHROPIC_API_KEY" not in env
    assert "PATH" in env


def test_too_long_feedback_offers_ranges_that_fit():
    words = make_words(30)  # each line 3.6 s + 0.35 s gap
    ls = S.lines(words)
    plan = {"slug": "a-b", "notes_md": "", "skipped": [], "clips": [clip(1, 20)]}
    err = S.validate(plan, words, ls)[0]
    assert "Nearby ranges that fit:" in err
    import re
    for a, b in re.findall(r"L(\d+)-L(\d+) \(", err.split("fit:")[1]):
        assert 30 <= S.span_seconds(words, ls, int(a), int(b)) <= 60


def test_keeps_valid_clips_when_one_never_fixes():
    words = make_words(30)
    bad = {"slug": "a-b", "notes_md": "", "skipped": [], "clips": [clip(1, 10), clip(11, 30)]}
    plan, _ = S.plan_clips(words, {"name": "v", "date": "d", "duration": 140}, ask=lambda p: bad)
    assert [(c["first_line"], c["last_line"]) for c in plan["clips"]] == [(1, 10)]
    assert any("dropped by validator" in s for s in plan["skipped"])


def test_claude_never_inherits_stdin(monkeypatch):
    seen = {}

    def fake_run(cmd, **kw):
        seen.update(kw)
        class R: returncode, stdout, stderr = 0, '{"structured_output": {"ok": 1}}', ""
        return R()
    monkeypatch.setattr(S.subprocess, "run", fake_run)
    assert S.ask_claude("x") == {"ok": 1}
    assert seen["stdin"] is S.subprocess.DEVNULL


def test_cut_lines_shorten_the_clip_and_hide_claims():
    words = make_words(30)
    words[5 * 12 + 3]["w"] = "cured"          # a claim on L6
    ls = S.lines(words)
    whole = {"slug": "a-b", "notes_md": "", "skipped": [], "clips": [clip(1, 12)]}
    errs = "\n".join(S.validate(whole, words, ls))
    assert "reads as a claim" in errs
    cut = {"slug": "a-b", "notes_md": "", "skipped": [], "clips": [clip(1, 12, cut_lines=[6])]}
    assert S.validate(cut, words, ls) == []
    assert S.span_seconds(words, ls, 1, 12, [6]) < S.span_seconds(words, ls, 1, 12)


def test_cut_lines_must_be_inside_and_fixes_must_match():
    words = make_words(30)
    ls = S.lines(words)
    plan = {"slug": "a-b", "notes_md": "", "skipped": [], "clips": [
        clip(1, 10, cut_lines=[1]),
        clip(11, 20, caption_fixes=[{"line": 12, "wrong": "nope", "right": "x"}]),
        clip(21, 30, caption_fixes=[{"line": 22, "wrong": "w21_0", "right": "a b c d"}]),
    ]}
    errs = "\n".join(S.validate(plan, words, ls))
    assert "strictly inside" in errs
    assert "'nope' is not a word in L12" in errs
    assert "must be 1-3 words" in errs


def test_fillers_do_not_count_towards_length():
    words = make_words(30)
    ls = S.lines(words)
    base = S.span_seconds(words, ls, 1, 10)
    for w in words[:24]:
        if w["w"].startswith("w0_"):
            w["w"] = "um"
    assert S.span_seconds(words, ls, 1, 10) < base


def test_prompt_carries_the_channel_brief_without_comments():
    from reels import config as C
    C.PRIVATE.mkdir()
    (C.PRIVATE / "channel.md").write_text("<!-- how to edit -->\n**Channel:** cooking at home.\n")
    ls = S.lines([{"w": "Hello.", "start": 0.0, "end": 0.5}])
    p = S.build_prompt(ls, {"name": "take.mp4", "date": "2026-10-12", "duration": 1.0})
    assert "## Channel\n\n**Channel:** cooking at home.\n\n## Recording" in p
    assert "how to edit" not in p
