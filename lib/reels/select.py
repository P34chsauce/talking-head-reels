"""Editorial step: transcript lines -> `claude -p` (make-reels skill) -> validated plan."""
from __future__ import annotations

import json
import os
import re
import subprocess

from . import config as C
from . import words as W
from .copy_check import check_copy, check_speech, load_rules


class TransientError(RuntimeError):
    """Worth retrying later (usage limit, network, billing guard)."""


class PermanentError(RuntimeError):
    """Retrying won't help without a human (e.g. no usable speech)."""


# --- transcript lines -------------------------------------------------------------------

def lines(words: list[dict], max_gap: float = 0.8, max_words: int = 40) -> list[dict]:
    """Sentence-ish lines: split at . ? ! or a pause longer than max_gap."""
    out, cur = [], []
    for i, w in enumerate(words):
        cur.append(i)
        nxt = words[i + 1] if i + 1 < len(words) else None
        end_sentence = re.search(r"[.!?…]['\"]?$", w["w"])
        pause = nxt is not None and nxt["start"] - w["end"] > max_gap
        if nxt is None or end_sentence or pause or len(cur) >= max_words:
            ws = [words[j] for j in cur]
            out.append({
                "n": len(out) + 1, "first": cur[0], "last": cur[-1],
                "start": ws[0]["start"], "end": ws[-1]["end"],
                "speech": W.speech_seconds(ws, ws[0]["start"], ws[-1]["end"]),
                "text": " ".join(x["w"] for x in ws),
            })
            cur = []
    return out


def _mmss(t: float) -> str:
    m, s = divmod(t, 60)
    return f"{int(m):02d}:{s:04.1f}"


def render_lines(ls: list[dict]) -> str:
    return "\n".join(
        f"L{l['n']} [{_mmss(l['start'])}-{_mmss(l['end'])} | {l['speech'] * C.FINAL_PER_SPEECH:.1f}s] {l['text']}"
        for l in ls)


# --- claude -p --------------------------------------------------------------------------

SCHEMA = {
    "type": "object",
    "required": ["slug", "notes_md", "clips", "skipped"],
    "properties": {
        "slug": {"type": "string", "pattern": "^[a-z0-9]+(-[a-z0-9]+){1,4}$"},
        "notes_md": {"type": "string"},
        "skipped": {"type": "array", "items": {"type": "string"}},
        "clips": {"type": "array", "minItems": 1, "maxItems": 5, "items": {
            "type": "object",
            "required": ["title", "first_line", "last_line", "why", "emojis", "cut_lines", "caption_fixes",
                         "caption", "hashtags"],
            "properties": {
                "title": {"type": "string"},
                "first_line": {"type": "integer"},
                "last_line": {"type": "integer"},
                "why": {"type": "string"},
                "emojis": {"type": "array", "maxItems": 2, "items": {
                    "type": "object", "required": ["line", "word", "emoji"],
                    "properties": {"line": {"type": "integer"}, "word": {"type": "string"},
                                   "emoji": {"type": "string"}}}},
                "cut_lines": {"type": "array", "items": {"type": "integer"}},
                "caption_fixes": {"type": "array", "items": {
                    "type": "object", "required": ["line", "wrong", "right"],
                    "properties": {"line": {"type": "integer"}, "wrong": {"type": "string"},
                                   "right": {"type": "string"}}}},
                "caption": {"type": "string"},
                "hashtags": {"type": "array", "minItems": 3, "maxItems": 5,
                             "items": {"type": "string", "pattern": "^#[a-z0-9_]+$"}},
            }}},
    },
}

TRANSIENT_HINTS = ("rate limit", "usage limit", "overloaded", "529", "timed out", "network",
                   "billing", "ECONNRESET", "ETIMEDOUT", "hit your limit", "blocked")


