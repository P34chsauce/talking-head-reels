"""Word-level transcript helpers: parsing, swear matching, cut-list remapping, caption phrases.

A word is a dict {"w": str, "start": float, "end": float} in seconds. Kept intervals
are sorted, non-overlapping (start, end) tuples on the source timeline.
"""
from __future__ import annotations

import re
from pathlib import Path

from .config import config_file

_PUNCT = re.compile(r"^[^\w']+|[^\w']+$")


def bare(word: str) -> str:
    return _PUNCT.sub("", word).lower()


# --- whisper.cpp ---------------------------------------------------------------

def from_whisper_cpp(data: dict) -> list[dict]:
    """Merge whisper.cpp tokens (-ojf, optionally -dtw) into words.

    A token starting with a space opens a new word; others glue onto the current one.
    With DTW, `t_dtw` (centiseconds) marks where each token is spoken; a word starts at
    its first token's t_dtw and ends where the next word starts, capped by the token
    offsets so trailing silence isn't swallowed.
    """
    words: list[dict] = []
    for seg in data["transcription"]:
        for tok in seg.get("tokens", []):
            text = tok["text"]
            if text.startswith("[_") or text.startswith("[") and text.endswith("]"):
                continue  # special tokens like [_BEG_], [_TT_123]
            frm, to = tok["offsets"]["from"] / 1000, tok["offsets"]["to"] / 1000
            dtw = tok.get("t_dtw", -1)
            t = dtw / 100 if dtw is not None and dtw >= 0 else frm
            if text.startswith(" ") or not words:
                words.append({"w": text.strip(), "start": t, "end": to, "_tok_end": to})
            else:
                words[-1]["w"] += text
                words[-1]["end"] = to
                words[-1]["_tok_end"] = to
    words = [w for w in words if w["w"]]
    for i, w in enumerate(words):
        nxt = words[i + 1]["start"] if i + 1 < len(words) else w["_tok_end"]
        w["end"] = max(w["start"] + 0.05, min(nxt, w["_tok_end"] if w["_tok_end"] > w["start"] else nxt))
        del w["_tok_end"]
    return _monotonic(words)


def _monotonic(words: list[dict]) -> list[dict]:
    last = 0.0
    for w in words:
        w["start"] = round(max(w["start"], last), 3)
        w["end"] = round(max(w["end"], w["start"] + 0.05), 3)
        last = w["start"]
    return words


# --- WhisperX ----------------------------------------------------------------------------

def from_whisperx(result: dict, silences: list[tuple[float, float]] = ()) -> list[dict]:
    """Aligned words from WhisperX. Words it couldn't align (digits, symbols) get times
    interpolated from their neighbours; a start that falls inside a pause is moved to the
    pause end (WhisperX tends to pull the first word after a silence too early)."""
    words = []
    for seg in result["segments"]:
        for w in seg.get("words", []):
            words.append({"w": w["word"].strip(), "start": w.get("start"), "end": w.get("end")})
    words = [w for w in words if w["w"]]
    for i, w in enumerate(words):
        if w["start"] is None:
            prev_end = next((x["end"] for x in reversed(words[:i]) if x["end"] is not None), 0.0)
            nxt = next((x["start"] for x in words[i + 1:] if x["start"] is not None), prev_end + 0.3)
            w["start"], w["end"] = prev_end, max(prev_end + 0.05, nxt)
    for w in words:
        for s, e in silences:
            if s <= w["start"] < e - 0.05 and w["end"] > e:
                w["start"] = e
    return _monotonic(words)


# --- quotes in notes --------------------------------------------------------------------

def find_phrase(words: list[dict], text: str, probe: int = 4) -> float | None:
    """Start time of the first place `text`'s opening words are spoken."""
    want = [bare(t) for t in text.split() if bare(t)][:probe]
    have = [bare(w["w"]) for w in words]
    for i in range(len(have) - len(want) + 1):
        if want and have[i:i + len(want)] == want:
            return words[i]["start"]
    return None


def fix_quote_timestamps(md: str, words: list[dict]) -> str:
    """Rewrite `"quote" [mm:ss]` stamps from the transcript itself."""
    def sub(m):
        t = find_phrase(words, m.group(1))
        if t is None:
            return m.group(0)
        return f'"{m.group(1)}"{m.group(2)}[{int(t // 60):02d}:{int(t % 60):02d}]'
    return re.sub(r'"([^"\n]+)"(\s*)\[\d{1,2}:\d{2}(?:\.\d+)?\]', sub, md)


# --- fillers --------------------------------------------------------------------------

FILLERS = {"so", "um", "umm", "uh", "er", "erm", "ah", "okay", "ok", "and", "anyway", "yeah", "right"}


def trim_leading_fillers(ws: list[dict]) -> list[dict]:
    """Drop filler words at the very start so the hook is the first thing heard."""
    i = 0
    while i < len(ws) - 1 and bare(ws[i]["w"]) in FILLERS:
        i += 1
    return ws[i:]


# --- swears ---------------------------------------------------------------------

def load_swears(path: Path | None = None) -> tuple[list[str], set[str]]:
    stems, exact = [], set()
    for raw in (path or config_file("swears.txt")).read_text().splitlines():
        line = raw.strip().lower()
        if not line or line.startswith("#"):
            continue
        (exact.add(line[1:]) if line.startswith("=") else stems.append(line))
    return stems, exact


def is_swear(word: str, swears=None) -> bool:
    stems, exact = swears or load_swears()
    b = bare(word)
    if not b:
        return False
    if "*" in word:  # whisper sometimes censors on its own: f***ing
        return True
    return b in exact or any(b.startswith(s) for s in stems)


