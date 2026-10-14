"""Banned-phrase check for post copy and clip speech.

    python -m reels.copy_check FILE...    exit 1 and list hits if any file fails
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

from .config import config_file


# Engagement bait: asks Instagram says it won't recommend to non-followers (comment a set word, number or
# emoji; tag, vote, react; stacked asks). Built in, so a private banned-phrases.txt can't drop them.
# Open questions and plain asks ("send this to the friend who...") are fine.
BAIT = [
    r"\b(comment|type|drop|reply)\s+(below\s+)?(with\s+)?(the word|a word|a number|an emoji|yes|no)\b",
    r"\b(comment|type|drop|reply)\s+(below\s+)?(with\s+)?(an?\s+)?"
    r"([\"“][^\"”\n]{1,20}[\"”]|'[^'\n]{1,20}'|[\U0001F300-\U0001FAFF☀-➿])",
    r"\btag\s+(a|your|someone|\d+|two|three|a few)\b",
    r"\b(like|double[- ]tap)\s+(this\s+)?if\b",
    r"\bfollow\s+for\s+follow\b|\bf4f\b",
    r"\b(like|share|comment)\s*(,|\+|&|and)\s*(comment|share|follow|subscribe|save)\b",
    r"\bshare\s+(this\s+)?(to|on)\s+your\s+stor(y|ies)\b",
    r"\b(vote|react)\s+with\b",
]


@dataclass(frozen=True)
class Hit:
    rule: str
    match: str
    section: str


def load_rules(path: Path | None = None) -> dict[str, list[re.Pattern]]:
    rules: dict[str, list[re.Pattern]] = {"copy": [], "claims": []}
    section = "copy"
    for raw in (path or config_file("banned-phrases.txt")).read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            continue
        rules.setdefault(section, []).append(re.compile(line, re.IGNORECASE))
    rules["bait"] = [re.compile(b, re.IGNORECASE) for b in BAIT]
    return rules


def _scan(text: str, sections: list[str], rules: dict[str, list[re.Pattern]]) -> list[Hit]:
    hits = []
    for section in sections:
        for rx in rules.get(section, []):
            for m in rx.finditer(text):
                hits.append(Hit(rx.pattern, m.group(0), section))
    return hits


def check_copy(text: str, rules=None) -> list[Hit]:
    """Post caption/hashtags and burned-in captions: every rule applies, engagement bait included."""
    rules = rules or load_rules()
    return _scan(text, ["copy", "claims", "bait"], rules)


def check_speech(text: str, rules=None) -> list[Hit]:
    """The spoken words of a clip: only claims apply. Talking about your own mental health is fine."""
    rules = rules or load_rules()
    return _scan(text, ["claims"], rules)


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__.strip())
        return 2
    rules = load_rules()
    failed = False
    for name in argv:
        hits = check_copy(Path(name).read_text(), rules)
        for h in hits:
            failed = True
            what = "engagement bait" if h.section == "bait" else "banned phrase"
            print(f"{name}: {what} {h.match!r} (rule {h.rule})")
    if not failed:
        print(f"ok: {len(argv)} file(s) pass the banned-phrase check")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
