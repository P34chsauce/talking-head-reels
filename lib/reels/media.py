"""ffmpeg / whisper.cpp / auto-editor / Remotion wrappers. Heavy steps are called through
the heavy queue by pipeline.py, never directly from the watcher."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

from . import ass as ASS
from . import config as C
from . import words as W


def run(cmd: list, **kw) -> subprocess.CompletedProcess:
    r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True, stdin=subprocess.DEVNULL, **kw)
    if r.returncode != 0:
        raise RuntimeError(f"{Path(str(cmd[0])).name} failed ({r.returncode}):\n{r.stderr[-3000:]}")
    return r


def probe(src: Path) -> dict:
    r = run([C.FFPROBE, "-v", "error", "-show_entries",
             "format=duration:format_tags=creation_time:stream=codec_type,width,height,avg_frame_rate",
             "-of", "json", src])
    return json.loads(r.stdout)


def duration(src: Path) -> float:
    return float(probe(src)["format"]["duration"])


# --- transcription ----------------------------------------------------------------------

def silences(wav: Path, min_len: float = 0.3) -> list[tuple[float, float]]:
    r = run([C.FFMPEG, "-hide_banner", "-nostats", "-i", wav, "-af",
             f"silencedetect=noise={C.SILENCE_THRESHOLD}:d={min_len}", "-f", "null", "-"])
    starts = [float(x) for x in re.findall(r"silence_start: (-?[\d.]+)", r.stderr)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", r.stderr)]
    return list(zip(starts, ends))


def transcribe(src: Path, job: Path) -> list[dict]:
    """whisper.cpp (Metal) for the text, WhisperX wav2vec2 forced alignment for word timing."""
    wav = job / "audio16k.wav"
    run([C.FFMPEG, "-hide_banner", "-y", "-i", src, "-vn", "-ar", "16000", "-ac", "1",
         "-c:a", "pcm_s16le", wav])
    run([C.WHISPER, "-m", C.WHISPER_MODEL, "-f", wav, "-l", "en", "--prompt", C.WHISPER_PROMPT,
         "-oj", "-of", job / "whisper", "-np"])
    env = {**os.environ, "HF_HOME": str(C.MODELS / "hf")}
    run([C.WX_PYTHON, C.ROOT / "lib" / "align_wx.py", job / "whisper.json", wav, job / "aligned.json"],
        env=env)
    result = json.loads((job / "aligned.json").read_text())
    return W.from_whisperx(result, silences(wav))


class Envelope:
    """10 ms RMS envelope of a 16 kHz mono WAV, for snapping cuts to quiet moments."""

    def __init__(self, wav: Path, step: float = 0.01):
        import array
        import wave
        with wave.open(str(wav)) as w:
            self.sr = w.getframerate()
            samples = array.array("h", w.readframes(w.getnframes()))
        self.step = step
        n = int(self.sr * step)
        self.rms = [
            (sum(x * x for x in samples[i:i + n]) / max(1, len(samples[i:i + n]))) ** 0.5
            for i in range(0, len(samples), n)
        ]

    def quietest(self, t0: float, t1: float) -> float:
        a, b = max(0, int(t0 / self.step)), min(len(self.rms), int(t1 / self.step) + 1)
        if a >= b:
            return t0
        i = min(range(a, b), key=lambda k: self.rms[k])
        return round(i * self.step + self.step / 2, 3)


# --- silence analysis -------------------------------------------------------------------

def speech_intervals(src: Path, job: Path) -> list[tuple[float, float]]:
    """auto-editor's audio-activity timeline for the whole source, as (start, end) seconds."""
    out = job / "silence.v3"
    run([C.AUTO_EDITOR, src, "--edit", f"audio:threshold={C.SILENCE_THRESHOLD}",
         "--margin", C.SILENCE_MARGIN, "--export", "v3", "-o", out, "--no-open", "--progress", "none"])
    tl = json.loads(out.read_text())
    num, den = (int(x) for x in tl["timebase"].split("/"))
    fps = num / den
    segs = sorted((c["offset"] / fps, (c["offset"] + c["dur"]) / fps) for c in tl["a"][0])
    # merge segments separated by tiny gaps: those pauses are natural breaths
    merged: list[tuple[float, float]] = []
    for s, e in segs:
        if merged and s - merged[-1][1] < C.MIN_CUT_GAP_S:
            merged[-1] = (merged[-1][0], e)
        else:
            merged.append((s, e))
    return merged


