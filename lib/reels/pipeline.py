"""One recording in, finished reels out. Every stage writes its output into work/<job>/ and is
skipped when that output already exists, so a re-run picks up where the last one stopped."""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import time
import traceback
from pathlib import Path

from . import assets as A
from . import config as C
from . import ingest
from . import media as M
from . import select as S
from . import words as W
from .copy_check import check_copy


def log(msg: str) -> None:
    print(f"[{dt.datetime.now():%H:%M:%S}] {msg}", flush=True)


# --- single-run lock (watcher sweep + manual run must not collide) ------------------------

LOCK = C.STATE / "lock"


def acquire_lock() -> bool:
    C.STATE.mkdir(parents=True, exist_ok=True)
    for _ in range(2):
        try:
            os.mkdir(LOCK)
            (LOCK / "pid").write_text(str(os.getpid()))
            return True
        except FileExistsError:
            try:
                pid = int((LOCK / "pid").read_text())
                os.kill(pid, 0)
                return False  # held by a live process
            except (FileNotFoundError, ValueError, ProcessLookupError):
                shutil.rmtree(LOCK, ignore_errors=True)  # stale
    return False


def release_lock() -> None:
    shutil.rmtree(LOCK, ignore_errors=True)


# --- job ----------------------------------------------------------------------------------

def slugify(text: str, n: int = 6) -> str:
    return "-".join(re.findall(r"[a-z0-9]+", text.lower())[:n]) or "clip"


def recorded_on(src: Path) -> str:
    try:
        tag = M.probe(src)["format"].get("tags", {}).get("creation_time")
        if tag:
            return tag[:10]
    except Exception:
        pass
    return dt.date.fromtimestamp(src.stat().st_mtime).isoformat()


def job_dir(src: Path, sha: str) -> Path:
    return C.WORK / f"{recorded_on(src)}_{slugify(src.stem, 4)}_{sha[:8]}"


def heavy(label: str, *args: str) -> None:
    """Run `python -m reels <args>`, through the machine-wide heavy queue when there is one."""
    cmd = [sys.executable, "-m", "reels", *args]
    if C.HEAVY_QUEUE.exists():
        cmd = [str(C.HEAVY_QUEUE), f"reels {label}", *cmd]
    env = {**os.environ, "PYTHONPATH": str(C.ROOT / "lib")}
    r = subprocess.run(cmd, env=env, cwd=C.ROOT, stdin=subprocess.DEVNULL)
    if r.returncode != 0:
        raise RuntimeError(f"heavy step '{label}' failed with exit {r.returncode} (see log above)")


# --- stages run inside the heavy queue -------------------------------------------------------

def stage_transcribe(job: Path) -> None:
    meta = json.loads((job / "job.json").read_text())
    words = M.transcribe(Path(meta["src"]), job)
    for i, w in enumerate(words):
        w["i"] = i
    (job / "words.json").write_text(json.dumps(words))  # audio16k.wav stays: render snaps cuts to it


def line_cuts(words: list[dict], ls: list[dict], cut_lines: list[int], quietest) -> list[tuple[float, float]]:
    """One cut per run of consecutive dropped lines (retakes, asides, tangents), edges snapped to quiet."""
    spans = []
    for n in sorted(set(cut_lines)):
        first, last = ls[n - 1]["first"], ls[n - 1]["last"]
        if spans and spans[-1][1] == n - 1:
            spans[-1][1] = n
            spans[-1][3] = last
        else:
            spans.append([n, n, first, last])
    out = []
    for _, _, first, last in spans:
        prev_end = words[first - 1]["end"] if first > 0 else 0.0
        next_start = words[last + 1]["start"] if last + 1 < len(words) else words[last]["end"] + 0.3
        a, b = words[first]["start"], words[last]["end"]
        s = quietest(max(prev_end, a - 0.25), a + 0.02)
        e = quietest(b - 0.02, min(b + 0.25, next_start + 0.02))
        out.append((round(s, 3), round(min(e, next_start), 3)))
    return out


