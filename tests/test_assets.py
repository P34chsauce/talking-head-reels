import datetime as dt
import json
import os
import subprocess

import pytest

from reels import assets as A
from reels import config as C
from reels import ingest

FF = C.FFMPEG


def make_video(path, size="1080x1920", secs=4, audio=False, extra=()):
    cmd = [FF, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=s={size}:r=30:d={secs}"]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=f=440:d={secs}", "-c:a", "aac"]
    subprocess.run([*cmd, *extra, "-c:v", "libx264", "-pix_fmt", "yuv420p", "-shortest", str(path)], check=True)
    return path


def make_image(path, size="1170x2532"):
    subprocess.run([FF, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=s={size}",
                    "-frames:v", "1", str(path)], check=True)
    return path


def fake_ask(kind="broll", calls=None, **over):
    def ask(prompt, schema=None, tools=None):
        if calls is not None:
            calls.append((prompt, tools))
        return {"kind": kind, "description": "a walk in the park at dusk", "tags": ["walk", "park", "dusk"],
                "moments": [] if kind in ("photo", "screenshot") else [{"t": 1.0, "what": "trees"}],
                "quality": "good", "issues": [], "privacy": [], **over}
    return ask


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    for name in ("WORK", "INBOX", "ARCHIVE", "STATE", "LIBRARY"):
        monkeypatch.setattr(C, name, tmp_path / name.lower())
        (tmp_path / name.lower()).mkdir()
    monkeypatch.setattr(C, "EXTRAS", C.ARCHIVE / "extras")
    monkeypatch.setattr(C, "CATALOG", C.STATE / "catalog.jsonl")
    monkeypatch.setattr(A, "LIB_INDEX", C.STATE / "library-index.json")
    monkeypatch.setattr(ingest, "LEDGER", C.STATE / "processed.jsonl")
    return tmp_path


@pytest.mark.parametrize("name, want", [
    ("VID_20261018_190812.mp4", dt.datetime(2026, 10, 18, 19, 8, 12)),
    ("20261018_070102.jpg", dt.datetime(2026, 10, 18, 7, 1, 2)),
    ("Screenshot_20261018-190812_Notes.png", dt.datetime(2026, 10, 18, 19, 8, 12)),
    ("Screen_Recording_20261018_190812_App.mp4", dt.datetime(2026, 10, 18, 19, 8, 12)),
])
def test_recorded_at_reads_phone_file_names(tmp_path, name, want):
    p = tmp_path / name
    p.write_bytes(b"x")
    assert A.recorded_at(p) == want


def test_recorded_at_falls_back_to_mtime(tmp_path):
    p = tmp_path / "walk.jpg"
    p.write_bytes(b"not really an image")
    os.utime(p, (1760000000, 1760000000))
    assert A.recorded_at(p) == dt.datetime.fromtimestamp(1760000000)


def test_inspect_applies_phone_rotation(tmp_path):
    src = make_video(tmp_path / "sideways.mp4", size="640x360", secs=1)
    rotated = tmp_path / "rotated.mp4"
    subprocess.run([FF, "-hide_banner", "-loglevel", "error", "-y", "-display_rotation", "90", "-i", src,
                    "-c", "copy", rotated], check=True)
    assert A.inspect(src)["width"] == 640
    info = A.inspect(rotated)
    assert (info["width"], info["height"], info["has_audio"]) == (360, 640, False)


def test_only_long_videos_with_sound_can_be_takes(tmp_path):
    assert A.not_a_take({"has_audio": False, "duration": 300})
    assert A.not_a_take({"has_audio": True, "duration": 12})
    assert not A.not_a_take({"has_audio": True, "duration": 90})


def test_contact_sheet_has_every_frame_stamped(tmp_path):
    src = make_video(tmp_path / "b.mp4", secs=6)
    info = A.inspect(src)
    sheet, times = A.contact_sheet(src, tmp_path, info)
    assert times == [0.5, 1.5, 2.5, 3.5, 4.5, 5.5]
    assert A.inspect(sheet)["width"] == 1080  # 3 x 360
    assert A.inspect(sheet)["height"] == 720  # 2 x 360


def test_still_image_gets_a_jpeg_proxy(tmp_path):
    src = make_image(tmp_path / "shot.png")
    sheet, times = A.contact_sheet(src, tmp_path, A.inspect(src))
    assert sheet.name == "image.jpg" and times == []
    info = A.inspect(sheet)
    assert max(info["width"], info["height"]) <= 1920


def test_validate_rejects_impossible_descriptions():
    video = {"duration": 4.0}
    still = {"duration": None}
    good = fake_ask()("p")
    assert A.validate(good, video) == []
    assert any("still image" in e for e in A.validate(good, still))
    assert any("outside" in e for e in A.validate({**good, "moments": [{"t": 9, "what": "x"}]}, video))
    assert any("video is broll" in e for e in A.validate({**good, "kind": "photo", "moments": []}, video))


