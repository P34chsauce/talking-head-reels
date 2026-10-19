"""Review page: watch a finished reel, pin comments to moments, adjust cutaways and copy, and re-render
just that reel with a before/after.

  reels review            http://127.0.0.1:8790 on this Mac
  reels review --phone    also on your home Wi-Fi, so the phone can open it (private link)

A reel is either in ready/ (a published take: work/<job>/) or in previews/<run>/. Its edit list is
clip k of the plan.json in that folder. Applying an edit saves the old version under
<job>/review/c<k>/revisions/, re-renders clip k only, and shows the new version next to the old one;
Accept puts it where the reel lives (ready/ syncs to the phone), Revert puts the old one back.
"""
from __future__ import annotations

import copy
import datetime as dt
import json
import mimetypes
import re
import secrets
import shutil
import subprocess
import threading
import uuid
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import assets as A
from . import config as C
from . import ingest
from . import media as M
from . import pipeline as P
from . import select as S
from .copy_check import check_copy

PORT = 8790
PAGE = Path(__file__).with_name("review.html")
TOKEN_FILE = C.STATE / "review-token"


# --- reels ----------------------------------------------------------------------------------

@dataclass
class Reel:
    id: str
    kind: str  # "ready" | "preview"
    job: Path  # folder holding plan.json, lines.json, words.json, clips/
    k: int  # 1-based clip number in plan.json
    mp4: Path  # where the reel lives
    txt: Path | None  # its post copy (ready/ only)

    @property
    def rdir(self) -> Path:
        return self.job / "review" / f"c{self.k}"

    def plan(self) -> dict:
        return json.loads((self.job / "plan.json").read_text())

    def clip(self) -> dict:
        return self.plan()["clips"][self.k - 1]


def reels() -> list[Reel]:
    """Every reel still on disk, newest first."""
    out = []
    for rec in ingest.ledger().values():
        job = C.WORK / rec.get("job", "")
        if rec.get("kind") == "asset" or not (job / "plan.json").exists():
            continue
        for name in rec.get("outputs", []):
            m = re.search(r"_(\d+)-[^_]*\.mp4$", name)
            mp4 = C.READY / name
            if m and mp4.exists():
                out.append(Reel(f"ready~{job.name}~{m.group(1)}", "ready", job, int(m.group(1)), mp4,
                                mp4.with_suffix(".txt")))
    if P.PREVIEWS.exists():
        for d in P.PREVIEWS.iterdir():
            if not (d / "plan.json").exists():
                continue
            for mp4 in d.glob("*.mp4"):
                m = re.match(r"(\d+)-", mp4.name)
                if m:
                    out.append(Reel(f"preview~{d.name}~{m.group(1)}", "preview", d, int(m.group(1)), mp4, None))
    return sorted(out, key=lambda r: r.mp4.stat().st_mtime, reverse=True)


def find(reel_id: str) -> Reel:
    for r in reels():
        if r.id == reel_id:
            return r
    raise KeyError(reel_id)


# --- comments -------------------------------------------------------------------------------

def comments(r: Reel) -> list[dict]:
    f = r.rdir / "comments.json"
    return json.loads(f.read_text()) if f.exists() else []


def save_comments(r: Reel, items: list[dict]) -> None:
    r.rdir.mkdir(parents=True, exist_ok=True)
    (r.rdir / "comments.json").write_text(json.dumps(items, indent=2, ensure_ascii=False))


def add_comment(r: Reel, t: float, text: str) -> dict:
    c = {"id": uuid.uuid4().hex[:8], "t": round(float(t), 2), "text": text.strip(), "done": False,
         "at": dt.datetime.now().isoformat(timespec="seconds")}
    save_comments(r, comments(r) + [c])
    return c


# --- what the page needs ---------------------------------------------------------------------

def timeline(r: Reel) -> dict | None:
    f = r.job / "clips" / f"c{r.k}" / "timeline.json"
    return json.loads(f.read_text()) if f.exists() else None


def clip_lines(r: Reel, pad: int = 6) -> list[dict]:
    """The clip's lines plus a few either side (so "start earlier" has somewhere to go)."""
    ls = json.loads((r.job / "lines.json").read_text())
    c = r.clip()
    lo, hi = max(1, c["first_line"] - pad), min(len(ls), c["last_line"] + pad)
    return [{"n": l["n"], "text": l["text"], "in_clip": c["first_line"] <= l["n"] <= c["last_line"]}
            for l in ls[lo - 1:hi]]