def apply_caption_fixes(edited: list[dict], ls: list[dict], fixes: list[dict]) -> list[dict]:
    """Correct mistranscribed words in the burned captions only (audio is untouched)."""
    out = [dict(w) for w in edited]
    for f in fixes:
        line = ls[f["line"] - 1]
        for w in out:
            if line["first"] <= w["i"] <= line["last"] and W.bare(w["w"]) == W.bare(f["wrong"]):
                tail = re.search(r"[^\w']+$", w["w"])
                right = f["right"].strip()
                if tail and not re.search(r"[^\w']$", right):
                    right += tail.group(0)
                w["w"] = right
                break
    return out


def offered_footage(meta: dict) -> tuple[list[dict], str]:
    """The library the clip planner may cut to, extras recorded with this take first."""
    if not C.CUTAWAYS:
        return [], ""
    when = dt.datetime.fromisoformat(meta["recorded_at"]) if meta.get("recorded_at") else None
    assets = A.menu(when)
    linked = {e["id"] for e in A.near(when)} if when else set()
    return assets, A.menu_text(assets, linked)


def cutaway_props(clip: dict, ls: list[dict], edited: list[dict], length: float) -> tuple[list[dict], list]:
    """Line ranges -> times on the edited clip, plus the files Remotion needs."""
    catalogue = {e["id"]: e for e in A.load().values()}
    props, files = [], []
    for n, v in enumerate(clip.get("visuals") or [], 1):
        e = catalogue.get(v["asset"])
        if not e or not A.path_of(e).exists():
            log(f"cutaway {v['asset']} skipped: not in the library any more")
            continue
        a, b = ls[v["from_line"] - 1], ls[v["to_line"] - 1]
        inside = [w for w in edited if a["first"] <= w["i"] <= b["last"]]
        if not inside:
            continue
        start = max(0.0, inside[0]["start"] - 0.1)
        after = next((w for w in edited if w["i"] > b["last"]), None)
        end = after["start"] - 0.05 if after else length
        at = float(v["at"])
        if e["duration"] is not None:  # never run past the end of the footage
            at = max(0.0, min(at, e["duration"] - (end - start)))
            end = min(end, start + e["duration"] - at)
        if end - start < 0.5:
            continue
        src = A.render_source(e)
        name = f"cut{n}{src.suffix}"
        files.append((src, name))
        props.append({"file": name, "kind": "image" if e["duration"] is None else "video",
                      "start": round(start, 3), "end": round(end, 3), "at": round(at, 3), "mode": v["mode"],
                      "aspect": round(e["width"] / e["height"], 4)})
    return props, files


def source_of(meta: dict) -> Path:
    """The take's video: where the job last saw it, else archived after publishing."""
    for p in (Path(meta["src"]), C.ARCHIVE / meta["name"]):
        if p.is_file():
            return p
    raise FileNotFoundError(f"source video {meta['name']} is in neither {meta['src']} nor archive/")


def line_times(ls: list[dict], edited: list[dict]) -> list[dict]:
    """Where each kept line sits on the edited clip: what the review page shows and comments map to."""
    out = []
    for l in ls:
        ws = [w for w in edited if l["first"] <= w["i"] <= l["last"]]
        if ws:
            out.append({"n": l["n"], "start": ws[0]["start"], "end": ws[-1]["end"],
                        "text": " ".join(w["w"] for w in ws)})
    return out


