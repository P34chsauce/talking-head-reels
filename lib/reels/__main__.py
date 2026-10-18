"""reels command line.

  reels process [FILE]     process one file, or sweep inbox/ (what the watcher runs)
  reels retry [FILE]       clear error logs and process again
  reels reprocess archive/FILE [--retranscribe]
                           run a finished take again (old reels/notes kept in work/<job>/previous/)
  reels status             inbox / ready / failures / heavy queue at a glance
  reels check-copy FILE..  banned-phrase check (exit 1 on any hit)
  reels talking-points     draft riff prompts from the last 7 days of your project (writes to notes)
  reels library [WORDS..]  list the asset library, or search it ("reels library desk coffee")
  reels library --scan     describe anything new in library/ now
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from . import config as C
from . import ingest, pipeline


def main(argv: list[str]) -> int:
    if not argv or argv[0] in {"-h", "--help", "help"}:
        print(__doc__.strip())
        return 0
    cmd, args = argv[0], argv[1:]

    if cmd == "_transcribe":
        pipeline.stage_transcribe(Path(args[0]))
        return 0
    if cmd == "_render":
        pipeline.stage_render(Path(args[0]))
        return 0

    if cmd == "process":
        if not args:
            return 1 if pipeline.sweep() else 0
        src = Path(args[0]).resolve()
        if not pipeline.acquire_lock():
            print("another run holds the lock")
            return 1
        try:
            print(pipeline.process(src, wait=False))
            return 0
        except Exception as e:
            pipeline.fail(src, e)
            return 1
        finally:
            pipeline.release_lock()

    if cmd == "retry":
        targets = [Path(a).resolve() for a in args] or [
            p.with_name(p.name.removesuffix(".error.log")) for p in C.INBOX.glob("*.error.log")]
        for t in targets:
            ingest.error_log(t).unlink(missing_ok=True)
            print(f"cleared {t.name}")
        return main(["process"])

    if cmd == "reprocess":
        if not args:
            print("usage: reels reprocess archive/FILE [--retranscribe]")
            return 2
        prev = pipeline.reprocess(Path(args[0]).resolve(), "--retranscribe" in args)
        print(f"previous run kept in {prev}; {Path(args[0]).name} is back in inbox/")
        return 0

    if cmd == "status":
        inbox = [p for p in C.INBOX.glob("*") if ingest.is_candidate(p)] if C.INBOX.exists() else []
        errs = list(C.INBOX.glob("*.error.log")) if C.INBOX.exists() else []
        ready = sorted(C.READY.glob("*.mp4")) if C.READY.exists() else []
        print(f"inbox:  {len(inbox)} video(s)" + "".join(f"\n  - {p.name}" for p in inbox))
        print(f"failed: {len(errs)}" + "".join(
            f"\n  - {p.name} ({'gave up' if ingest.gave_up(p.with_name(p.name[:-10])) else 'will retry'})"
            for p in errs))
        from . import assets as A
        lib_errs = list((C.STATE / "errors").glob("library__*.error.log")) if (C.STATE / "errors").exists() else []
        print(f"library: {len(A.entries())} asset(s)" + (f", {len(lib_errs)} failed (state/errors/)" if lib_errs else ""))
        print(f"ready:  {len(ready)} reel(s)" + "".join(f"\n  - {p.name}" for p in ready[-10:]))
        agent = subprocess.run(["launchctl", "list", "local.reels.watcher"], capture_output=True, text=True)
        print("watcher:", "loaded" if agent.returncode == 0 else "not loaded")
        if C.HEAVY_QUEUE.exists():
            print(subprocess.run([str(C.HEAVY_QUEUE), "--status"], capture_output=True, text=True).stdout.strip())
        return 0

    if cmd == "check-copy":
        from .copy_check import main as check
        return check(args)

    if cmd == "library":
        from . import assets as A
        if args[:1] == ["--scan"]:
            failed = A.scan_library(wait=False)
            for p, e in failed:
                pipeline.fail(p, e)
            return 1 if failed else 0
        found = A.search(" ".join(args)) if args else A.entries()
        for e in found:
            length = f"{e['duration']:.0f}s" if e["duration"] else "still"
            flags = "".join(f" [{x}]" for x in (["poor"] if e["quality"] == "poor" else []) + (
                ["privacy: " + "; ".join(e["privacy"])] if e["privacy"] else []))
            print(f"{e['id']}  {e['recorded_at'][:16]}  {e['kind']:<10} {length:>5} {e['orientation']:<9} "
                  f"{e['description'][:90]}{flags}")
        print(f"{len(found)} asset(s)" + (f" matching {' '.join(args)!r}" if args else ""))
        return 0

    if cmd == "talking-points":
        from .talking_points import main as tp
        return tp(args)

    print(f"unknown command {cmd!r}\n\n{__doc__.strip()}")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
