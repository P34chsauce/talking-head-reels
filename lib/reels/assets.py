"""Asset library: B-roll, screen recordings, photos and screenshots the edit can cut to.

Each asset is described once by `claude -p` (describe-asset skill) from a contact sheet of sampled
frames, and kept in state/catalog.jsonl. library/ is read in place; extras from inbox/ are moved to
archive/extras/ once described.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import shutil
import subprocess
from pathlib import Path

from . import config as C
from . import ingest
from . import media as M
from . import select as S

KINDS = ["broll", "screen", "photo", "screenshot"]

DESCRIBE_SCHEMA = {
    "type": "object",
    "required": ["kind", "description", "tags", "moments", "quality", "issues", "privacy"],
    "properties": {
        "kind": {"type": "string", "enum": KINDS},
        "description": {"type": "string"},
        "tags": {"type": "array", "minItems": 3, "maxItems": 8, "items": {"type": "string"}},
        "moments": {"type": "array", "items": {
            "type": "object", "required": ["t", "what"],
            "properties": {"t": {"type": "number"}, "what": {"type": "string"}}}},
        "quality": {"type": "string", "enum": ["good", "ok", "poor"]},
        "issues": {"type": "array", "items": {"type": "string"}},
        "privacy": {"type": "array", "items": {"type": "string"}},
    },
}

# Phone camera, screenshot and screen-recorder file names carry the local capture time:
# VID_20261018_190812.mp4, 20261018_190812.jpg, Screenshot_20261018_190812_App.png
STAMP = re.compile(r"(20\d{2})(\d{2})(\d{2})[_-](\d{2})(\d{2})(\d{2})")


def is_image(p: Path) -> bool:
    return p.suffix.lower() in C.IMAGE_EXTS


def recorded_at(p: Path) -> dt.datetime:
    """Local capture time: the file name's stamp, then the file's own metadata, then mtime."""
    m = STAMP.search(p.name)
    if m:
        try:
            return dt.datetime(*map(int, m.groups()))
        except ValueError:
            pass
    try:
        if is_image(p):
            r = M.run(["sips", "-g", "creation", p])
            hit = re.search(r"creation: (\d{4}):(\d{2}):(\d{2}) (\d{2}):(\d{2}):(\d{2})", r.stdout)
            if hit:
                return dt.datetime(*map(int, hit.groups()))
        else:
            tag = M.probe(p)["format"].get("tags", {}).get("creation_time")
            if tag:  # UTC
                return dt.datetime.fromisoformat(tag.replace("Z", "+00:00")).astimezone().replace(tzinfo=None)
    except Exception:  # noqa: BLE001 — metadata is a nicety; mtime always works
        pass
    return dt.datetime.fromtimestamp(p.stat().st_mtime)


# --- what the file is -----------------------------------------------------------------------

def inspect(p: Path) -> dict:
    """Size as shown (phone videos are often stored sideways with a rotation flag), length, audio."""
    if is_image(p):
        r = M.run(["sips", "-g", "pixelWidth", "-g", "pixelHeight", p])
        w = int(re.search(r"pixelWidth: (\d+)", r.stdout).group(1))
        h = int(re.search(r"pixelHeight: (\d+)", r.stdout).group(1))
        return {"width": w, "height": h, "duration": None, "has_audio": False}
    r = M.run([C.FFPROBE, "-v", "error", "-show_entries",
               "format=duration:stream=codec_type,width,height:stream_side_data=rotation:stream_tags=rotate",
               "-of", "json", p])
    d = json.loads(r.stdout)
    video = next(s for s in d["streams"] if s.get("codec_type") == "video")
    rot = next((sd.get("rotation") for sd in video.get("side_data_list", []) if "rotation" in sd),
               video.get("tags", {}).get("rotate", 0))
    w, h = video["width"], video["height"]
    if abs(int(float(rot))) % 180 == 90:
        w, h = h, w
    return {"width": w, "height": h, "duration": round(float(d["format"]["duration"]), 2),
            "has_audio": any(s.get("codec_type") == "audio" for s in d["streams"])}


def not_a_take(info: dict) -> bool:
    """No sound, or too short to hold one clip: this video can only ever be B-roll."""
    return not info["has_audio"] or (info["duration"] or 0) < C.CLIP_MIN_S


def kind_hint(p: Path) -> str:
    n = p.name.lower()
    if is_image(p):
        return "screenshot" if "screenshot" in n else "photo"
    return "screen" if "screen" in n else "broll"


def orientation(w: int, h: int) -> str:
    return "portrait" if h > w * 1.05 else "landscape" if w > h * 1.05 else "square"


# --- contact sheet + proxy ------------------------------------------------------------------

def make_proxy(src: Path, adir: Path) -> Path:
    """A JPEG the renderer and Claude can both read (HEIC goes through sips), at most 1920 px."""
    out = adir / "image.jpg"
    if src.suffix.lower() in {".heic", ".heif"}:
        M.run(["sips", "-s", "format", "jpeg", "-Z", "1920", src, "--out", out])
    else:
        M.run([C.FFMPEG, "-hide_banner", "-y", "-i", src, "-vf",
               "scale='min(1920,iw)':'min(1920,ih)':force_original_aspect_ratio=decrease",
               "-frames:v", "1", "-q:v", "3", out])
    return out


def frame_times(duration: float, n: int = C.SHEET_FRAMES) -> list[float]:
    step = duration / n
    return [round(step * (i + 0.5), 2) for i in range(n)]


def contact_sheet(src: Path, adir: Path, info: dict) -> tuple[Path, list[float]]:
    """Video: N frames in a 3-wide grid, each stamped with its time. Image: the proxy itself."""
    if info["duration"] is None:
        return make_proxy(src, adir), []
    times = frame_times(info["duration"])
    for i, t in enumerate(times):
        M.run([C.FFMPEG, "-hide_banner", "-y", "-ss", f"{t:.2f}", "-i", src, "-frames:v", "1", "-vf",
               f"scale=360:360:force_original_aspect_ratio=decrease,pad=360:360:(ow-iw)/2:(oh-ih)/2,"
               f"drawtext=fontfile={C.FONT}:text='{t:.1f}s':x=8:y=8:fontsize=28:fontcolor=white:"
               f"box=1:boxcolor=black@0.6:boxborderw=4", adir / f"f{i}.jpg"])
    sheet = adir / "sheet.jpg"
    rows = -(-len(times) // 3)
    M.run([C.FFMPEG, "-hide_banner", "-y", "-i", adir / "f%d.jpg", "-vf", f"tile=3x{rows}",
           "-frames:v", "1", "-q:v", "3", sheet])
    return sheet, times


# --- describe-asset -------------------------------------------------------------------------

def build_prompt(src: Path, info: dict, sheet: Path, times: list[float], speech: str = "") -> str:
    shape = f"{info['width']}x{info['height']} ({orientation(info['width'], info['height'])})"
    p = (f"/describe-asset\n\n## Channel\n\n{C.channel()}\n\n## Asset\n\n"
         f"File: {src.name}\nLooks like: {kind_hint(src)} (from the file name; check it)\nSize: {shape}\n")
    if info["duration"] is None:
        p += f"Still image. Read it at: {sheet}\n"
    else:
        p += (f"Video, {info['duration']:.1f}s, {'with' if info['has_audio'] else 'no'} sound.\n"
              f"Contact sheet of frames at {', '.join(f'{t:.1f}s' for t in times)} "
              f"(left to right, top to bottom; each frame is stamped with its time). Read it at: {sheet}\n")
    if speech:
        p += f"\nSpeech heard in it: {speech[:1500]}\n"
    return p


def describe(src: Path, info: dict, sheet: Path, times: list[float], speech: str = "",
             ask=None) -> dict:
    ask = ask or S.ask_claude
    prompt = build_prompt(src, info, sheet, times, speech)
    d = ask(prompt, schema=DESCRIBE_SCHEMA, tools=f"Skill,Read({sheet.parent}/**)")
    errs = validate(d, info)
    if errs:  # one retry with the reasons, like the clip planner
        d = ask(prompt + "\nYour previous answer was rejected:\n" + "\n".join(f"- {e}" for e in errs) + "\n",
                schema=DESCRIBE_SCHEMA, tools=f"Skill,Read({sheet.parent}/**)")
        errs = validate(d, info)
        if errs:
            raise RuntimeError("asset description invalid: " + "; ".join(errs))
    return d


def validate(d: dict, info: dict) -> list[str]:
    errs = []
    if d.get("kind") not in KINDS:
        errs.append(f"kind must be one of {KINDS}")
    still = info["duration"] is None
    if still and d.get("kind") in {"broll", "screen"}:
        errs.append("a still image is a photo or a screenshot")
    if not still and d.get("kind") in {"photo", "screenshot"}:
        errs.append("a video is broll or screen")
    if not d.get("description", "").strip():
        errs.append("description is empty")
    for m in d.get("moments", []):
        if still:
            errs.append("a still image has no moments")
            break
        if not (0 <= m["t"] <= info["duration"]):
            errs.append(f"moment at {m['t']}s is outside the video (0-{info['duration']}s)")
    return errs


# --- catalogue ------------------------------------------------------------------------------

def _rel(p: Path) -> str:
    try:
        return str(p.resolve().relative_to(C.ROOT))
    except ValueError:
        return str(p.resolve())


def path_of(entry: dict) -> Path:
    return C.ROOT / entry["path"]  # an absolute path stays absolute


def load() -> dict[str, dict]:
    """sha256 -> latest entry."""
    out = {}
    if C.CATALOG.exists():
        for line in C.CATALOG.read_text().splitlines():
            if line.strip():
                e = json.loads(line)
                out[e["sha256"]] = e
    return out


def record(entry: dict) -> None:
    C.CATALOG.parent.mkdir(parents=True, exist_ok=True)
    with C.CATALOG.open("a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def entries() -> list[dict]:
    """Assets whose file is still there, oldest first."""
    return sorted((e for e in load().values() if path_of(e).exists()), key=lambda e: e["recorded_at"])


def near(when: dt.datetime, window: float = C.LINK_WINDOW_S) -> list[dict]:
    """Assets recorded within `window` seconds of `when` (the extras that belong to a take)."""
    return [e for e in entries()
            if abs((dt.datetime.fromisoformat(e["recorded_at"]) - when).total_seconds()) <= window]


def search(query: str) -> list[dict]:
    terms = query.lower().split()
    def text(e):
        return " ".join([e["kind"], e["description"], *e["tags"], *(m["what"] for m in e["moments"])]).lower()
    return [e for e in entries() if all(t in text(e) for t in terms)]


def add(src: Path, origin: str, sha: str | None = None, speech: str = "", ask=None) -> dict:
    """Describe one file and add it to the catalogue. origin 'inbox' moves it to archive/extras/."""
    sha = sha or ingest.sha256(src)
    have = load().get(sha)
    if have and path_of(have).exists():
        if origin == "inbox":  # a second copy of something already in the library
            C.EXTRAS.mkdir(parents=True, exist_ok=True)
            shutil.move(src, ingest.unique(C.EXTRAS / src.name))
        return have
    info = inspect(src)
    adir = C.WORK / "assets" / sha[:12]
    adir.mkdir(parents=True, exist_ok=True)
    sheet, times = contact_sheet(src, adir, info)
    d = describe(src, info, sheet, times, speech, ask)
    when = recorded_at(src)
    final = src
    if origin == "inbox":
        C.EXTRAS.mkdir(parents=True, exist_ok=True)
        final = ingest.unique(C.EXTRAS / src.name)
        shutil.move(src, final)
    entry = {
        "id": sha[:12], "sha256": sha, "path": _rel(final), "name": src.name, "origin": origin,
        "recorded_at": when.isoformat(timespec="seconds"), **info,
        "orientation": orientation(info["width"], info["height"]),
        "image": _rel(adir / "image.jpg") if info["duration"] is None else None,
        "sheet": _rel(sheet), "speech": speech[:1500], **d,
        "added_at": dt.datetime.now().isoformat(timespec="seconds"),
    }
    record(entry)
    return entry


# --- library/ -------------------------------------------------------------------------------

LIB_INDEX = C.STATE / "library-index.json"  # path -> [size, mtime, sha]: skip re-hashing big files


def library_files() -> list[Path]:
    if not C.LIBRARY.exists():
        return []
    return sorted(p for p in C.LIBRARY.rglob("*")
                  if not any(part.startswith(".") for part in p.relative_to(C.LIBRARY).parts)
                  and ingest.is_candidate(p))


def scan_library(wait: bool = True, ask=None, log=print) -> list[tuple[Path, BaseException]]:
    """Describe anything new in library/. Returns the files that failed (the caller logs them)."""
    index = json.loads(LIB_INDEX.read_text()) if LIB_INDEX.exists() else {}
    known = load()
    failed = []
    for p in library_files():
        if ingest.gave_up(p) or (wait and not ingest.due_for_retry(p)):
            continue
        rel = str(p.relative_to(C.LIBRARY))
        st = p.stat()
        cached = index.get(rel)
        if cached and cached[:2] == [st.st_size, st.st_mtime]:
            sha = cached[2]
        else:
            if wait and not ingest.settled(p):
                continue
            sha = ingest.sha256(p)
            index[rel] = [st.st_size, st.st_mtime, sha]
            LIB_INDEX.parent.mkdir(parents=True, exist_ok=True)
            LIB_INDEX.write_text(json.dumps(index))
        if sha in known and path_of(known[sha]).exists():
            continue
        try:
            e = add(p, "library", sha=sha, ask=ask)
            ingest.error_log(p).unlink(missing_ok=True)
            log(f"library: {rel} -> {e['kind']} {e['id']}: {e['description'][:80]}")
        except Exception as exc:  # noqa: BLE001 — one bad file must not stop the scan
            failed.append((p, exc))
    return failed


# --- for the edit ---------------------------------------------------------------------------

MENU_MAX = 30  # assets offered to the clip planner per recording


def menu(when: dt.datetime | None) -> list[dict]:
    """What the clip planner may cut to: extras recorded around the take first, then the rest of
    the library, newest first. Poor-quality assets are left out."""
    usable = [e for e in entries() if e["quality"] != "poor"]
    linked = near(when) if when else []
    ids = {e["id"] for e in linked}
    rest = sorted((e for e in usable if e["id"] not in ids), key=lambda e: e["recorded_at"], reverse=True)
    return [e for e in linked if e["quality"] != "poor"] + rest[: max(0, MENU_MAX - len(linked))]


def menu_text(assets: list[dict], linked: set[str] = frozenset()) -> str:
    """One block per asset, in the words the planner needs to match footage to lines."""
    out = []
    for e in assets:
        length = f"{e['duration']:.1f}s video" if e["duration"] else "still image"
        head = f"{e['id']} | {e['kind']} | {length} | {e['orientation']} | quality {e['quality']}"
        if e["id"] in linked:
            head += " | recorded with this take"
        lines = [head, f"  {e['description']}"]
        if e["moments"]:
            lines.append("  moments: " + "; ".join(f"{m['t']:.1f}s {m['what']}" for m in e["moments"]))
        if e["issues"]:
            lines.append("  issues: " + "; ".join(e["issues"]))
        if e["privacy"]:
            lines.append("  privacy: " + "; ".join(e["privacy"]))
        out.append("\n".join(lines))
    return "\n\n".join(out)


def render_source(e: dict) -> Path:
    """A file Remotion renders quickly and predictably: stills as their JPEG proxy; video as
    upright H.264, 30 fps constant, at most 1080 px on the short side, no sound. Cached."""
    adir = C.WORK / "assets" / e["id"]
    adir.mkdir(parents=True, exist_ok=True)
    if e["duration"] is None:
        img = adir / "image.jpg"
        return img if img.exists() else make_proxy(path_of(e), adir)
    out = adir / "render.mp4"
    if not out.exists():
        tmp = adir / "render.tmp.mp4"
        M.run([C.FFMPEG, "-hide_banner", "-y", "-i", path_of(e), "-an", "-vf",
               "scale='if(gt(iw,ih),-2,min(1080,iw))':'if(gt(iw,ih),min(1080,ih),-2)',fps=30",
               "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p", tmp])
        tmp.replace(out)
    return out