def detail(r: Reel) -> dict:
    meta_f = r.job / "clips" / f"c{r.k}" / "meta.json"
    meta = json.loads(meta_f.read_text()) if meta_f.exists() else {}
    job_meta = json.loads((r.job / "job.json").read_text())
    when = dt.datetime.fromisoformat(job_meta["recorded_at"]) if job_meta.get("recorded_at") else None
    return {
        "id": r.id, "kind": r.kind, "title": r.clip()["title"], "clip": r.clip(), "mp4": str(r.mp4),
        "duration": meta.get("duration"), "cutaways": meta.get("cutaways", []), "timeline": timeline(r),
        "lines": clip_lines(r), "comments": comments(r), "status": STATUS.get(r.id, {"state": "idle"}),
        "candidate": str(r.rdir / "candidate.mp4") if (r.rdir / "candidate.mp4").exists() else None,
        "post": P.post_text(r.clip()),
        "assets": [{"id": e["id"], "kind": e["kind"], "description": e["description"],
                    "duration": e["duration"], "quality": e["quality"], "privacy": e["privacy"]}
                   for e in A.menu(when)],
    }


def summary(r: Reel) -> dict:
    return {"id": r.id, "kind": r.kind, "title": r.clip()["title"], "file": r.mp4.name,
            "updated": dt.datetime.fromtimestamp(r.mp4.stat().st_mtime).isoformat(timespec="minutes"),
            "open_comments": sum(1 for c in comments(r) if not c["done"]),
            "pending": (r.rdir / "candidate.mp4").exists()}


def poster(r: Reel) -> Path:
    out = r.rdir / "poster.jpg"
    if not out.exists() or out.stat().st_mtime < r.mp4.stat().st_mtime:
        r.rdir.mkdir(parents=True, exist_ok=True)
        M.run([C.FFMPEG, "-hide_banner", "-y", "-ss", "1", "-i", r.mp4, "-frames:v", "1", "-vf", "scale=270:-2", out])
    return out


# --- revising with claude -p (revise-reel skill) --------------------------------------------

REVISE_SCHEMA = copy.deepcopy(S.SCHEMA["properties"]["clips"]["items"])
REVISE_SCHEMA["required"] = REVISE_SCHEMA["required"] + ["summary"]
REVISE_SCHEMA["properties"]["summary"] = {"type": "string"}


def _mmss(t: float) -> str:
    return f"{int(t // 60)}:{t % 60:04.1f}"


def revise_prompt(r: Reel, clip: dict, notes: list[dict], feedback: list[str] | None = None) -> str:
    tl = timeline(r)
    if tl:
        where = "\n".join(f"L{l['n']} [{_mmss(l['start'])}-{_mmss(l['end'])}] {l['text']}" for l in tl["lines"])
        cuts = "\n".join(f"- {_mmss(v['start'])}-{_mmss(v['end'])}: {v['mode']} cutaway ({v['file']})"
                         for v in tl["cutaways"]) or "- none"
        times = f"Where each kept line plays in the reel now:\n{where}\n\nCutaways on screen now:\n{cuts}\n"
    else:
        times = "(No line timings for this reel yet: map comment times using the cutaway times and line order.)\n"
    near = "\n".join(f"L{l['n']}{'' if l['in_clip'] else ' (outside the clip)'} {l['text']}" for l in clip_lines(r))
    job_meta = json.loads((r.job / "job.json").read_text())
    when = dt.datetime.fromisoformat(job_meta["recorded_at"]) if job_meta.get("recorded_at") else None
    assets = A.menu(when)
    p = (f"/revise-reel\n\n## Channel\n\n{C.channel()}\n\n"
         f"## The reel now\n\n```json\n{json.dumps(clip, indent=2, ensure_ascii=False)}\n```\n\n{times}\n"
         f"Transcript lines around the clip:\n{near}\n\n"
         f"## Footage you can cut to\n\n{A.menu_text(assets)}\n\n"
         "## The founder's comments\n\n"
         + "\n".join(f"- at {_mmss(n['t'])}: {n['text']}" for n in notes) + "\n")
    if feedback:
        p += "\nYour previous version was rejected by the validator. Fix every point:\n" + "\n".join(f"- {f}" for f in feedback) + "\n"
    return p


