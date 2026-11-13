#!/usr/bin/env bash
# Check public copy against the copy rules and the measured claims.
#
# Usage: scripts/check_copy.sh [--root DIR] [FILE...]
#
# With no files it checks README.md and the Pages entries ui/demo.html and ui/how-it-works.html
# against every rule, and the rest of docs/ against the
# personal email rule only: the ADRs and design notes say "offline" of rendering outside the live
# loop, which is not a claim to readers. Files given on the command line get every rule.
#
# Every file: no personal email address.
# README first screen: everything above the first `##` heading after the GIF must carry the
# licence line whenever it says "free".
# Claims, read from docs/benchmarks/claims.json and conductor/ghostband/offline_check.json:
#   "slow it" needs a tempo range reaching below 1.0, "speed it up" one reaching above 1.0;
#   "at <n> percent" needs n/100 inside the tempo range;
#   the tune or melody surviving needs `melody_survives: true`;
#   "nothing uploaded", "nothing leaves" or "offline" need a passing offline check for the
#   version in pyproject.toml.
set -euo pipefail

repo="$(cd "$(dirname "$0")/.." && pwd)"
exec uv run --frozen --project "$repo" python - "$repo" "$@" <<'PY'
import argparse
import re
import sys
import tomllib
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel

if TYPE_CHECKING:
    from collections.abc import Callable

REPO = Path(sys.argv.pop(1))
LICENCE_LINE = (
    "Code Apache-2.0. Model weights CC-BY-4.0 with Google's usage terms; Google claims no "
    "rights in what you make with them. Optional SheetSage2 chart upgrade: non-commercial."
)
PERSONAL_EMAIL = re.compile(r"@(gmail|outlook|icloud)\.", re.IGNORECASE)
FREE = re.compile(r"\bfree\b", re.IGNORECASE)
LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
SLOW = re.compile(r"\bslow(?:s|ing)? (?:it|them)(?: down)?\b", re.IGNORECASE)
SPEED = re.compile(r"\bspeed(?:s|ing)? (?:it |them )?up\b", re.IGNORECASE)
PERCENT = re.compile(r"\bat (\d+(?:\.\d+)?) ?(?:percent\b|%)", re.IGNORECASE)
MELODY = re.compile(
    r"\b(?:tune|melody) (?:survives|stays|is kept)\b|\bkeeps? (?:the|your) (?:tune|melody)\b",
    re.IGNORECASE,
)
OFFLINE = re.compile(r"\bnothing (?:is )?uploaded\b|\bnothing leaves\b|\boffline\b", re.IGNORECASE)


class ClaimsFile(BaseModel):
    tempo_range: tuple[float, float] | None = None
    melody_survives: bool = False


class OfflineCheck(BaseModel):
    version: str = ""
    result: str = ""


class Claims:
    """The measured facts that copy may state, read once from the repository."""

    def __init__(self, root: Path) -> None:
        claims_path = root / "docs" / "benchmarks" / "claims.json"
        claims = (
            ClaimsFile.model_validate_json(claims_path.read_bytes())
            if claims_path.exists()
            else ClaimsFile()
        )
        offline_path = root / "conductor" / "ghostband" / "offline_check.json"
        offline = (
            OfflineCheck.model_validate_json(offline_path.read_bytes())
            if offline_path.exists()
            else OfflineCheck()
        )
        pyproject = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
        self.version = str(pyproject["project"]["version"])
        self.tempo_range = claims.tempo_range
        self.melody_survives = claims.melody_survives
        self.offline = offline.result == "pass" and offline.version == self.version

    def slower(self) -> bool:
        return self.tempo_range is not None and self.tempo_range[0] < 1.0

    def faster(self) -> bool:
        return self.tempo_range is not None and self.tempo_range[1] > 1.0

    def tempo(self, percent: str) -> bool:
        if self.tempo_range is None:
            return False
        low, high = self.tempo_range
        return low <= float(percent) / 100 <= high


Problem = tuple[int, str]


def personal_email(lines: list[str]) -> list[Problem]:
    return [
        (number, "uses a personal email address")
        for number, line in enumerate(lines, 1)
        if PERSONAL_EMAIL.search(line)
    ]


def first_screen(lines: list[str]) -> list[Problem]:
    """README's first screen: above the first `##` heading that follows the GIF."""
    gif = next((index for index, line in enumerate(lines) if ".gif" in line.lower()), 0)
    end = next(
        (index for index, line in enumerate(lines) if index > gif and line.startswith("## ")),
        len(lines),
    )
    screen = lines[:end]
    text = " ".join(LINK.sub(r"\1", " ".join(screen)).split())
    if any(FREE.search(line) for line in screen) and LICENCE_LINE not in text:
        line = next(number for number, line in enumerate(screen, 1) if FREE.search(line))
        return [(line, 'says "free" above the first heading without the licence line')]
    return []


def claims(lines: list[str], facts: Claims) -> list[Problem]:
    rules: list[tuple[re.Pattern[str], Callable[[re.Match[str]], bool], str]] = [
        (SLOW, lambda _: facts.slower(), "no measured tempo below 1.0 in claims.json"),
        (SPEED, lambda _: facts.faster(), "no measured tempo above 1.0 in claims.json"),
        (PERCENT, lambda match: facts.tempo(match[1]), "outside the tempo range in claims.json"),
        (MELODY, lambda _: facts.melody_survives, "melody_survives is not true in claims.json"),
        (
            OFFLINE,
            lambda _: facts.offline,
            f"offline_check.json has no passing result for version {facts.version}",
        ),
    ]
    return [
        (number, f'"{match[0]}": {reason}')
        for number, line in enumerate(lines, 1)
        for pattern, holds, reason in rules
        for match in pattern.finditer(line)
        if not holds(match)
    ]


def check(path: Path, facts: Claims, *, email_only: bool) -> list[Problem]:
    lines = path.read_text(encoding="utf-8").splitlines()
    found = personal_email(lines)
    if email_only:
        return found
    found += claims(lines, facts)
    if path.name == "README.md":
        found += first_screen(lines)
    return found


def default_files(root: Path) -> tuple[list[Path], list[Path]]:
    """Public copy, which gets every rule, and the other docs, which get the email rule."""
    public = [root / "README.md", root / "ui" / "demo.html", root / "ui" / "how-it-works.html"]
    public = [path for path in public if path.exists()]
    docs = [
        path
        for path in sorted((root / "docs").rglob("*.md"))
        if path not in public and "references" not in path.relative_to(root / "docs").parts
    ]
    return public, docs


def main() -> int:
    parser = argparse.ArgumentParser(description="Check public copy against the copy rules.")
    parser.add_argument("--root", type=Path, default=REPO, help="repository to read claims from")
    parser.add_argument("files", type=Path, nargs="*", help="files to check; default: all copy")
    args = parser.parse_args()
    root: Path = args.root
    facts = Claims(root)
    public, docs = (args.files, []) if args.files else default_files(root)
    found = [
        f"check_copy: {path}:{line}: {problem}"
        for paths, email_only in ((public, False), (docs, True))
        for path in paths
        for line, problem in check(path, facts, email_only=email_only)
    ]
    for line in found:
        sys.stderr.write(line + "\n")
    return 1 if found else 0


sys.exit(main())
PY
