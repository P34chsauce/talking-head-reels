"""Paths, tool locations and tunables. Everything else imports from here."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INBOX = ROOT / "inbox"
WORK = ROOT / "work"
READY = ROOT / "ready"
ARCHIVE = ROOT / "archive"
MODELS = ROOT / "models"
LOGS = ROOT / "logs"
STATE = ROOT / "state"
CONFIG = ROOT / "config"
PRIVATE = ROOT / "private"  # gitignored: your own channel brief, rules and settings (see README)
CAPTIONS = ROOT / "captions"
FONT = ROOT / "assets" / "fonts" / "AtkinsonHyperlegible-Bold.ttf"


def config_file(name: str) -> Path:
    """private/<name> when you have one, else the default in config/."""
    p = PRIVATE / name
    return p if p.exists() else CONFIG / name


def _json(name: str) -> dict:
    """config/<name> with any keys from private/<name> on top."""
    d = json.loads((CONFIG / name).read_text())
    if (PRIVATE / name).exists():
        d.update(json.loads((PRIVATE / name).read_text()))
    return d


def _path(p: str) -> Path:
    return (ROOT / Path(p).expanduser()).resolve()  # absolute paths and ~ win over ROOT


SETTINGS = _json("settings.json")
NOTES_DIR = _path(SETTINGS["notes_dir"])  # session notes and talking points (Markdown)
PROJECT_REPO = _path(SETTINGS["project_repo"]) if SETTINGS.get("project_repo") else None  # read-only
PROJECT_BRANCH = SETTINGS.get("project_branch", "main")
CUTAWAYS = SETTINGS.get("cutaways", True)  # false: talking head only, the library is never offered

# Homebrew's default ffmpeg lacks libass/drawtext; ffmpeg-full is keg-only.
FFMPEG = os.environ.get("REELS_FFMPEG", "/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg")
FFPROBE = os.environ.get("REELS_FFPROBE", "/opt/homebrew/opt/ffmpeg-full/bin/ffprobe")
WHISPER = os.environ.get("REELS_WHISPER", "/opt/homebrew/bin/whisper-cli")
AUTO_EDITOR = os.environ.get("REELS_AUTO_EDITOR", "/opt/homebrew/bin/auto-editor")
CLAUDE = os.environ.get("REELS_CLAUDE", str(Path.home() / ".local/bin/claude"))
# Optional: a machine-wide queue so transcription/renders never run alongside other heavy jobs.
HEAVY_QUEUE = Path(os.environ.get("REELS_HEAVY_QUEUE", Path.home() / ".claude/scripts/heavy-queue.sh"))
WHISPER_MODEL = MODELS / "ggml-large-v3-turbo.bin"
# Whisper copies the style of what it has already written: an unpunctuated fumbled start ("is it
# recording yeah okay um") turned a whole take lowercase with no punctuation. A punctuated opening
# prompt sets the style, and can teach it names it would otherwise misspell (settings.json).
# The "um"/"uh" in it also make Whisper write fillers down, so they can be cut.
WHISPER_PROMPT = SETTINGS["whisper_prompt"]
WX_PYTHON = MODELS / ".wx" / "bin" / "python"  # WhisperX venv for word alignment (see README)

VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".mkv", ".3gp"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp"}

# Asset library: B-roll, screen recordings, photos and screenshots the edit can cut to.
# library/ is yours (synced from the phone, read in place, never moved); extras that arrive in inbox/
# are described, then moved to archive/extras/. Both end up in the catalogue.
LIBRARY = ROOT / "library"
EXTRAS = ARCHIVE / "extras"
CATALOG = STATE / "catalog.jsonl"
LINK_WINDOW_S = 2 * 3600  # an extra recorded this close to a take belongs to that take
SHEET_FRAMES = 6  # frames on a video asset's contact sheet

# Output spec (Instagram Reels)
WIDTH, HEIGHT, FPS = 1080, 1920, 30
TARGET_LUFS, TARGET_TP, TARGET_LRA = -14.0, -1.5, 11.0

# Clip rules
CLIP_MIN_S, CLIP_MAX_S = 30.0, 60.0
CLIPS_MIN, CLIPS_MAX = 3, 5
# Finished clips run ~4.5% longer than summed speech (word tails, kept breaths); measured 2026-10-01.
FINAL_PER_SPEECH = 1.05  # fewer allowed when the recording is short; see select.py

# Silence removal (auto-editor) — keep natural breaths, drop dead air.
SILENCE_THRESHOLD = "-35dB"
SILENCE_MARGIN = "0.12s"
MIN_CUT_GAP_S = 0.35  # gaps shorter than this are left alone

# Fully-synced check
SETTLE_SECONDS = 10
MAX_ATTEMPTS = 5  # automatic attempts; waits 1h, 2h, 4h, 8h between them (covers a usage-limit reset)
SYNC_WAIT_MAX_S = 45 * 60  # how long one sweep keeps waiting on a file that is still syncing


def style() -> dict:
    return _json("style.json")


def channel() -> str:
    """The channel brief handed to Claude with every transcript."""
    return re.sub(r"<!--.*?-->", "", config_file("channel.md").read_text(), flags=re.S).strip()
