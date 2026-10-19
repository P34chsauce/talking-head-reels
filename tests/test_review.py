import json

import pytest

from reels import config as C
from reels import ingest
from reels import pipeline as P
from reels import review as R
from test_cutaways import clip as make_clip, take


@pytest.fixture
def preview(tmp_path, monkeypatch):
    """A previews/<run>/ folder with one 10-line clip, as `reels preview` leaves it."""
    for name in ("WORK", "READY", "STATE", "ARCHIVE"):
        monkeypatch.setattr(C, name, tmp_path / name.lower())
        (tmp_path / name.lower()).mkdir()
    monkeypatch.setattr(P, "PREVIEWS", tmp_path / "previews")
    monkeypatch.setattr(ingest, "LEDGER", C.STATE / "processed.jsonl")
    monkeypatch.setattr(R.A, "entries", lambda: [])
    monkeypatch.setattr(R.A, "menu", lambda when: [])
    d = P.PREVIEWS / "2026-10-12_take_abc-20261019-120000"
    (d / "clips" / "c1").mkdir(parents=True)
    words = take(n_lines=12)
    (d / "words.json").write_text(json.dumps(words))
    from reels import select as S
    (d / "lines.json").write_text(json.dumps(S.lines(words)))
    (d / "job.json").write_text(json.dumps({"src": "x.mp4", "name": "x.mp4", "date": "2026-10-12", "duration": 60}))
    (d / "plan.json").write_text(json.dumps({"slug": "a-b", "notes_md": "# n", "skipped": [],
                                             "clips": [make_clip(title="Old title")]}))
    (d / "1-old-title.mp4").write_bytes(b"old reel")
    return d


def test_finds_preview_and_ready_reels(preview):
    job = C.WORK / "2026-10-12_take_abc"
    job.mkdir()
    (job / "plan.json").write_text((preview / "plan.json").read_text())
    (C.READY / "2026-10-12_a-b_1-old-title.mp4").write_bytes(b"r")
    ingest.record({"sha256": "s", "status": "done", "job": job.name, "outputs": ["2026-10-12_a-b_1-old-title.mp4"]})
    ingest.record({"sha256": "t", "status": "done", "kind": "asset", "job": "asset x", "outputs": []})
    found = {r.id: r for r in R.reels()}
    assert set(found) == {f"preview~{preview.name}~1", f"ready~{job.name}~1"}
    assert found[f"ready~{job.name}~1"].txt == C.READY / "2026-10-12_a-b_1-old-title.txt"


def test_comment_apply_accept(preview):
    r = R.reels()[0]
    R.add_comment(r, 9.2, "swap this for the garden")
    seen = []

    def ask(prompt):
        seen.append(prompt)
        return {**make_clip(title="New title"), "summary": "0:09: renamed."}

    def render():
        (preview / "clips" / "c1" / "final.mp4").write_bytes(b"new reel")

    R.apply(r, r.clip(), ask=ask, render=render)
    assert R.STATUS[r.id] == {"state": "ready", "summary": "0:09: renamed."}
    assert "at 0:09.2: swap this for the garden" in seen[0] and '"title": "Old title"' in seen[0]
    assert r.clip()["title"] == "New title"  # the plan carries the new edit
    assert r.mp4.read_bytes() == b"old reel"  # untouched until accepted
    R.accept(r)
    assert r.mp4.read_bytes() == b"new reel"
    assert all(c["done"] for c in R.comments(r))
    rev = next((r.rdir / "revisions").iterdir())
    assert (rev / "reel.mp4").read_bytes() == b"old reel"
    assert json.loads((rev / "clip.json").read_text())["title"] == "Old title"


def test_revert_restores_the_old_edit(preview):
    r = R.reels()[0]
    R.apply(r, make_clip(title="Direct edit"), render=lambda: (preview / "clips" / "c1" / "final.mp4").write_bytes(b"n"))
    assert R.STATUS[r.id]["state"] == "ready" and r.clip()["title"] == "Direct edit"
    R.revert(r)
    assert r.clip()["title"] == "Old title" and r.mp4.read_bytes() == b"old reel"
    assert not (r.rdir / "candidate.mp4").exists() and not list((r.rdir / "revisions").iterdir())


def test_invalid_direct_edit_is_refused_without_rendering(preview):
    r = R.reels()[0]
    bad = make_clip(visuals=[{"from_line": 3, "to_line": 3, "asset": "nope", "at": 0, "mode": "full", "why": ""}])
    R.apply(r, bad, render=lambda: pytest.fail("must not render"))
    assert R.STATUS[r.id]["state"] == "failed" and "no footage was offered" in R.STATUS[r.id]["error"]
    assert r.clip()["title"] == "Old title"


def test_bait_in_the_caption_is_refused(preview):
    r = R.reels()[0]
    R.apply(r, make_clip(caption="Comment YES if you agree!"), render=lambda: pytest.fail("must not render"))
    assert R.STATUS[r.id]["state"] == "failed"


def test_only_reel_and_library_media_are_served(preview, tmp_path):
    assert R.servable(preview / "1-old-title.mp4")
    secret = tmp_path / "secret.mp4"
    secret.write_bytes(b"x")
    assert not R.servable(secret)
    assert not R.servable(preview / "plan.json")
    assert not R.servable(preview / ".." / ".." / "secret.mp4")
