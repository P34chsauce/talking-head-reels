import datetime as dt
import json

from reels import assets as A
from reels import config as C
from reels import pipeline as P
from reels import select as S


def word(w, s, e, i):
    return {"w": w, "start": s, "end": e, "i": i}


def take(n_lines=12, words_per_line=8, wlen=0.5):
    """n lines of evenly spaced words, each line ending in a full stop: ~4 s per line."""
    words, t = [], 0.0
    for l in range(n_lines):
        for k in range(words_per_line):
            words.append(word(f"w{l}_{k}" + ("." if k == words_per_line - 1 else ""), t, t + wlen - 0.05, len(words)))
            t += wlen
        t += 0.3
    return words


def asset(id_, duration=10.0, kind="broll", quality="good", recorded="2026-10-19T15:00:00", **over):
    return {"id": id_, "sha256": id_ * 4, "path": f"/nowhere/{id_}.mp4", "name": f"{id_}.mp4", "kind": kind,
            "duration": duration, "width": 1080, "height": 1920, "orientation": "portrait", "has_audio": False,
            "quality": quality, "recorded_at": recorded, "description": f"footage {id_}", "tags": ["x"],
            "moments": [], "issues": [], "privacy": [], **over}


def clip(**over):
    return {"title": "t", "first_line": 1, "last_line": 10, "why": "", "emojis": [], "cut_lines": [],
            "caption_fixes": [], "visuals": [], "caption": "A line.\nSend this to a friend who needs it.",
            "hashtags": ["#a", "#b", "#c"], **over}


def visual(**over):
    return {"from_line": 3, "to_line": 3, "asset": "aaa", "at": 1.0, "mode": "full", "why": "", **over}


def errs_for(c, assets):
    words = take()
    ls = S.lines(words)
    return S.validate_visuals("clip 1", c, words, ls, {a["id"]: a for a in assets})


def test_a_good_cutaway_passes():
    assert errs_for(clip(visuals=[visual()]), [asset("aaa")]) == []


def test_unknown_asset_and_no_footage_are_rejected():
    assert "not one of the offered" in errs_for(clip(visuals=[visual(asset="zzz")]), [asset("aaa")])[0]
    assert "no footage was offered" in errs_for(clip(visuals=[visual()]), [])[0]


def test_cutaway_must_sit_on_kept_lines_inside_the_clip():
    assert "inside the clip" in errs_for(clip(visuals=[visual(from_line=9, to_line=11)]), [asset("aaa")])[0]
    assert "cut line" in errs_for(clip(cut_lines=[3], visuals=[visual()]), [asset("aaa")])[0]


def test_overlapping_cutaways_are_rejected():
    vs = [visual(to_line=4), visual(from_line=4, to_line=4, asset="bbb")]
    assert any("overlaps" in e for e in errs_for(clip(visuals=vs), [asset("aaa"), asset("bbb")]))


def test_footage_too_short_for_the_lines_says_where_to_start():
    e = errs_for(clip(visuals=[visual(at=7.0)]), [asset("aaa", duration=10.0)])
    assert len(e) == 1 and "start earlier" in e[0]
    assert errs_for(clip(visuals=[visual(at=12.0)]), [asset("aaa", duration=10.0)])[0].count("outside")


def test_stills_have_no_footage_length_limit():
    assert errs_for(clip(visuals=[visual(at=0, from_line=2, to_line=2)]), [asset("aaa", duration=None, kind="photo")]) == []


def test_plan_keeps_a_clip_without_its_cutaways_when_only_they_fail():
    words = take(n_lines=14)
    bad = {"slug": "a-b", "notes_md": "# x", "skipped": [],
           "clips": [clip(last_line=12, visuals=[visual(asset="nope")])]}
    plan, _ = S.plan_clips(words, {"name": "t.mp4", "date": "2026-10-19", "duration": 70},
                           ask=lambda prompt: bad, assets=[asset("aaa")], footage="aaa | broll")
    assert len(plan["clips"]) == 1 and plan["clips"][0]["visuals"] == []