def _child_env() -> dict:
    # Same environment launchd gives us: no inherited Claude Code session variables.
    # Never pass API keys or base URLs: claude must run on your Claude subscription login.
    banned = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL")
    return {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE") and k not in banned}


def ask_claude(prompt: str, timeout: int = 900) -> dict:
    cmd = [C.CLAUDE, "-p", prompt,
           "--output-format", "json",
           "--json-schema", json.dumps(SCHEMA),
           "--permission-mode", "dontAsk", "--allowedTools", "Skill",
           "--strict-mcp-config", "--no-session-persistence"]
    try:
        r = subprocess.run(cmd, cwd=C.ROOT, env=_child_env(), capture_output=True, text=True,
                           stdin=subprocess.DEVNULL, timeout=timeout)  # claude -p would read stdin
    except subprocess.TimeoutExpired as e:
        raise TransientError(f"claude -p timed out after {timeout}s") from e
    out = (r.stdout or "") + (r.stderr or "")
    try:
        d = json.loads(r.stdout)
    except json.JSONDecodeError:
        kind = TransientError if any(h.lower() in out.lower() for h in TRANSIENT_HINTS) else RuntimeError
        raise kind(f"claude -p exit {r.returncode}: {out[-2000:]}")
    if d.get("is_error"):
        msg = str(d.get("result") or d)
        kind = TransientError if any(h.lower() in msg.lower() for h in TRANSIENT_HINTS) else RuntimeError
        raise kind(f"claude -p error: {msg[:2000]}")
    plan = d.get("structured_output")
    if plan is None:  # older CLI: JSON in the result text
        plan = json.loads(re.search(r"\{.*\}", d["result"], re.S).group(0))
    return plan


# --- validation -------------------------------------------------------------------------

def clip_words(words, ls, clip):
    a, b = ls[clip["first_line"] - 1], ls[clip["last_line"] - 1]
    return words[a["first"]:b["last"] + 1]


def kept_words(words, ls, clip) -> list[dict]:
    """The clip's words minus its cut lines and cut fillers: what the viewer will hear."""
    cut = set(clip.get("cut_lines") or [])
    out = []
    for n in range(clip["first_line"], clip["last_line"] + 1):
        if n not in cut:
            line = ls[n - 1]
            out += [w for w in words[line["first"]:line["last"] + 1] if not W.is_filler(w["w"])]
    return out


def span_seconds(words, ls, fl, ll, cut_lines=()) -> float:
    """Estimated length of the finished clip for lines fl..ll without `cut_lines`."""
    kw = kept_words(words, ls, {"first_line": fl, "last_line": ll, "cut_lines": list(cut_lines)})
    return round(W.speech_of(kw) * C.FINAL_PER_SPEECH, 1)


def length_options(words, ls, fl, ll) -> str:
    """Concrete in-range alternatives, because summing line lengths by eye is error-prone."""
    lo, hi, target = C.CLIP_MIN_S, C.CLIP_MAX_S, (C.CLIP_MIN_S + C.CLIP_MAX_S) / 2
    opts = []
    for a in range(max(1, fl - 6), min(len(ls), fl + 8) + 1):
        for b in range(max(a, ll - 8), min(len(ls), ll + 6) + 1):
            sec = span_seconds(words, ls, a, b)
            if lo <= sec <= hi and (a, b) != (fl, ll):
                opts.append((abs(a - fl) + abs(b - ll), abs(sec - target), a, b, sec))
    if not opts:
        return "No nearby line range fits; choose a different stretch."
    best = sorted(opts)[:4]
    return "Nearby ranges that fit: " + ", ".join(f"L{a}-L{b} ({sec:.1f}s)" for _, _, a, b, sec in best) + "."


def validate(plan: dict, words: list[dict], ls: list[dict]) -> list[str]:
    errs = []
    rules = load_rules()
    used = []
    for k, c in enumerate(plan["clips"], 1):
        tag = f"clip {k} ({c.get('title')!r})"
        fl, ll = c["first_line"], c["last_line"]
        if not (1 <= fl <= ll <= len(ls)):
            errs.append(f"{tag}: line range L{fl}-L{ll} is outside L1-L{len(ls)}")
            continue
        for a, b in used:
            if fl <= b and ll >= a:
                errs.append(f"{tag}: overlaps another clip (L{a}-L{b})")
        used.append((fl, ll))
        cut = c.get("cut_lines") or []
        bad_cut = [n for n in cut if not (fl < n < ll)]
        if bad_cut:
            errs.append(f"{tag}: cut_lines {bad_cut} must be strictly inside L{fl}-L{ll} "
                        "(to drop the first or last line, move first_line/last_line instead)")
            continue
        kw = kept_words(words, ls, c)
        secs = span_seconds(words, ls, fl, ll, cut)
        if not (C.CLIP_MIN_S <= secs <= C.CLIP_MAX_S):
            hint = length_options(words, ls, fl, ll) if not cut else "Adjust the range or the cut lines."
            errs.append(f"{tag}: L{fl}-L{ll}{f' minus {cut}' if cut else ''} would be ~{secs:.1f}s once "
                        f"edited; must be {C.CLIP_MIN_S:.0f}-{C.CLIP_MAX_S:.0f}s. {hint}")
        for f in c.get("caption_fixes") or []:
            if not (fl <= f["line"] <= ll) or f["line"] in cut:
                errs.append(f"{tag}: caption fix for L{f['line']} is not on a kept line of the clip")
            elif not any(W.bare(x["w"]) == W.bare(f["wrong"]) for x in clip_words(words, ls, {"first_line": f["line"], "last_line": f["line"]})):
                errs.append(f"{tag}: caption fix: {f['wrong']!r} is not a word in L{f['line']}")
            elif not f["right"].strip() or len(f["right"].split()) > 3:
                errs.append(f"{tag}: caption fix {f['wrong']!r} -> {f['right']!r} must be 1-3 words")
        spoken = " ".join(w["w"] for w in kw)
        for h in check_speech(spoken, rules):
            errs.append(f"{tag}: speaker says {h.match!r}, which reads as a claim; pick other lines")
        post = c["caption"] + "\n" + " ".join(c["hashtags"])
        for h in check_copy(post, rules):
            errs.append(f"{tag}: caption/hashtags contain banned phrase {h.match!r}")
        for e in c["emojis"]:
            if not (fl <= e["line"] <= ll) or e["line"] in cut:
                errs.append(f"{tag}: emoji anchor L{e['line']} is not on a kept line of the clip")
            elif not any(W.bare(x["w"]) == W.bare(e["word"]) for x in clip_words(words, ls, {"first_line": e["line"], "last_line": e["line"]})):
                errs.append(f"{tag}: emoji anchor word {e['word']!r} is not in L{e['line']}")
    return errs


def build_prompt(ls: list[dict], meta: dict, feedback: list[str] | None = None) -> str:
    total = sum(l["speech"] for l in ls)
    p = (f"/make-reels\n\n## Channel\n\n{C.channel()}\n\n## Recording\n\n{meta['name']}, recorded {meta['date']}, "
         f"{meta['duration']:.0f}s long, ~{total:.0f}s of speech.\n"
         f"Transcript ({len(ls)} lines):\n\n{render_lines(ls)}\n")
    if feedback:
        p += ("\nYour previous plan was rejected by the validator. Fix every point and return a full "
              "new plan:\n" + "\n".join(f"- {f}" for f in feedback) + "\n")
    return p


def plan_clips(words: list[dict], meta: dict, ask=ask_claude) -> tuple[dict, list[dict]]:
    ls = lines(words)
    total = sum(l["speech"] for l in ls)
    if total < C.CLIP_MIN_S:
        raise PermanentError(f"only {total:.0f}s of speech found; a clip needs {C.CLIP_MIN_S:.0f}s")
    feedback = None
    for attempt in range(1, 4):
        plan = ask(build_prompt(ls, meta, feedback))
        feedback = validate(plan, words, ls)
        if not feedback:
            print(f"plan accepted on attempt {attempt}: {len(plan['clips'])} clip(s)", flush=True)
            return plan, ls
        print(f"plan attempt {attempt} rejected:\n  " + "\n  ".join(feedback), flush=True)
    # Keep the clips that pass on their own rather than failing the whole recording.
    good = [c for c in plan["clips"] if not validate({**plan, "clips": [c]}, words, ls)]
    good = [c for i, c in enumerate(good)
            if not any(c["first_line"] <= o["last_line"] and c["last_line"] >= o["first_line"] for o in good[:i])]
    if good:
        dropped = len(plan["clips"]) - len(good)
        print(f"keeping {len(good)} valid clip(s), dropped {dropped} after 3 attempts", flush=True)
        plan["skipped"] = plan.get("skipped", []) + [f"dropped by validator: {f}" for f in feedback]
        return {**plan, "clips": good}, ls
    raise RuntimeError("plan still invalid after 3 attempts:\n" + "\n".join(feedback))
