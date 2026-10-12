"""Compare word-timestamp accuracy of transcripts against silence boundaries.

Ground truth: ffmpeg silencedetect pauses (>= 0.5 s). The word right after a pause should start at
the pause end; the word right before should end at the pause start.

  python tests/bench_alignment.py AUDIO.wav name=words.json [name=words.json ...]
words.json: list of {"w","start","end"} (see reels.words) or a WhisperX JSON.
"""
import json
import re
import statistics
import subprocess
import sys

sys.path.insert(0, "lib")
from reels import config as C  # noqa: E402


def silences(wav):
    r = subprocess.run([C.FFMPEG, "-hide_banner", "-nostats", "-i", wav, "-af",
                        "silencedetect=noise=-35dB:d=0.5", "-f", "null", "-"], capture_output=True, text=True)
    starts = [float(x) for x in re.findall(r"silence_start: ([\d.]+)", r.stderr)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", r.stderr)]
    return list(zip(starts, ends))


def load(path):
    d = json.load(open(path))
    if isinstance(d, dict) and "segments" in d:  # WhisperX
        return [{"w": w["word"], "start": w["start"], "end": w["end"]}
                for s in d["segments"] for w in s.get("words", []) if "start" in w]
    return d


def score(words, pauses):
    onset, offset = [], []
    for s, e in pauses:
        after = min((w for w in words if w["start"] >= s - 0.3), key=lambda w: abs(w["start"] - e), default=None)
        before = min((w for w in words if w["end"] <= e + 0.3), key=lambda w: abs(w["end"] - s), default=None)
        if after:
            onset.append(abs(after["start"] - e))
        if before:
            offset.append(abs(before["end"] - s))
    f = lambda xs: f"median {statistics.median(xs)*1000:4.0f} ms, p90 {sorted(xs)[int(len(xs)*.9)]*1000:4.0f} ms"
    return f"onsets {f(onset)} | ends {f(offset)} | n={len(onset)}"


if __name__ == "__main__":
    wav, specs = sys.argv[1], sys.argv[2:]
    p = silences(wav)
    for spec in specs:
        name, path = spec.split("=", 1)
        print(f"{name:12s} {score(load(path), p)}")
