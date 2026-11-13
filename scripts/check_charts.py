"""Validate distributable charts and optionally render listening previews."""

import argparse
import asyncio
import sys
from pathlib import Path

from ghostband.chart import Chart
from ghostband.chords import parse_chord

ROOT = Path(__file__).resolve().parents[1]


def check_chart(path: Path) -> Chart:
    """Require schema validity, publishable rights and a meaningful source."""
    chart = Chart.model_validate_json(path.read_text(encoding="utf-8"))
    if chart.rights not in {"public-domain", "own-work"}:
        raise ValueError("rights must be public-domain or own-work")
    if not chart.source.strip():
        raise ValueError("source must name the composition or recording source")
    for chord in chart.chords:
        parse_chord(chord.symbol)
    return chart


async def _run(command: list[str]) -> str:
    process = await asyncio.create_subprocess_exec(
        *command, cwd=ROOT, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
    )
    output, _ = await process.communicate()
    if process.returncode != 0:
        raise RuntimeError(output.decode("utf-8", errors="replace").strip())
    return output.decode("utf-8")


async def _changed_paths(ref: str) -> set[Path]:
    command = ["/usr/bin/git", "rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}"]
    revision = (await _run(command)).strip()
    command = ["/usr/bin/git", "diff", "--name-only", "--diff-filter=ACMR", "-z", revision, "--"]
    result = await _run(command)
    return {ROOT / name for name in result.split("\0") if name}


async def main() -> int:
    """Check chart files or directories, returning nonzero for any invalid chart."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", type=Path, default=[ROOT / "charts"])
    parser.add_argument("--changed-since")
    parser.add_argument("--render-preview", type=Path)
    args = parser.parse_args()
    paths: list[Path] = args.paths
    charts = sorted(
        {
            chart.resolve()
            for path in paths
            for chart in (path.glob("*.chart.json") if path.is_dir() else [path])
        }
    )
    if not charts:
        parser.error("No chart files found")
    try:
        if args.changed_since is not None:
            changed = await _changed_paths(args.changed_since)
            charts = [path for path in charts if path in changed]
        invalid = False
        for path in charts:
            try:
                check_chart(path)
            except (OSError, ValueError) as error:
                print(f"{path}: {error}", file=sys.stderr)
                invalid = True
        if invalid:
            return 1
        if args.render_preview is not None and charts:
            command = [
                "/bin/bash",
                str(ROOT / "scripts/render_fixtures.sh"),
                "--preview",
                str(args.render_preview.resolve()),
                *map(str, charts),
            ]
            print(await _run(command), end="")
    except (OSError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        return 1
    print(f"Checked {len(charts)} charts")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