def test_prompt_without_footage_is_unchanged():
    ls = S.lines(take(n_lines=3))
    meta = {"name": "t.mp4", "date": "2026-10-19", "duration": 20}
    p = S.build_prompt(ls, meta)
    assert "Footage" not in p
    assert S.build_prompt(ls, meta, footage="aaa | broll").endswith("## Footage you can cut to\n\naaa | broll\n")


def test_menu_puts_linked_extras_first_and_drops_poor_ones(monkeypatch):
    lib = [asset("old", recorded="2026-10-01T09:00:00"), asset("new", recorded="2026-10-18T09:00:00"),
           asset("linked", recorded="2026-10-19T15:30:00"), asset("bad", quality="poor", recorded="2026-10-19T15:10:00")]
    monkeypatch.setattr(A, "entries", lambda: sorted(lib, key=lambda e: e["recorded_at"]))
    ids = [e["id"] for e in A.menu(dt.datetime(2026, 10, 19, 15, 0))]
    assert ids == ["linked", "new", "old"]
    text = A.menu_text(A.menu(dt.datetime(2026, 10, 19, 15, 0)), {"linked"})
    assert "linked | broll | 10.0s video | portrait | quality good | recorded with this take" in text


def test_cutaways_off_offers_nothing(monkeypatch):
    monkeypatch.setattr(C, "CUTAWAYS", False)
    assert P.offered_footage({"recorded_at": "2026-10-19T15:00:00"}) == ([], "")


def test_cutaway_timing_follows_the_edited_words(tmp_path, monkeypatch):
    words = take(n_lines=6)
    ls = S.lines(words)
    edited = [{**w, "start": round(w["start"] / 2, 3), "end": round(w["end"] / 2, 3)} for w in words]  # pretend the edit halved time
    src = tmp_path / "r.mp4"
    src.write_bytes(b"x")
    e = asset("aaa", duration=3.0, path=str(src))
    monkeypatch.setattr(A, "load", lambda: {"k": e})
    monkeypatch.setattr(A, "render_source", lambda e: src)
    props, files = P.cutaway_props({"visuals": [visual(from_line=2, to_line=3, at=2.5)]}, ls, edited, length=20.0)
    line2_first = edited[ls[1]["first"]]["start"]
    line4_first = edited[ls[3]["first"]]["start"]
    v = props[0]
    assert v["start"] == round(line2_first - 0.1, 3)
    # wanted L2 through the start of L4, but the footage only has 3 s: `at` is pulled back to 0,
    # and the cutaway ends when the footage does
    assert v["at"] == 0.0 and v["end"] == round(v["start"] + 3.0, 3) < line4_first
    assert files == [(src, "cut1.mp4")] and v["file"] == "cut1.mp4" and v["kind"] == "video"


def test_missing_asset_is_skipped_at_render(monkeypatch):
    monkeypatch.setattr(A, "load", lambda: {})
    words = take(n_lines=4)
    props, files = P.cutaway_props({"visuals": [visual(from_line=2, to_line=2)]}, S.lines(words), words, 20.0)
    assert props == [] and files == []


def test_face_box_finds_no_face_in_a_test_pattern(tmp_path):
    import shutil
    import pytest
    from reels import media as M
    from test_assets import make_video
    if not shutil.which("swiftc"):
        pytest.skip("no Swift toolchain: the face bubble falls back to style.faceCenter")
    assert M.face_box(make_video(tmp_path / "pattern.mp4", secs=2), tmp_path) is None
    assert not list(tmp_path.glob("face*.jpg"))  # sampled frames are cleaned up


def test_long_cutaways_are_rejected():
    e = errs_for(clip(visuals=[visual(at=0, from_line=2, to_line=5)]), [asset("aaa", duration=None, kind="photo")])
    assert len(e) == 1 and "under 7s" in e[0]