def clip_keep(speech: list[tuple[float, float]], start: float, end: float,
              cuts: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Speech inside [start, end] minus `cuts` (swears, fillers, dropped lines)."""
    keep = [(max(s, start), min(e, end)) for s, e in speech if e > start and s < end]
    keep = W.subtract(keep, cuts)
    # snap to the 30 fps grid so audio and video segments have identical lengths
    snap = lambda t: round(t * C.FPS) / C.FPS
    keep = [(snap(s), snap(e)) for s, e in keep]
    return [(s, e) for s, e in keep if e - s >= 2 / C.FPS]


# --- edit render --------------------------------------------------------------------------

def render_edit(src: Path, keep: list[tuple[float, float]], out_video: Path, out_wav: Path) -> None:
    """Jump-cut the kept intervals into a silent 1080x1920@30 video and a 48 kHz WAV."""
    args = [C.FFMPEG, "-hide_banner", "-y"]
    for s, e in keep:
        args += ["-ss", f"{s:.3f}", "-t", f"{e - s:.3f}", "-i", src]
    vf = f"fps={C.FPS},scale={C.WIDTH}:{C.HEIGHT}:force_original_aspect_ratio=increase,crop={C.WIDTH}:{C.HEIGHT},setsar=1,format=yuv420p"
    parts, labels = [], []
    for i, (s, e) in enumerate(keep):
        d = e - s
        fade = min(0.006, d / 4)
        parts.append(f"[{i}:v]{vf},trim=duration={d:.4f},setpts=PTS-STARTPTS[v{i}]")
        parts.append(f"[{i}:a]aresample=48000,aformat=channel_layouts=stereo,atrim=duration={d:.4f},"
                     f"apad=whole_dur={d:.4f},asetpts=PTS-STARTPTS,afade=t=in:d={fade:.4f},"
                     f"afade=t=out:st={d - fade:.4f}:d={fade:.4f}[a{i}]")
        labels.append(f"[v{i}][a{i}]")
    parts.append("".join(labels) + f"concat=n={len(keep)}:v=1:a=1[v][a]")
    (out_video.parent / "edit.filter").write_text(";\n".join(parts))
    args += ["-/filter_complex", out_video.parent / "edit.filter",
             "-map", "[v]", "-c:v", "libx264", "-preset", "veryfast", "-crf", "16", "-an", out_video,
             "-map", "[a]", "-c:a", "pcm_s16le", out_wav]
    run(args)


# --- audio ------------------------------------------------------------------------------

# Talking-head speech peaks ~20 dB above its average, so loudnorm can't reach -14 LUFS in its exact
# (linear) mode and silently falls back to dynamic mode, landing ~1 LU short (measured on the
# stress-test take: -14.9/-15.0/-14.8). Instead: gentle voice compression, exact measured gain,
# a peak limiter, and re-measure until within tolerance.
VOICE_CHAIN = "highpass=f=70,acompressor=threshold=0.04:ratio=4:attack=5:release=150:knee=4"
PEAK_LIMIT = "alimiter=limit=0.794:attack=3:release=60:level=disabled"  # -2 dBFS -> < -1.5 dBTP after AAC


def _ebu(wav: Path, chain: str) -> tuple[float, float]:
    r = run([C.FFMPEG, "-hide_banner", "-nostats", "-i", wav, "-af", f"{chain},ebur128=peak=true",
             "-f", "null", "-"])
    return (float(re.findall(r"I:\s+(-?[\d.]+) LUFS", r.stderr)[-1]),
            float(re.findall(r"Peak:\s+(-?[\d.]+) dBFS", r.stderr)[-1]))


def loudnorm(wav: Path, out: Path, mute: list[tuple[float, float]] | None = None,
             tolerance: float = 0.2) -> dict:
    """Normalise speech to TARGET_LUFS integrated. `mute` windows (edited timeline) are silenced first."""
    pre = ""
    if mute:
        expr = "+".join(f"between(t,{s:.3f},{e:.3f})" for s, e in mute)
        pre = f"volume=enable='{expr}':volume=0,"
    base = f"{pre}{VOICE_CHAIN}"
    i0, _ = _ebu(wav, base)
    gain = C.TARGET_LUFS - i0
    for _ in range(4):
        chain = f"{base},volume={gain:.2f}dB,{PEAK_LIMIT}"
        i, peak = _ebu(wav, chain)
        if abs(i - C.TARGET_LUFS) <= tolerance:
            break
        gain += C.TARGET_LUFS - i
    run([C.FFMPEG, "-hide_banner", "-y", "-i", wav, "-af", chain, "-ar", "48000", "-c:a", "pcm_s16le", out])
    return {"input_i": i0, "gain_db": round(gain, 2), "output_i": i, "peak_dbfs": peak}


def measure_lufs(src: Path) -> float:
    r = run([C.FFMPEG, "-hide_banner", "-nostats", "-i", src, "-af", "ebur128", "-f", "null", "-"])
    return float(re.findall(r"I:\s+(-?[\d.]+) LUFS", r.stderr)[-1])


# --- captions -----------------------------------------------------------------------------

REMOTION_DIR = C.CAPTIONS


def render_remotion(video: Path, props: dict, out: Path) -> None:
    pub = out.parent / "public"
    pub.mkdir(exist_ok=True)
    for f, target in ((video, pub / "clip.mp4"), (C.FONT, pub / C.FONT.name)):
        if target.exists():
            target.unlink()
        try:
            os.link(f, target)
        except OSError:
            shutil.copy2(f, target)
    props_file = out.parent / "props.json"
    props_file.write_text(json.dumps({**props, "video": "clip.mp4"}))
    run([REMOTION_DIR / "node_modules/.bin/remotion", "render", "src/index.ts", "Reel", out.resolve(),
         f"--props={props_file.resolve()}", f"--public-dir={pub.resolve()}", "--muted",
         "--codec=h264", "--crf=18", "--log=error"], cwd=REMOTION_DIR)


def render_ass(video: Path, wav: Path, phrases: list[dict], style: dict, out: Path) -> None:
    sub = out.parent / "captions.ass"
    sub.write_text(ASS.build(phrases, style))
    fonts = C.FONT.parent
    run([C.FFMPEG, "-hide_banner", "-y", "-i", video, "-i", wav,
         "-vf", f"ass={sub}:fontsdir={fonts}", "-map", "0:v", "-map", "1:a",
         "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", out])


def mux(video: Path, wav: Path, out: Path) -> None:
    run([C.FFMPEG, "-hide_banner", "-y", "-i", video, "-i", wav, "-map", "0:v", "-map", "1:a",
         "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", "-shortest", out])


def frame(src: Path, t: float, out: Path) -> None:
    run([C.FFMPEG, "-hide_banner", "-y", "-ss", f"{t:.2f}", "-i", src, "-frames:v", "1", out])