def stage_render(job: Path, only: set[int] | None = None) -> None:
    """Render every clip without a final.mp4, or just the clips in `only` (1-based)."""
    meta = json.loads((job / "job.json").read_text())
    src = source_of(meta)
    words = json.loads((job / "words.json").read_text())
    plan = json.loads((job / "plan.json").read_text())
    ls = json.loads((job / "lines.json").read_text())
    style = C.style()
    speech = M.speech_intervals(src, job)
    swears = W.load_swears()
    wav = job / "audio16k.wav"
    if not wav.exists():
        M.run([C.FFMPEG, "-hide_banner", "-y", "-i", src, "-vn", "-ar", "16000", "-ac", "1",
               "-c:a", "pcm_s16le", wav])
    env = M.Envelope(wav)
    for k, clip in enumerate(plan["clips"], 1):
        cdir = job / "clips" / f"c{k}"
        final = cdir / "final.mp4"
        if (only is not None and k not in only) or (only is None and final.exists()):
            continue
        cdir.mkdir(parents=True, exist_ok=True)
        log(f"clip {k}/{len(plan['clips'])}: {clip['title']}")
        a, b = ls[clip["first_line"] - 1]["first"], ls[clip["last_line"] - 1]["last"]
        cw = W.trim_leading_fillers(words[a:b + 1])
        a = cw[0]["i"]
        prev_end = words[a - 1]["end"] if a > 0 else 0.0
        next_start = words[b + 1]["start"] if b + 1 < len(words) else meta["duration"]
        start = max(cw[0]["start"] - 0.15, prev_end + 0.02, 0.0)
        end = min(cw[-1]["end"] + 0.35, next_start - 0.02, meta["duration"])

        mode = style.get("swearMode", "cut")
        cuts = W.cut_spans(cw, W.is_filler, env.quietest)
        cuts += line_cuts(words, ls, clip.get("cut_lines") or [], env.quietest)
        if mode == "cut":
            cuts += W.swear_spans(cw, swears, env.quietest)
        keep = M.clip_keep(speech, start, end, cuts)
        M.render_edit(src, keep, cdir / "edit.mp4", cdir / "edit.wav")

        edited = apply_caption_fixes(W.remap(cw, keep), ls, clip.get("caption_fixes") or [])
        mute = []
        if mode == "mute":
            mute = [(w["start"], w["end"]) for w in edited if W.is_swear(w["w"], swears)]
            edited = [{**w, "w": W.mask(w["w"])} if W.is_swear(w["w"], swears) else w for w in edited]
        M.loudnorm(cdir / "edit.wav", cdir / "norm.wav", mute)

        length = W.total(keep)
        phrases = W.phrases(edited, style["wordsPerPhrase"], style["phraseBreakGapS"])
        emojis = []
        for e in clip["emojis"][: style["maxEmojisPerClip"]]:
            line = ls[e["line"] - 1]
            hit = next((w for w in edited if line["first"] <= w["i"] <= line["last"]
                        and W.bare(w["w"]) == W.bare(e["word"])), None)
            if hit:
                emojis.append({"emoji": e["emoji"], "start": max(0.0, hit["start"] - 0.1),
                               "end": min(hit["start"] + 2.2, length)})
        renderer = "remotion"
        cuts_props, cut_files = cutaway_props(clip, ls, edited, length)
        (cdir / "timeline.json").write_text(json.dumps({
            "length": round(length, 3), "lines": line_times(ls, edited), "cutaways": cuts_props}))
        clip_style = style
        if any(v["mode"] == "pip" for v in cuts_props):  # aim the face bubble at this take's face
            face = M.face_box(cdir / "edit.mp4", cdir)
            if face:
                clip_style = {**style, "faceCenter": [face["x"], face["y"]], "faceWidth": face["w"]}
        try:
            props = {"durationInFrames": max(1, round(length * C.FPS)), "fps": C.FPS,
                     "phrases": phrases, "emojis": emojis, "style": clip_style, "visuals": cuts_props}
            M.render_remotion(cdir / "edit.mp4", props, cdir / "captioned.mp4", cut_files)
            M.mux(cdir / "captioned.mp4", cdir / "norm.wav", cdir / "final.tmp.mp4")
        except Exception as e:  # fall back to libass, keep going
            log(f"Remotion failed, using ASS fallback (no emojis, no cutaways): {str(e)[-400:]}")
            renderer = "ass"
            M.render_ass(cdir / "edit.mp4", cdir / "norm.wav", phrases, style, cdir / "final.tmp.mp4")
        lufs = M.measure_lufs(cdir / "final.tmp.mp4")
        dur = M.duration(cdir / "final.tmp.mp4")
        (cdir / "meta.json").write_text(json.dumps({
            "title": clip["title"], "renderer": renderer, "duration": round(dur, 2),
            "lufs": lufs, "segments": len(keep), "source_range": [start, end],
            "swears_cut": sum(1 for w in cw if W.is_swear(w["w"], swears)),
            "cutaways": [{k: v[k] for k in ("file", "start", "end", "at", "mode")} for v in cuts_props]
            if renderer == "remotion" else [],
        }, indent=2))
        os.replace(cdir / "final.tmp.mp4", final)
        log(f"clip {k}: {dur:.1f}s, {lufs:.1f} LUFS, {len(keep)} segments, captions via {renderer}")