def check(r: Reel, clip: dict) -> list[str]:
    words = json.loads((r.job / "words.json").read_text())
    ls = json.loads((r.job / "lines.json").read_text())
    job_meta = json.loads((r.job / "job.json").read_text())
    when = dt.datetime.fromisoformat(job_meta["recorded_at"]) if job_meta.get("recorded_at") else None
    by_id = {e["id"]: e for e in A.menu(when)}
    by_id.update({v["asset"]: e for v in clip.get("visuals") or []  # footage already in the reel stays allowed
                  for e in A.entries() if e["id"] == v["asset"]})
    return S.validate({"clips": [clip]}, words, ls, by_id)


def revise(r: Reel, clip: dict, notes: list[dict], ask=None) -> tuple[dict, str]:
    ask = ask or (lambda p: S.ask_claude(p, schema=REVISE_SCHEMA))
    feedback = None
    for _ in range(3):
        out = ask(revise_prompt(r, clip, notes, feedback))
        summary_text = out.pop("summary", "")
        feedback = check(r, out)
        if not feedback:
            return out, summary_text
    raise RuntimeError("revision still invalid after 3 attempts:\n" + "\n".join(feedback))


# --- apply / accept / revert ------------------------------------------------------------------

STATUS: dict[str, dict] = {}
_one_at_a_time = threading.Lock()


def apply(r: Reel, clip: dict, ask=None, render=None) -> None:
    """Revise (when there are open comments), validate, save the old version, re-render clip k."""
    notes = [c for c in comments(r) if not c["done"]]
    STATUS[r.id] = {"state": "working", "step": "revising with your comments" if notes else "checking"}
    with _one_at_a_time:
        try:
            if notes:
                clip, said = revise(r, clip, notes, ask)
            else:
                errs = check(r, clip)
                if errs:
                    raise ValueError("\n".join(errs))
                said = "Applied your changes."
            hits = check_copy(P.post_text(clip))
            if hits:
                raise ValueError(f"caption fails the copy check: {[h.match for h in hits]}")
            stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
            rev = r.rdir / "revisions" / stamp
            rev.mkdir(parents=True)
            plan = r.plan()
            (rev / "clip.json").write_text(json.dumps(plan["clips"][r.k - 1], indent=2, ensure_ascii=False))
            for name in ("timeline.json", "meta.json"):  # so Revert restores the old timings too
                f = r.job / "clips" / f"c{r.k}" / name
                if f.exists():
                    shutil.copy2(f, rev / name)
            plan["clips"][r.k - 1] = clip
            (r.job / "plan.json").write_text(json.dumps(plan, indent=2, ensure_ascii=False))
            STATUS[r.id] = {"state": "working", "step": "rendering (about 3 minutes)", "summary": said}
            (render or (lambda: P.heavy(f"review {r.mp4.name}", "_render", str(r.job), str(r.k))))()
            shutil.copy2(r.job / "clips" / f"c{r.k}" / "final.mp4", r.rdir / "candidate.mp4")
            (r.rdir / "candidate.json").write_text(json.dumps({"revision": stamp, "summary": said,
                                                               "comments": [n["id"] for n in notes]}))
            STATUS[r.id] = {"state": "ready", "summary": said}
        except Exception as e:  # noqa: BLE001 — shown on the page
            STATUS[r.id] = {"state": "failed", "error": str(e)[-1500:]}


def accept(r: Reel) -> None:
    cand = json.loads((r.rdir / "candidate.json").read_text())
    rev = r.rdir / "revisions" / cand["revision"]
    shutil.copy2(r.mp4, rev / "reel.mp4")  # keep the old reel with its old edit list
    (r.rdir / "candidate.mp4").replace(r.mp4)  # one rename: Syncthing never sees half a file
    if r.txt is not None:
        r.txt.write_text(P.post_text(r.clip()))
    save_comments(r, [{**c, "done": c["done"] or c["id"] in cand["comments"]} for c in comments(r)])
    (r.rdir / "candidate.json").unlink()
    STATUS[r.id] = {"state": "idle", "summary": "Accepted."}


def revert(r: Reel) -> None:
    cand = json.loads((r.rdir / "candidate.json").read_text())
    old = json.loads((r.rdir / "revisions" / cand["revision"] / "clip.json").read_text())
    plan = r.plan()
    plan["clips"][r.k - 1] = old
    (r.job / "plan.json").write_text(json.dumps(plan, indent=2, ensure_ascii=False))
    rev = r.rdir / "revisions" / cand["revision"]
    for name in ("timeline.json", "meta.json"):
        if (rev / name).exists():
            shutil.copy2(rev / name, r.job / "clips" / f"c{r.k}" / name)
    shutil.rmtree(rev)
    (r.rdir / "candidate.mp4").unlink()
    (r.rdir / "candidate.json").unlink()
    STATUS[r.id] = {"state": "idle", "summary": "Reverted to the previous version."}


