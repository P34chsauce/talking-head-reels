"""Which inbox files are safe to process: fully synced, not seen before, not failed for good."""
from __future__ import annotations

import hashlib
import json
import subprocess
import time
from pathlib import Path

from . import config as C


def is_candidate(p: Path) -> bool:
    """Ignore Syncthing temp files (.syncthing.x.tmp, ~syncthing~x.tmp), dotfiles and non-video."""
    n = p.name
    if n.startswith(".") or n.startswith("~") or n.endswith(".tmp") or ".syncthing." in n:
        return False
    return p.is_file() and p.suffix.lower() in C.VIDEO_EXTS


def settled(p: Path, wait: float = C.SETTLE_SECONDS, sleep=time.sleep) -> bool:
    """Size and mtime unchanged across `wait` seconds, and older than `wait` already."""
    try:
        a = p.stat()
        if time.time() - a.st_mtime < wait:
            return False
        sleep(wait)
        b = p.stat()
    except FileNotFoundError:
        return False
    return (a.st_size, a.st_mtime) == (b.st_size, b.st_mtime) and a.st_size > 0


def probe_ok(p: Path) -> bool:
    """ffprobe can read a duration and a video stream (a truncated MP4 has no moov atom)."""
    r = subprocess.run(
        [C.FFPROBE, "-v", "error", "-select_streams", "v:0", "-show_entries",
         "format=duration:stream=codec_type", "-of", "json", str(p)],
        capture_output=True, text=True)
    if r.returncode != 0:
        return False
    try:
        d = json.loads(r.stdout)
        return float(d["format"]["duration"]) > 1 and bool(d.get("streams"))
    except (KeyError, ValueError, json.JSONDecodeError):
        return False


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# --- ledger: content hash -> outcome, so a re-synced or reverted file is never redone ---

LEDGER = C.STATE / "processed.jsonl"


def ledger() -> dict[str, dict]:
    out = {}
    if LEDGER.exists():
        for line in LEDGER.read_text().splitlines():
            if line.strip():
                rec = json.loads(line)
                out[rec["sha256"]] = rec
    return out


def record(rec: dict) -> None:
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("a") as f:
        f.write(json.dumps(rec) + "\n")


def error_log(p: Path) -> Path:
    return p.with_name(p.name + ".error.log")


def attempts(p: Path) -> int:
    log = error_log(p)
    if not log.exists():
        return 0
    return sum(1 for line in log.read_text().splitlines() if line.startswith("=== attempt"))


def due_for_retry(p: Path, now: float | None = None) -> bool:
    """No error log, or the backoff since the last failed attempt has passed."""
    log = error_log(p)
    if not log.exists():
        return True
    n = attempts(p)
    wait_h = 2 ** max(0, n - 1)
    return (now or time.time()) - log.stat().st_mtime >= wait_h * 3600


def gave_up(p: Path) -> bool:
    """A permanent failure, or transient ones that hit MAX_ATTEMPTS. `reels retry` clears it."""
    log = error_log(p)
    if not log.exists():
        return False
    text = log.read_text()
    return "PERMANENT" in text or attempts(p) >= C.MAX_ATTEMPTS