# --- the whole thing ------------------------------------------------------------------------

def post_text(clip: dict) -> str:
    return clip["caption"].strip() + "\n\n" + " ".join(clip["hashtags"]) + "\n"


unique = ingest.unique


def add_extra(src: Path, sha: str, speech: str = "") -> str:
    """Not a take: describe it into the library (it moves to archive/extras/)."""
    log(f"{src.name}: not a take, adding it to the library (claude -p, describe-asset skill)")
    e = A.add(src, "inbox", sha=sha, speech=speech)
    ingest.error_log(src).unlink(missing_ok=True)
    ingest.record({"sha256": sha, "status": "done", "kind": "asset", "job": f"asset {e['id']}",
                   "source": src.name, "outputs": [], "at": dt.datetime.now().isoformat(timespec="seconds")})
    return f"done:{e['kind']} {e['id']}: {e['description'][:80]}"


def process(src: Path, wait: bool = True) -> str:
    """Returns 'done', 'skipped:<why>'. Raises on failure (caller writes the error log)."""
    if not ingest.is_candidate(src):
        return "skipped:not a video or image"
    if ingest.gave_up(src):
        return "skipped:failed before (run `reels retry`)"
    if wait and not ingest.due_for_retry(src):
        return "skipped:waiting to retry"
    if wait and not ingest.settled(src):
        return "skipped:still syncing"
    if not ingest.probe_ok(src):
        return "skipped:not readable yet"

    sha = ingest.sha256(src)
    done = ingest.ledger().get(sha)
    if done and done.get("status") == "done":
        dest = unique(C.ARCHIVE / src.name)
        C.ARCHIVE.mkdir(exist_ok=True)
        shutil.move(src, dest)
        return f"skipped:already processed as {done['job']} (moved to archive)"

    if A.is_image(src) or A.not_a_take(A.inspect(src)):
        return add_extra(src, sha)

    job = job_dir(src, sha)
    job.mkdir(parents=True, exist_ok=True)
    meta_file = job / "job.json"
    if not meta_file.exists():
        meta_file.write_text(json.dumps({
            "src": str(src), "name": src.name, "sha256": sha, "date": recorded_on(src),
            "duration": M.duration(src)}, indent=2))
    meta = json.loads(meta_file.read_text())
    meta["src"] = str(src)  # the file may have been renamed by a retry
    meta.setdefault("recorded_at", A.recorded_at(src).isoformat(timespec="seconds"))  # links extras
    meta_file.write_text(json.dumps(meta, indent=2))
    log(f"job {job.name}")

    if not (job / "words.json").exists():
        log("transcribing (heavy queue)")
        heavy(f"transcribe {src.name}", "_transcribe", str(job))

    if not (job / "plan.json").exists():
        words = json.loads((job / "words.json").read_text())
        speech = sum(l["speech"] for l in S.lines(words))
        if speech < C.CLIP_MIN_S:  # too little talking for a clip: it's B-roll with some sound
            result = add_extra(src, sha, " ".join(w["w"] for w in words))
            shutil.rmtree(job)
            return result
        assets, footage = offered_footage(meta)
        log(f"choosing clips (claude -p, make-reels skill; {len(assets)} cutaway(s) offered)")
        plan, ls = S.plan_clips(words, meta, assets=assets, footage=footage)
        (job / "lines.json").write_text(json.dumps(ls))
        (job / "plan.json").write_text(json.dumps(plan, indent=2, ensure_ascii=False))

    plan = json.loads((job / "plan.json").read_text())
    if not all((job / "clips" / f"c{k}" / "final.mp4").exists() for k in range(1, len(plan["clips"]) + 1)):
        log("rendering clips (heavy queue)")
        heavy(f"render {src.name}", "_render", str(job))

    publish(job, meta, plan, src)
    return "done"