# --- web server -------------------------------------------------------------------------------

def token() -> str:
    if not TOKEN_FILE.exists():
        TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        TOKEN_FILE.write_text(secrets.token_urlsafe(16))
    return TOKEN_FILE.read_text().strip()


def servable(p: Path) -> bool:
    """Only media of reels and the asset library: never anything else on the Mac."""
    p = p.resolve()
    roots = [C.READY, C.WORK, P.PREVIEWS]
    return p.is_file() and p.suffix.lower() in {".mp4", ".jpg", ".png"} and any(
        p.is_relative_to(x.resolve()) for x in roots if x.exists())


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # quiet
        pass

    def _authorised(self, q: dict) -> bool:
        cookie = self.headers.get("Cookie", "")
        return q.get("token", [""])[0] == token() or f"reels_review={token()}" in cookie

    def _json(self, data, code=200):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _file(self, p: Path):
        size = p.stat().st_size
        start, end = 0, size - 1
        rng = re.match(r"bytes=(\d*)-(\d*)", self.headers.get("Range", ""))
        if rng and (rng.group(1) or rng.group(2)):
            if rng.group(1):
                start = int(rng.group(1))
                end = int(rng.group(2)) if rng.group(2) else end
            else:
                start = size - int(rng.group(2))
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        else:
            self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(p.name)[0] or "application/octet-stream")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        with p.open("rb") as f:
            f.seek(start)
            left = end - start + 1
            while left > 0:
                chunk = f.read(min(1 << 16, left))
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    return
                left -= len(chunk)

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if not self._authorised(q):
            self.send_error(403, "open the link printed by `reels review`")
            return
        try:
            if u.path == "/":
                body = PAGE.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Set-Cookie", f"reels_review={token()}; Path=/; HttpOnly; SameSite=Strict")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif u.path == "/api/reels":
                self._json([summary(r) for r in reels()])
            elif u.path == "/api/reel":
                self._json(detail(find(q["id"][0])))
            elif u.path == "/media":
                p = Path(q["path"][0])
                if not servable(p):
                    self.send_error(404)
                    return
                self._file(p.resolve())
            elif u.path == "/poster":
                self._file(poster(find(q["id"][0])))
            elif u.path == "/thumb":
                e = next(e for e in A.entries() if e["id"] == q["asset"][0])
                self._file(C.ROOT / e["sheet"])
            else:
                self.send_error(404)
        except (KeyError, StopIteration):
            self.send_error(404)

    def do_POST(self):
        u = urlparse(self.path)
        if not self._authorised(parse_qs(u.query)):
            self.send_error(403)
            return
        data = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        try:
            r = find(data["id"])
        except KeyError:
            self.send_error(404)
            return
        if u.path == "/api/comment":
            self._json(add_comment(r, data["t"], data["text"]))
        elif u.path == "/api/comment/delete":
            save_comments(r, [c for c in comments(r) if c["id"] != data["cid"]])
            self._json({"ok": True})
        elif u.path == "/api/apply":
            if STATUS.get(r.id, {}).get("state") == "working":
                self._json({"error": "already working on this reel"}, 409)
                return
            STATUS[r.id] = {"state": "working", "step": "starting"}
            threading.Thread(target=apply, args=(r, data["clip"]), daemon=True).start()
            self._json({"ok": True})
        elif u.path == "/api/accept":
            accept(r)
            self._json({"ok": True})
        elif u.path == "/api/revert":
            revert(r)
            self._json({"ok": True})
        else:
            self.send_error(404)


def lan_ip() -> str | None:
    for iface in ("en0", "en1"):
        r = subprocess.run(["ipconfig", "getifaddr", iface], capture_output=True, text=True)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    return None


def main(args: list[str]) -> int:
    phone = "--phone" in args
    host = "0.0.0.0" if phone else "127.0.0.1"
    server = ThreadingHTTPServer((host, PORT), Handler)
    print(f"review page: http://127.0.0.1:{PORT}/?token={token()}", flush=True)
    if phone:
        ip = lan_ip()
        print(f"on your phone (same Wi-Fi): http://{ip}:{PORT}/?token={token()}" if ip
              else "no Wi-Fi address found; the phone link isn't available")
    print("Ctrl-C to stop")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0
