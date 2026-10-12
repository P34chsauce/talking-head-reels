"""Banned-phrase check for post copy and clip speech.

    python -m reels.copy_check FILE...    exit 1 and list hits if any file fails
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

from .config import config_file


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
    return rules


def _scan(text: str, sections: list[str], rules: dict[str, list[re.Pattern]]) -> list[Hit]:
    hits = []
    for section in sections:
        for rx in rules.get(section, []):
            for m in rx.finditer(text):
                hits.append(Hit(rx.pattern, m.group(0), section))
    return hits


def check_copy(text: str, rules=None) -> list[Hit]:
    """Post caption/hashtags and burned-in captions: every rule applies."""
    rules = rules or load_rules()
    return _scan(text, ["copy", "claims"], rules)


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
            print(f"{name}: banned phrase {h.match!r} (rule {h.rule})")
    if not failed:
        print(f"ok: {len(argv)} file(s) pass the banned-phrase check")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