def publish(job: Path, meta: dict, plan: dict, src: Path) -> None:
    C.READY.mkdir(exist_ok=True)
    stem = f"{meta['date']}_{plan['slug']}"
    outputs = []
    for k, clip in enumerate(plan["clips"], 1):
        cdir = job / "clips" / f"c{k}"
        text = post_text(clip)
        hits = check_copy(text)
        if hits:  # validated at plan time; this is the belt-and-braces gate before anything ships
            raise S.PermanentError(f"clip {k} caption failed banned-phrase check: {[h.match for h in hits]}")
        name = f"{stem}_{k}-{slugify(clip['title'], 5)}"
        txt = C.READY / f"{name}.txt"
        mp4 = C.READY / f"{name}.mp4"
        tmp = cdir / "post.txt"
        tmp.write_text(text)
        os.replace(tmp, txt)
        shutil.copy2(cdir / "final.mp4", cdir / "ready.tmp.mp4")
        os.replace(cdir / "ready.tmp.mp4", mp4)
        outputs.append(mp4.name)

    words = json.loads((job / "words.json").read_text())
    notes = W.fix_quote_timestamps(plan["notes_md"].rstrip(), words) + "\n"
    header = (f"---\ntype: reel-notes\nrecorded: {meta['date']}\nsource: {meta['name']}\n"
              f"clips: {len(plan['clips'])}\ntags: [reels, building-in-public]\n---\n\n")
    (job / "notes.md").write_text(header + notes)
    if C.NOTES_DIR.parent.exists():
        C.NOTES_DIR.mkdir(exist_ok=True)
        shutil.copy2(job / "notes.md", C.NOTES_DIR / f"{stem}.md")
        log(f"notes -> {C.NOTES_DIR / f'{stem}.md'}")

    C.ARCHIVE.mkdir(exist_ok=True)
    if src.exists():
        shutil.move(src, unique(C.ARCHIVE / src.name))
    ingest.error_log(src).unlink(missing_ok=True)
    ingest.record({"sha256": meta["sha256"], "status": "done", "job": job.name, "source": meta["name"],
                   "outputs": outputs, "at": dt.datetime.now().isoformat(timespec="seconds")})
    # Free disk: finished reels are in ready/, keep only the small text artefacts.
    (job / "audio16k.wav").unlink(missing_ok=True)
    for p in job.glob("clips/*/*"):
        if p.suffix in {".mp4", ".wav"} or p.name == "public":
            shutil.rmtree(p) if p.is_dir() else p.unlink()
    log(f"done: {len(outputs)} reel(s) in ready/")


def reprocess(archived: Path, retranscribe: bool = False) -> Path:
    """Put an archived take back through the pipeline (after changing the editing rules).

    The previous run's reels and notes are moved into work/<job>/previous/ for a before/after
    comparison, its ledger entry is dropped and the take goes back into inbox/ for the watcher.
    """
    sha = ingest.sha256(archived)
    rec = ingest.ledger().get(sha)
    if not rec:
        raise SystemExit(f"{archived.name} isn't in the ledger; just put it in inbox/")
    job = C.WORK / rec["job"]
    prev = job / "previous" / dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    prev.mkdir(parents=True)
    for out in rec.get("outputs", []):
        for f in (C.READY / out, (C.READY / out).with_suffix(".txt")):
            if f.exists():
                shutil.move(f, prev / f.name)
    plan_file = job / "plan.json"
    if plan_file.exists():
        meta = json.loads((job / "job.json").read_text())
        note = C.NOTES_DIR / f"{meta['date']}_{json.loads(plan_file.read_text())['slug']}.md"
        if note.exists():
            shutil.move(note, prev / note.name)
    stale = ["plan.json", "lines.json", "notes.md", "clips"]
    if retranscribe:
        stale += ["words.json", "whisper.json", "aligned.json"]
    for name in stale:
        f = job / name
        if f.exists():
            shutil.move(f, prev / name)
    lines = [l for l in ingest.LEDGER.read_text().splitlines() if l.strip() and json.loads(l)["sha256"] != sha]
    ingest.LEDGER.write_text("".join(l + "\n" for l in lines))
    dest = C.INBOX / archived.name
    shutil.move(archived, dest)
    return prev


PREVIEWS = C.ROOT / "previews"