CUT_FILLERS = {"um", "umm", "uh", "uhh", "er", "erm", "hmm", "mm", "ah"}


def is_filler(word: str) -> bool:
    return bare(word) in CUT_FILLERS


def cut_spans(words: list[dict], match, quietest=None, reach: float = 0.25) -> list[tuple[float, float]]:
    """Cut windows for every word where `match(word)` is true (swears, fillers).

    Aligners place word edges ~0.1 s inside soft onsets (f-, sh-) and trailing stops (-t), so each
    boundary snaps to the quietest moment of the real audio within `reach` outside the word:
    `quietest(t0, t1) -> t` (see media.Envelope). The end never reaches past the next word's start:
    in "shit and bloody" the quietest point was inside "and", which got cut with it. Vowel and
    plosive onsets are aligned well, so the next word's start is a safe ceiling. Without audio,
    fall back to fixed padding.
    """
    spans = []
    for k, w in enumerate(words):
        if not match(w["w"]):
            continue
        next_start = words[k + 1]["start"] if k + 1 < len(words) else float("inf")
        ceiling = max(w["end"], min(w["end"] + reach, next_start + 0.02))
        if quietest:
            # +30 ms beyond the quiet point: a soft fricative can itself be the quietest moment
            start = max(0.0, quietest(max(0.0, w["start"] - reach), w["start"] + 0.02) - 0.03)
            end = min(quietest(w["end"] - 0.02, ceiling) + 0.03, ceiling)
        else:
            start, end = max(0.0, w["start"] - 0.12), min(w["end"] + 0.12, ceiling)
        spans.append((round(start, 3), round(end, 3)))
    return spans


def swear_spans(words: list[dict], swears=None, quietest=None, reach: float = 0.25) -> list[tuple[float, float]]:
    swears = swears or load_swears()
    return cut_spans(words, lambda w: is_swear(w, swears), quietest, reach)


def mask(word: str) -> str:
    b = bare(word)
    return word.replace(b[1:], "*" * (len(b) - 1)) if len(b) > 1 else word


# --- intervals --------------------------------------------------------------------

def subtract(keep: list[tuple[float, float]], cuts: list[tuple[float, float]],
             min_len: float = 0.08) -> list[tuple[float, float]]:
    """Remove `cuts` from `keep`; drop slivers shorter than min_len."""
    out = []
    for ks, ke in keep:
        pieces = [(ks, ke)]
        for cs, ce in sorted(cuts):
            nxt = []
            for ps, pe in pieces:
                if ce <= ps or cs >= pe:
                    nxt.append((ps, pe))
                    continue
                if cs > ps:
                    nxt.append((ps, cs))
                if ce < pe:
                    nxt.append((ce, pe))
            pieces = nxt
        out.extend(p for p in pieces if p[1] - p[0] >= min_len)
    return out


def total(keep: list[tuple[float, float]]) -> float:
    return sum(e - s for s, e in keep)


def remap(words: list[dict], keep: list[tuple[float, float]]) -> list[dict]:
    """Map source-timeline words onto the edited timeline (kept intervals glued together).

    Words whose midpoint falls in a removed region are dropped (that's how cut swears
    leave the captions). Kept words are clamped to their interval.
    """
    out, offset = [], 0.0
    bounds = []
    for s, e in keep:
        bounds.append((s, e, offset))
        offset += e - s
    for w in words:
        mid = (w["start"] + w["end"]) / 2
        for s, e, off in bounds:
            if s <= mid < e:
                ns = off + max(w["start"], s) - s
                ne = off + min(w["end"], e) - s
                out.append({**w, "start": round(ns, 3), "end": round(max(ne, ns + 0.05), 3)})
                break
    return out


def speech_seconds(words: list[dict], start: float, end: float, max_gap: float = 0.35) -> float:
    """Estimated length of [start, end] once silences longer than max_gap are cut."""
    inside = [w for w in words if w["start"] >= start - 0.01 and w["end"] <= end + 0.01]
    return speech_of(inside, max_gap)


def speech_of(inside: list[dict], max_gap: float = 0.35) -> float:
    """Estimated edited length of these words in order (gaps, including across cuts, capped)."""
    if not inside:
        return 0.0
    dur = 0.0
    for i, w in enumerate(inside):
        dur += w["end"] - w["start"]
        if i + 1 < len(inside):
            dur += min(max(0.0, inside[i + 1]["start"] - w["end"]), max_gap)
    return round(dur, 2)


# --- captions -------------------------------------------------------------------------

def phrases(words: list[dict], max_words: int = 4, gap: float = 0.45) -> list[dict]:
    """Group words into short caption phrases, breaking on pauses and sentence ends."""
    out: list[dict] = []
    cur: list[dict] = []

    def flush():
        if cur:
            out.append({"start": cur[0]["start"], "end": cur[-1]["end"], "words": list(cur)})
            cur.clear()

    for i, w in enumerate(words):
        if cur and (len(cur) >= max_words or w["start"] - cur[-1]["end"] > gap):
            flush()
        cur.append(w)
        if re.search(r"[.!?…]$", w["w"]) or (re.search(r"[,;:]$", w["w"]) and len(cur) >= 2):
            flush()
    flush()
    # Hold each phrase on screen until the next one starts (no flicker in short gaps).
    for a, b in zip(out, out[1:]):
        if b["start"] - a["end"] < 0.6:
            a["end"] = b["start"]
    return out
