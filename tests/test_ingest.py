import os
import time

from reels import ingest


def test_candidate_filters_syncthing_temp_and_dotfiles(tmp_path):
    names = {
        "VID_20261001_120000.mp4": True,
        "clip.MOV": True,
        ".syncthing.VID_1.mp4.tmp": False,
        "~syncthing~VID_1.mp4.tmp": False,
        ".hidden.mp4": False,
        "VID_1.mp4.error.log": False,
        "notes.txt": False,
    }
    for n in names:
        (tmp_path / n).write_bytes(b"x")
    for n, want in names.items():
        assert ingest.is_candidate(tmp_path / n) is want, n


def test_settled_requires_old_and_unchanged(tmp_path):
    p = tmp_path / "a.mp4"
    p.write_bytes(b"1234")
    assert not ingest.settled(p, wait=10, sleep=lambda s: None)  # just written
    old = time.time() - 60
    os.utime(p, (old, old))
    assert ingest.settled(p, wait=10, sleep=lambda s: None)

    def grow(_):
        with p.open("ab") as f:
            f.write(b"more")
    assert not ingest.settled(p, wait=10, sleep=grow)  # still syncing


def test_settled_rejects_empty_and_missing(tmp_path):
    p = tmp_path / "empty.mp4"
    p.write_bytes(b"")
    old = time.time() - 60
    os.utime(p, (old, old))
    assert not ingest.settled(p, wait=1, sleep=lambda s: None)
    assert not ingest.settled(tmp_path / "gone.mp4", wait=1, sleep=lambda s: None)


def test_truncated_mp4_fails_probe(tmp_path):
    p = tmp_path / "half.mp4"
    p.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 5000)
    assert not ingest.probe_ok(p)


def test_attempts_and_gave_up(tmp_path, monkeypatch):
    monkeypatch.setattr(ingest.C, "MAX_ATTEMPTS", 2)
    p = tmp_path / "v.mp4"
    log = ingest.error_log(p)
    log.write_text("=== attempt 1\ntransient: usage limit\n")
    assert ingest.attempts(p) == 1 and not ingest.gave_up(p)
    log.write_text(log.read_text() + "=== attempt 2\ntransient\n")
    assert ingest.gave_up(p)
    log.write_text("=== attempt 1\nPERMANENT: no speech found\n")
    assert ingest.gave_up(p)


def test_envelope_finds_the_quiet_gap(tmp_path):
    import array
    import wave
    from reels.media import Envelope
    sr = 16000
    loud = [8000 if i % 2 else -8000 for i in range(sr // 2)]
    quiet = [0] * (sr // 10)
    p = tmp_path / "a.wav"
    with wave.open(str(p), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
        w.writeframes(array.array("h", loud + quiet + loud).tobytes())
    t = Envelope(p).quietest(0.3, 0.8)
    assert 0.5 <= t <= 0.6