def preview(target: Path) -> Path:
    """Edit a finished take again with today's rules and asset library, into previews/<job>-<time>/.

    For trying the editor out on old recordings: ready/, archive/, notes and the ledger are never
    touched, and the transcript is reused. `target` is a work/<job> folder or the archived take.
    """
    if target.is_dir():
        job = target
    else:
        rec = ingest.ledger().get(ingest.sha256(target))
        if not rec:
            raise SystemExit(f"{target.name} isn't in the ledger; give its work/<job> folder instead")
        job = C.WORK / rec["job"]
    meta = json.loads((job / "job.json").read_text())
    src = next((p for p in (Path(meta["src"]), C.ARCHIVE / meta["name"], target) if p.is_file()), None)
    if src is None:
        raise SystemExit(f"can't find the source video for {job.name}")
    meta = {**meta, "src": str(src)}
    meta.setdefault("recorded_at", A.recorded_at(src).isoformat(timespec="seconds"))
    out = PREVIEWS / f"{job.name}-{dt.datetime.now():%Y%m%d-%H%M%S}"
    out.mkdir(parents=True)
    (out / "job.json").write_text(json.dumps(meta, indent=2))
    for name in ("words.json", "silence.v3", "audio16k.wav"):
        if (job / name).exists():
            shutil.copy2(job / name, out / name)
    words = json.loads((out / "words.json").read_text())
    assets, footage = offered_footage(meta)
    log(f"preview {job.name}: choosing clips ({len(assets)} cutaway(s) offered)")
    plan, ls = S.plan_clips(words, meta, assets=assets, footage=footage)
    (out / "lines.json").write_text(json.dumps(ls))
    (out / "plan.json").write_text(json.dumps(plan, indent=2, ensure_ascii=False))
    heavy(f"preview {job.name}", "_render", str(out))
    for k, clip in enumerate(plan["clips"], 1):
        name = f"{k}-{slugify(clip['title'], 5)}"
        shutil.copy2(out / "clips" / f"c{k}" / "final.mp4", out / f"{name}.mp4")
        (out / f"{name}.txt").write_text(post_text(clip))
    log(f"preview: {len(plan['clips'])} reel(s) in {out}")
    return out


def fail(src: Path, exc: BaseException) -> None:
    kind = ("PERMANENT" if isinstance(exc, S.PermanentError)
            else "TRANSIENT" if isinstance(exc, S.TransientError) else "ERROR")
    n = ingest.attempts(src) + 1
    ingest.error_log(src).parent.mkdir(parents=True, exist_ok=True)
    with ingest.error_log(src).open("a") as f:
        f.write(f"=== attempt {n} at {dt.datetime.now():%Y-%m-%d %H:%M:%S} [{kind}]\n")
        f.write("".join(traceback.format_exception(exc)) + "\n")
    log(f"FAILED ({kind}, attempt {n}/{C.MAX_ATTEMPTS}): {src.name}: {str(exc)[:300]}")


RESCAN = C.STATE / "rescan"


def sweep(wait: bool = True) -> int:
    """Process every ready file in inbox/, oldest first; keep going until nothing is left that
    could still finish syncing. Returns the number of failures."""
    if not acquire_lock():
        RESCAN.touch()  # the running sweep will look again before it exits
        log("another run holds the lock; asked it to rescan")
        return 0
    failures, started = 0, time.time()
    try:
        while True:
            RESCAN.unlink(missing_ok=True)
            for p, e in A.scan_library(wait=wait, log=log):
                fail(p, e)
                failures += 1
            # Images first, then oldest first: extras are in the library before their take is edited.
            files = sorted((p for p in C.INBOX.iterdir() if ingest.is_candidate(p)),
                           key=lambda p: (not A.is_image(p), p.stat().st_mtime)) if C.INBOX.exists() else []
            syncing = False
            for src in files:
                try:
                    result = process(src, wait=wait)
                    if result in ("skipped:still syncing", "skipped:not readable yet"):
                        syncing = True
                    elif result != "skipped:waiting to retry":
                        log(f"{src.name}: {result}")
                except Exception as e:  # noqa: BLE001 — one bad file must not stop the sweep
                    fail(src, e)
                    failures += 1
            if RESCAN.exists():
                continue
            if syncing and time.time() - started < C.SYNC_WAIT_MAX_S:
                time.sleep(C.SETTLE_SECONDS)
                continue
            break
    finally:
        release_lock()
    return failures
