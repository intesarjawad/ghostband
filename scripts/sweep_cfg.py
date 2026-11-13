"""Paired offline cfg_notes experiment; never publishes live acceptance claims.

Run with --engine and --model-dir pointing at the local engine and installed assets.
The output directory keeps WAVs, command timelines, diagnostic reports and a resumable
sidecar per successful render. Use a fresh output directory after changing render code.
"""

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from ghostband.importing import read_chart
from ghostband.presets import PRESETS, Guidance
from ghostband.render import OfflineEngine, render_chart, write_take

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import measure_identity as identity
from scripts import measure_sync as sync

VALUES = (5, 7, 10, 14)
RUNS = (1, 2, 3)
SWEEP_PRESET = 1


@dataclass(frozen=True)
class Row:
    cfg: int
    preset: int
    result: sync.PresetResult


def choose(rows: list[Row]) -> int:
    """Maximise within-120 rate, breaking ties by median error, then lower guidance."""

    def rank(row: Row) -> tuple[float, float, int]:
        result = row.result
        if result.real_within_tolerance is None or result.median_ms is None:
            raise ValueError("Cannot select guidance without scored changes")
        return (-result.real_within_tolerance, result.median_ms, row.cfg)

    return min(rows, key=rank).cfg


def confirmed_choice(rows: list[Row], candidate: int, identity_status: int) -> int:
    """Keep the baseline unless identity passes and the shared setting improves sync."""
    if identity_status != 0 or candidate == 5:
        return 5
    cells = {(row.preset, row.cfg): row.result.real_within_tolerance for row in rows}
    before_total = after_total = 0.0
    for preset in range(len(PRESETS)):
        before, after = cells[(preset, 5)], cells[(preset, candidate)]
        if before is None or after is None:
            raise ValueError("Shared guidance needs scored changes for every preset")
        before_total += before
        after_total += after
    return candidate if after_total > before_total else 5


def record_cell(  # noqa: PLR0913, PLR0917
    folder: Path,
    binary: Path,
    models: Path,
    cfg: int,
    preset: int,
    chart_path: Path,
    repeat: int,
    *,
    condition: str | None = None,
    slider: int | None = None,
) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    stem = f"{chart_path.name.removesuffix('.chart.json')}-p{preset}-r{repeat}"
    if condition is not None:
        stem += f"-{condition}-s{slider}"
    output = folder / f"{stem}.wav"
    if output.with_suffix(".take.json").is_file():
        return
    chart = read_chart(chart_path)
    if condition in ("chords", "none"):
        chart = chart.model_copy(update={"melody": []})
    if condition == "none":
        chart = chart.model_copy(update={"chords": []})
    held = PRESETS[preset]
    engine = OfflineEngine(
        binary,
        models,
        held.a,
        preset=held.name,
        prompt_b=held.b,
        guidance=Guidance(cfg_notes=cfg),
        seed_rotation=repeat,
    )
    print(f"Render cfg={cfg} {stem}", flush=True)
    result = render_chart(chart, output, engine, slider=float(slider or 0), sweep=condition is None)
    write_take(output, result, engine, chart_path, repeat, condition=condition, slider=slider)


def measure(script: str, folder: Path, report: Path) -> int:
    completed = subprocess.run(  # noqa: S603
        [
            sys.executable,
            str(sync.ROOT / "scripts" / script),
            str(folder),
            "--diagnostic",
            "--report",
            str(report),
        ],
        check=False,
    )
    if completed.returncode not in (0, 1):
        raise RuntimeError(f"{script} exited {completed.returncode}; inspect {folder}")
    return completed.returncode


def sync_row(folder: Path, cfg: int, preset: int) -> Row:
    measure("measure_sync.py", folder, folder / "sync.md")
    units = [sync.score_take(path) for path in sorted(folder.glob("*.take.json"))]
    (result,) = sync.summarise(units)
    return Row(cfg, preset, result)


