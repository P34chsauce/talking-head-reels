from reels import pipeline as P


def wd(w, s, e, i):
    return {"w": w, "start": s, "end": e, "i": i}


def test_caption_fix_keeps_timing_and_punctuation():
    ls = [{"first": 0, "last": 2}]
    edited = [wd("I", 0, .2, 0), wd("had", .2, .4, 1), wd("to,", .4, .6, 2)]
    out = P.apply_caption_fixes(edited, ls, [{"line": 1, "wrong": "to", "right": "the"}])
    assert out[2] == {"w": "the,", "start": .4, "end": .6, "i": 2}
    assert edited[2]["w"] == "to,"  # input untouched


def test_consecutive_cut_lines_merge_into_one_cut():
    words = [wd(f"w{i}", i * 1.0, i * 1.0 + 0.5, i) for i in range(6)]
    ls = [{"first": 0, "last": 1}, {"first": 2, "last": 2}, {"first": 3, "last": 3}, {"first": 4, "last": 5}]
    quietest = lambda a, b: (a + b) / 2
    cuts = P.line_cuts(words, ls, [2, 3], quietest)
    assert len(cuts) == 1
    s, e = cuts[0]
    assert 1.5 <= s <= 2.0 and 3.5 <= e <= 4.0  # from after w1 to before w4


def test_reprocess_moves_take_back_and_keeps_previous_outputs(tmp_path, monkeypatch):
    import json
    from reels import config as C, ingest
    for name in ("WORK", "READY", "INBOX", "NOTES_DIR", "STATE"):
        monkeypatch.setattr(C, name, tmp_path / name.lower())
        (tmp_path / name.lower()).mkdir()
    monkeypatch.setattr(ingest, "LEDGER", tmp_path / "state" / "processed.jsonl")
    src = tmp_path / "take.mp4"
    src.write_bytes(b"video")
    job = C.WORK / "2026-10-02_take_abc"
    job.mkdir()
    (job / "job.json").write_text(json.dumps({"date": "2026-10-02"}))
    (job / "plan.json").write_text(json.dumps({"slug": "old-slug"}))
    (job / "words.json").write_text("[]")
    (C.READY / "old_1.mp4").write_bytes(b"r")
    (C.READY / "old_1.txt").write_text("c")
    (C.NOTES_DIR / "2026-10-02_old-slug.md").write_text("n")
    ingest.record({"sha256": ingest.sha256(src), "status": "done", "job": job.name, "outputs": ["old_1.mp4"]})
    ingest.record({"sha256": "other", "status": "done", "job": "x", "outputs": []})

    prev = P.reprocess(src)
    assert (C.INBOX / "take.mp4").exists() and not src.exists()
    assert sorted(p.name for p in prev.iterdir()) == ["2026-10-02_old-slug.md", "old_1.mp4", "old_1.txt", "plan.json"]
    assert (job / "words.json").exists()  # transcript reused unless --retranscribe
    assert list(ingest.ledger()) == ["other"]
