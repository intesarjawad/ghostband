"""README figures must be copied from a file the measurement scripts wrote."""

import subprocess
import sys
from pathlib import Path

from scripts.check_readme_numbers import benchmark_numbers, figures, problems

ROOT = Path(__file__).resolve().parents[2]


def test_only_figures_with_a_measurement_unit_are_checked() -> None:
    readme = (
        "Requires macOS 26, Python 3.12 and uv 0.13.7; about 2.8 GB to download.\n"
        "Code Apache-2.0, read 2026-11-04, $9.99 a month.\n"
        "First sound in 4.2 s; jitter 1.5 ms at 96 bpm, -14 LUFS.\n"
        "Beats within 70 ms 92% of the time, 92 percent of takes, "
        "1.3x and 2\N{MULTIPLICATION SIGN} tempo.\n"
    )
    assert figures(readme) == [
        (3, "4.2"),
        (3, "1.5"),
        (3, "96"),
        (3, "-14"),
        (4, "70"),
        (4, "92"),
        (4, "92"),
        (4, "1.3"),
        (4, "2"),
    ]


def test_fenced_code_is_not_checked() -> None:
    readme = "Run:\n\n```sh\nghostband play song.chart.json --seconds 30 s\n```\nTook 3 s.\n"
    assert figures(readme) == [(6, "3")]


def test_a_figure_must_be_written_the_same_way_in_a_benchmark(tmp_path: Path) -> None:
    (tmp_path / "cold-start.md").write_text("| first sound | 4.21 s |\n", encoding="utf-8")
    (tmp_path / "tempo.csv").write_text("factor,error\n0.8,1.2\n", encoding="utf-8")
    measured = benchmark_numbers(tmp_path)
    assert {"4.21", "0.8", "1.2"} <= measured
    assert problems("First sound in 4.21 s, tempo error 1.2%.\n", measured) == []
    assert problems("First sound in 4.2 s.\n", measured) == [
        "line 1: 4.2 is not in any file under docs/benchmarks/"
    ]
    assert problems("At 80 percent.\n", measured) == [
        "line 1: 80 is not in any file under docs/benchmarks/"
    ]


def run(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        [sys.executable, str(ROOT / "scripts/check_readme_numbers.py"), "--root", str(root)],
        capture_output=True,
        text=True,
        check=False,
    )


def test_the_command_fails_on_an_unmeasured_figure_and_passes_on_a_measured_one(
    tmp_path: Path,
) -> None:
    (tmp_path / "docs" / "benchmarks").mkdir(parents=True)
    (tmp_path / "docs" / "benchmarks" / "cold-start.md").write_text("4.21\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("# Ghostband\n\nFirst sound in 9 s.\n", encoding="utf-8")
    failing = run(tmp_path)
    assert failing.returncode == 1
    assert "README.md line 3: 9 is not in any file" in failing.stderr
    (tmp_path / "README.md").write_text("# Ghostband\n\nFirst sound in 4.21 s.\n", encoding="utf-8")
    assert run(tmp_path).returncode == 0
