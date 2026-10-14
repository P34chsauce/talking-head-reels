"""Weekly riff prompts: what happened in your project this week (read-only git log) plus what
past recordings already covered -> 5-7 prompts about the *experience* of the week.

  reels talking-points [--days N] [--dry-run]
"""
from __future__ import annotations

import collections
import datetime as dt
import re
import subprocess

from . import config as C
from .select import _child_env

KEEP_TYPES = {"feat", "fix", "perf", "refactor", "revert"}
# Trailing ticket refs: "(proj-a1b2c)", "(a1b2c.3)", "(#12)", "(ABC-123)"
NOISE = re.compile(r"\s*\((?:[a-z]+-[a-z0-9]{3,6}(?:\.\d+)?|[a-z0-9]{4,6}(?:\.\d+)?|#\d+|[A-Z]+-\d+|,|\s)+\)")


def git_week(days: int) -> tuple[list[str], dict]:
    """Read-only: `git log` of the project branch. Never writes there."""
    r = subprocess.run(
        ["git", "-C", str(C.PROJECT_REPO), "log", C.PROJECT_BRANCH, f"--since={days} days ago", "--no-merges",
         "--pretty=format:%ad\t%s", "--date=format:%Y-%m-%d %a %H"],
        capture_output=True, text=True, check=True)
    subjects, types, hours, days_seen = [], collections.Counter(), collections.Counter(), collections.Counter()
    for line in r.stdout.splitlines():
        when, subject = line.split("\t", 1)
        date, dow, hour = when.split()
        days_seen[f"{date} {dow}"] += 1
        h = int(hour)
        hours["late night (22:00-04:00)" if h >= 22 or h < 4 else
              "early (04:00-08:00)" if h < 8 else "daytime"] += 1
        m = re.match(r"^(\w+)(?:\([^)]*\))?!?:\s*(.*)", subject)
        kind = m.group(1) if m else "shipped"
        types[kind] += 1
        if kind in KEEP_TYPES or not m:
            subjects.append(f"{date} {kind}: {NOISE.sub('', m.group(2) if m else subject).strip()}")
    stats = {"total": sum(types.values()), "by_type": dict(types.most_common()),
             "when": dict(hours), "per_day": dict(sorted(days_seen.items()))}
    return subjects, stats


def past_topics(limit: int = 10) -> str:
    if not C.NOTES_DIR.exists():
        return "(no previous recordings yet)"
    notes = sorted((p for p in C.NOTES_DIR.glob("*.md")), reverse=True)[:limit]
    out = []
    for p in notes:
        text = p.read_text()
        title = next((l[2:] for l in text.splitlines() if l.startswith("# ")), p.stem)
        summary = re.search(r"## Summary\n(.*?)(\n## |\Z)", text, re.S)
        out.append(f"- {p.stem[:10]} {title}: {summary.group(1).strip()[:400] if summary else ''}")
    return "\n".join(out) or "(no previous recordings yet)"


PROMPT = """You help a creator plan their next talking-to-camera recording session. The videos are
them talking about their own experience of building their project: feelings, decisions, mistakes, rest,
what they learned. NOT feature demos and NOT walkthroughs of commits.

About the channel:
{channel}

Write 5-7 riff prompts for the next session, in Markdown, addressed to the creator ("you"), in the
language and voice the channel brief asks for. For each prompt:
### <n>. <a punchy prompt question>
- **What happened:** one line grounding it in this week's work (plain words, no commit jargon, no IDs)
- **Hook to open with:** a first sentence of 10 words or fewer they could say in the first 3 seconds,
  saying what the story is about and why it matters (no greeting, no "so today")
- **The turn:** the "but…" moment, the thing that didn't go to plan or that they didn't expect
- **Land it on:** the one-line lesson or payoff to finish on, so the story ends on its peak, not a wind-down
- **Dig into:** 2-3 follow-up questions about how it felt, what they'd do differently, what it taught them

Each prompt is one idea that a stranger could follow in a 60-second reel with no other context, and
should leave a viewer something they'd send to a friend.

Rules: mix highs and lows; use the timing pattern (late nights, weekends, busy days) as material where it
fits; don't repeat topics already covered in past recordings; never suggest wording the channel brief
rules out, and never suggest a medical or therapeutic claim.
Start with `# Talking points — week of {week}` and nothing before it.

This week in numbers: {stats}

This week's feature/fix commits ({n} of {total}):
{subjects}

Already covered in past recordings:
{past}
"""


def main(argv: list[str]) -> int:
    days = int(argv[argv.index("--days") + 1]) if "--days" in argv else 7
    if C.PROJECT_REPO is None:
        print('set "project_repo" in private/settings.json to the git repo of the project you talk about')
        return 1
    subjects, stats = git_week(days)
    if not subjects:
        print(f"no commits on {C.PROJECT_BRANCH} in the last {days} days")
        return 1
    week = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    prompt = PROMPT.format(channel=C.channel(), week=week, stats=stats, n=len(subjects), total=stats["total"],
                           subjects="\n".join(subjects[:300]), past=past_topics())
    if "--dry-run" in argv:
        print(prompt)
        return 0
    r = subprocess.run([C.CLAUDE, "-p", prompt, "--tools", "", "--strict-mcp-config",
                        "--no-session-persistence"], cwd=C.ROOT, env=_child_env(),
                       capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=900)
    if r.returncode != 0 or not r.stdout.strip():
        print(f"claude -p failed ({r.returncode}): {(r.stderr or r.stdout)[-1500:]}")
        return 1
    md = r.stdout.strip()
    md = md[md.find("# Talking points"):] if "# Talking points" in md else md
    out_dir = C.NOTES_DIR / "talking-points"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{dt.date.today().isoformat()}.md"
    out.write_text(f"---\ntype: talking-points\nweek_of: {week}\ntags: [reels]\n---\n\n{md}\n")
    print(md)
    print(f"\nsaved to {out}")
    return 0