def percent(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def millis(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.0f}"


def report(rows: list[Row], candidate: int, adopted: int, identity_status: int) -> str:
    lines = [
        "# Offline cfg_notes sweep",
        "",
        "Diagnostic only: frame-blocking MRT2 small, onset mode 1, bass pulse, full charts, "
        "smoothed A-to-B sweep. Three paired seed rotations (1/2/3) per chart. "
        "No live acceptance or public claims are established.",
        "",
        "| cfg_notes | Preset | Takes | Within 120 ms | Median error (ms) | Null within 120 ms |",
        "| ---: | --- | ---: | ---: | ---: | ---: |",
    ]
    for row in rows:
        result = row.result
        lines.append(
            f"| {row.cfg} | {result.preset} | {result.units} "
            f"| {percent(result.real_within_tolerance)} | {millis(result.median_ms)} "
            f"| {percent(result.null_within_tolerance)} |"
        )
    lines += [
        "",
        f"Candidate: {candidate}. Identity command exit: {identity_status}. "
        f"Chosen shared cfg_notes after four-preset confirmation: {adopted}.",
        "",
        "Identity checks all five charts, three conditions, both slider endpoints and "
        "three paired repeats on all four presets. See [the identity report](cfg-identity.md). "
        "The other presets receive matched baseline and candidate sync renders.",
        "",
        "| Preset | Before (cfg 5) within 120 ms "
        f"| Tested candidate (cfg {candidate}) within 120 ms |",
        "| --- | ---: | ---: |",
    ]
    by_cell = {(row.preset, row.cfg): row.result for row in rows}
    for preset, held in enumerate(PRESETS):
        before, after = by_cell.get((preset, 5)), by_cell.get((preset, candidate))
        if before is not None and after is not None:
            lines.append(
                f"| {held.name} | {percent(before.real_within_tolerance)} "
                f"| {percent(after.real_within_tolerance)} |"
            )
    ceiling = sync.detector_ceiling()
    lines += [
        "",
        f"FluidSynth detector ceiling: {percent(sync.pooled(ceiling, 0.12))}, "
        f"{sum(score.eligible for score in ceiling)} eligible changes, "
        f"{sum(score.misses for score in ceiling)} misses.",
    ]
    return "\n".join(lines) + "\n"


def record_identity(folder: Path, binary: Path, models: Path, cfg: int, charts: list[Path]) -> None:
    for preset in range(len(PRESETS)):
        for chart in charts:
            for repeat in RUNS:
                for condition in identity.CONDITIONS:
                    for slider in identity.SLIDERS:
                        record_cell(
                            folder,
                            binary,
                            models,
                            cfg,
                            preset,
                            chart,
                            repeat,
                            condition=condition,
                            slider=slider,
                        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--engine", type=Path, required=True)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    rows: list[Row] = []
    charts = sorted((sync.ROOT / "charts").glob("*.chart.json"))
    for cfg in VALUES:
        folder = args.output / f"cfg-{cfg}-p{SWEEP_PRESET}"
        for chart in charts:
            for repeat in RUNS:
                record_cell(folder, args.engine, args.model_dir, cfg, SWEEP_PRESET, chart, repeat)
        rows.append(sync_row(folder, cfg, SWEEP_PRESET))
        print(sync.render_report([], [rows[-1].result]), flush=True)
    candidate = choose(rows)
    folder = args.output / "identity"
    record_identity(folder, args.engine, args.model_dir, candidate, charts)
    status = measure("measure_identity.py", folder, folder / "identity.md")
    for preset in range(len(PRESETS)):
        if preset == SWEEP_PRESET:
            continue
        for cfg in sorted({5, candidate}):
            folder = args.output / f"cfg-{cfg}-p{preset}"
            for chart in charts:
                for repeat in RUNS:
                    record_cell(folder, args.engine, args.model_dir, cfg, preset, chart, repeat)
            rows.append(sync_row(folder, cfg, preset))
    adopted = confirmed_choice(rows, candidate, status)
    text = report(rows, candidate, adopted, status)
    args.report.write_text(text, encoding="utf-8")
    (args.output / "selection.json").write_text(
        json.dumps({"candidate": candidate, "identity_exit": status, "chosen": adopted}, indent=2)
        + "\n",
        encoding="utf-8",
    )
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