def test_inbox_extra_is_described_moved_and_catalogued(dirs):
    src = make_image(C.INBOX / "Screenshot_20261018_190812_App.png")
    calls = []
    e = A.add(src, "inbox", ask=fake_ask("screenshot", calls))
    assert not src.exists()
    assert (C.EXTRAS / src.name).exists()
    assert e["kind"] == "screenshot" and e["recorded_at"] == "2026-10-18T19:08:12"
    assert e["orientation"] == "portrait" and e["duration"] is None
    prompt, tools = calls[0]
    assert "Still image" in prompt and "screenshot" in prompt
    assert tools.startswith("Skill,Read(") and str(C.WORK / "assets") in tools
    assert A.path_of(e) == (C.EXTRAS / src.name).resolve()
    assert [x["id"] for x in A.entries()] == [e["id"]]


def test_near_finds_extras_recorded_around_a_take(dirs):
    ask = fake_ask("photo")
    for k, name in enumerate(("20261018_090000.png", "20261018_120000.png", "20261019_090000.png")):
        A.add(make_image(C.INBOX / name, size=f"64x{40 + 2 * k}"), "inbox", ask=ask)  # sizes differ: same bytes = same asset
    hits = A.near(dt.datetime(2026, 10, 18, 10, 30))
    assert [h["name"] for h in hits] == ["20261018_090000.png", "20261018_120000.png"]


def test_search_matches_every_word(dirs):
    A.add(make_image(C.INBOX / "a.png", size="64x64"), "inbox", ask=fake_ask("photo"))
    A.add(make_image(C.INBOX / "b.png", size="64x32"), "inbox",
          ask=fake_ask("photo", description="a laptop on a desk at night", tags=["laptop", "desk", "night"]))
    assert [e["name"] for e in A.search("desk night")] == ["b.png"]
    assert [e["name"] for e in A.search("PARK")] == ["a.png"]


def test_library_scan_describes_each_file_once_and_leaves_it_in_place(dirs):
    (C.LIBRARY / "walks").mkdir()
    clip = make_video(C.LIBRARY / "walks" / "park.mp4", secs=2)
    make_image(C.LIBRARY / ".hidden.png", size="32x32")
    calls = []
    assert A.scan_library(wait=False, ask=fake_ask(calls=calls), log=lambda m: None) == []
    assert len(calls) == 1 and clip.exists()
    assert A.entries()[0]["origin"] == "library"
    assert A.scan_library(wait=False, ask=fake_ask(calls=calls), log=lambda m: None) == []
    assert len(calls) == 1  # already catalogued: no second claude call


def test_library_scan_reports_failures_without_stopping(dirs):
    make_image(C.LIBRARY / "a.png", size="32x32")
    make_image(C.LIBRARY / "b.png", size="32x32")
    def ask(prompt, schema=None, tools=None):
        if "a.png" in prompt:
            raise RuntimeError("boom")
        return fake_ask("photo")(prompt)
    failed = A.scan_library(wait=False, ask=ask, log=lambda m: None)
    assert [p.name for p, _ in failed] == ["a.png"]
    assert [e["name"] for e in A.entries()] == ["b.png"]


def test_library_error_logs_stay_out_of_the_synced_folder(dirs):
    p = C.LIBRARY / "walks" / "park.mp4"
    assert ingest.error_log(p) == C.STATE / "errors" / "library__walks__park.mp4.error.log"
    assert ingest.error_log(C.INBOX / "take.mp4") == C.INBOX / "take.mp4.error.log"


def test_duplicate_of_a_library_asset_is_archived_not_described_again(dirs):
    lib = make_image(C.LIBRARY / "a.png", size="32x32")
    calls = []
    A.scan_library(wait=False, ask=fake_ask("photo", calls), log=lambda m: None)
    copy = C.INBOX / "a-copy.png"
    copy.write_bytes(lib.read_bytes())
    e = A.add(copy, "inbox", ask=fake_ask("photo", calls))
    assert len(calls) == 1 and e["origin"] == "library"
    assert not copy.exists() and (C.EXTRAS / "a-copy.png").exists()


def test_pipeline_sends_images_and_silent_video_to_the_library(dirs, monkeypatch):
    from reels import pipeline as P
    monkeypatch.setattr(A.S, "ask_claude", fake_ask())
    shot = make_image(C.INBOX / "shot.png", size="64x64")
    broll = make_video(C.INBOX / "walk.mp4", secs=2)
    monkeypatch.setattr(A.S, "ask_claude", fake_ask("photo"))
    assert P.process(shot, wait=False).startswith("done:photo")
    monkeypatch.setattr(A.S, "ask_claude", fake_ask("broll"))
    assert P.process(broll, wait=False).startswith("done:broll")
    assert sorted(p.name for p in C.EXTRAS.iterdir()) == ["shot.png", "walk.mp4"]
    assert [json.loads(l)["kind"] for l in ingest.LEDGER.read_text().splitlines()] == ["asset", "asset"]
    assert not list(C.WORK.glob("20*"))  # no take job was started
