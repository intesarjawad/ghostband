"""Fail when a measured figure in the README does not appear in docs/benchmarks/.

Run it as `uv run python scripts/check_readme_numbers.py` from the repository root.

A measured figure is a number followed by a unit of time, tempo, loudness, ratio or percentage
(`4.2 s`, `120 ms`, `96 bpm`, `-14 LUFS`, `1.3x`, `92%`, `92 percent`). Versions, dates, prices
and download sizes carry no such unit and are not checked; fenced code blocks are skipped. Each
figure's number must appear, written the same way, as a number in a file under
docs/benchmarks/, which only the measurement scripts write.
"""

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NUMBER = r"-?\d+(?:\.\d+)?"
FIGURE = re.compile(
    rf"(?<![\w.])(?P<number>{NUMBER})"
    r"\s?(?:ms|s|sec|seconds?|min|minutes?|bpm|dB|LUFS|LU|x|\u00d7|%|percent)(?!\w)"
)
BENCHMARK_NUMBER = re.compile(rf"(?<![\w.]){NUMBER}(?![\d])")


def figures(readme: str) -> list[tuple[int, str]]:
    """Line number and number of every measured figure outside fenced code."""
    found: list[tuple[int, str]] = []
    fenced = False
    for line_number, line in enumerate(readme.splitlines(), 1):
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        if not fenced:
            found.extend((line_number, match["number"]) for match in FIGURE.finditer(line))
    return found


def benchmark_numbers(folder: Path) -> set[str]:
    """Every number written in any file under the benchmarks folder."""
    return {
        match[0]
        for path in folder.rglob("*")
        if path.is_file()
        for match in BENCHMARK_NUMBER.finditer(path.read_text(encoding="utf-8", errors="replace"))
    }


def problems(readme: str, measured: set[str]) -> list[str]:
    return [
        f"line {line}: {number} is not in any file under docs/benchmarks/"
        for line, number in figures(readme)
        if number not in measured
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT, help="repository to check")
    args = parser.parse_args()
    root: Path = args.root
    found = problems(
        (root / "README.md").read_text(encoding="utf-8"),
        benchmark_numbers(root / "docs" / "benchmarks"),
    )
    for line in found:
        sys.stderr.write(f"check_readme_numbers: README.md {line}\n")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
