"""Score whether the band's beat follows the tempo factor, with the default preset.

Run it as `uv run python scripts/measure_tempo.py [TAKES]`, from the repository root.

A take is a WAV recorded from the engine tap with the tempo factor set before `play`,
starting in the same controller tick as `play`, plus a sidecar named `<take>.take.json`
with these fields:

    chart          chart path, relative to the repository root or absolute
    preset         preset name the take was played with, the first bundled preset
    onset_mode     the engine's onset mode during the take, 0 or 1
    tempo_factor   0.5, 0.7, 1.0, 1.3 or 1.5
    run            1-based repeat number
    recording      WAV path, relative to the sidecar
    play_time_s    scheduler clock when `play` was sent
    beat_audible   [beat, audible_time_s] pairs from the scheduler's event log

The engine has no tempo input, so any following comes from the pitch-state timing. Beat This
finds the beats in each recording through the isolated extractor. The reference is what Beat
This finds in the chart's own FluidSynth render (fixtures/<chart>.wav, which starts at chart
beat 0 at the chart tempo): those beats, as chart positions, are placed at the times the
scheduler made them audible. The tracker therefore scores the band against its own reading
of the chart, at the same metrical level, and a render of the chart scores 1.0 against
itself. Detected beats are compared with the reference by the beat F-measure with a 70 ms
window, and the tempo they imply with the reference tempo times the factor. A factor passes
in an onset mode when every chart has all its runs, every chart's mean F-measure reaches the
floor and the median output tempo is within the tolerance of the expected tempo.
The tempo range is the widest run of consecutive passing factors that contains 1.0 in the
onset mode that ships.
"""

import argparse
import asyncio
import csv
import json
import sys
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import Annotated, Literal

import numpy as np
from ghostband.chart import Chart
from ghostband.evaluation.provenance import TakeProvenance
from ghostband.extractors.permissive import Beats, run_permissive
from ghostband.importing import read_chart
from ghostband.presets import PRESETS
from ghostband.session import SHIPPED_ONSET_MODE
from numpy.typing import NDArray
from pydantic import BaseModel, ConfigDict, Field, field_validator

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.measure_identity import CLAIMS_PATH
from scripts.measure_sync import ROOT, TAKE_SUFFIX, Timeline, bootstrap_mean

sf = import_module("soundfile")
ONSET_MODES = (0, 1)
FACTORS = (0.5, 0.7, 1.0, 1.3, 1.5)
RUNS = 3
BEAT_WINDOW_S = 0.07
# A factor holds when the mean beat F-measure reaches this floor.
F_FLOOR = 0.8
# A factor holds when the median output tempo is this close to chart tempo times factor.
TEMPO_TOLERANCE = 0.05
# The extractor's message, in extract/permissive/run.py, when it finds no meter.
NO_METER_ERROR = "Two beats and two downbeats are required"
DEFAULT_TAKES = ROOT / "takes" / "tempo"
RENDERS = ROOT / "fixtures"
REPORT_PATH = ROOT / "docs" / "benchmarks" / "tempo.md"
CSV_PATH = ROOT / "docs" / "benchmarks" / "tempo.csv"
OFFERED_RANGE = (0.5, 1.5)
CSV_FIELDS = (
    "chart",
    "preset",
    "onset_mode",
    "tempo_factor",
    "run",
    "f_measure",
    "reference_beats",
    "detected_beats",
    "output_bpm",
    "expected_bpm",
)

Detect = Callable[[Path], list[float]]
Interval = tuple[float, float, float]


class TakeFile(BaseModel):
    """The sidecar that ties one recording to its chart, tempo factor and scheduler log."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    chart: Annotated[str, Field(min_length=1)]
    preset: Annotated[str, Field(min_length=1)]
    onset_mode: Literal[0, 1]
    tempo_factor: float
    run: Annotated[int, Field(ge=1, strict=True)]
    recording: Annotated[str, Field(min_length=1)]
    play_time_s: float
    beat_audible: Annotated[list[tuple[float, float]], Field(min_length=2)]
    # Absent from takes recorded before the engine's count was kept.
    dropped_frames: Annotated[int, Field(ge=0, strict=True)] | None = None

    mode: Literal["live"] = "live"
    provenance: TakeProvenance | None = None

    @field_validator("tempo_factor")
    @classmethod
    def validate_factor(cls, value: float) -> float:
        if value not in FACTORS:
            raise ValueError(f"must be one of {', '.join(f'{factor:g}' for factor in FACTORS)}")
        return value


@dataclass(frozen=True)
class Unit:
    """One take's beat F-measure and the tempo its beats imply."""

    chart: str
    preset: str
    onset_mode: int
    factor: float
    run: int
    f_measure: float
    reference_beats: int
    detected_beats: int
    output_bpm: float | None
    expected_bpm: float


def reference_beats(timeline: Timeline, positions: NDArray[np.float64]) -> NDArray[np.float64]:
    """Recording seconds of the reference beat positions the scheduler log covers."""
    times = [timeline.at(float(position)) for position in positions]
    return np.array([time for time in times if time is not None and time >= 0], dtype=np.float64)


def output_bpm(beats: NDArray[np.float64]) -> float | None:
    """Tempo from the median gap between detected beats, or None with fewer than two."""
    if beats.size < 2:
        return None
    return float(60 / np.median(np.diff(beats)))


def beat_f_measure(reference: NDArray[np.float64], detected: NDArray[np.float64]) -> float:
    """Score detected beats against the audible ones inside a 70 ms window."""
    if not detected.size:
        return 0.0
    beat = import_module("mir_eval.beat")
    return float(beat.f_measure(reference, detected, f_measure_threshold=BEAT_WINDOW_S))


def detect_beats(recording: Path) -> list[float]:
    """Find beat times in a WAV with Beat This through the isolated extractor.

    A take in which Beat This finds no meter has no beats to score, so it scores zero
    instead of stopping the whole measurement.
    """
    try:
        found = asyncio.run(run_permissive("beats", recording))
    except RuntimeError as error:
        if NO_METER_ERROR in str(error):
            return []
        raise
    return Beats.model_validate(found).beats_s


def render_positions(
    chart: Chart, render: Path, detect: Detect = detect_beats
) -> NDArray[np.float64]:
    """Chart positions of the beats Beat This finds in the chart's own render.

    The render plays chart beat 0 at 0 s at the chart tempo, so these positions carry the
    tracker's metrical level and phase for this chart. Beats past the render's end, 30 s for
    every fixture, have no reference and are not scored.
    """
    found = np.array(sorted(detect(render)), dtype=np.float64)
    if found.size < 2:
        raise ValueError(
            f"Beat This found fewer than two beats in {render}; run make fixtures to re-render it"
        )
    return found * chart.tempo_bpm / 60


def read_take(path: Path) -> TakeFile:
    return TakeFile.model_validate_json(path.read_text(encoding="utf-8"))


def _read_chart(reference: str) -> tuple[str, Chart]:
    path = Path(reference)
    chart = read_chart(path if path.is_absolute() else ROOT / path)
    return path.name.removesuffix(".chart.json"), chart


def score_take(
    take: TakeFile,
    folder: Path,
    positions: NDArray[np.float64],
    detect: Detect = detect_beats,
) -> Unit:
    """Score one take and the recording beside its sidecar against reference `positions`.

    Detected beats outside the reference span, give or take one window, are ignored so
    that audio the reference does not cover cannot count as false beats. Reference beats
    after the recording ends are not scored: the take stops as the chart's last beat falls
    due, so that beat is never in the recording.
    """
    name, chart = _read_chart(take.chart)
    timeline = Timeline.from_log(take.beat_audible, take.play_time_s)
    recorded_s = float(sf.info(str(folder / take.recording)).duration)
    reference = reference_beats(timeline, positions)
    reference = reference[reference <= recorded_s]
    if reference.size < 2:
        raise ValueError("beat_audible must cover two reference beats after play")
    found = np.array(sorted(detect(folder / take.recording)), dtype=np.float64)
    inside = found[
        (found >= reference[0] - BEAT_WINDOW_S) & (found <= reference[-1] + BEAT_WINDOW_S)
    ]
    return Unit(
        chart=name,
        preset=take.preset,
        onset_mode=take.onset_mode,
        factor=take.tempo_factor,
        run=take.run,
        f_measure=beat_f_measure(reference, inside),
        reference_beats=int(reference.size),
        detected_beats=int(inside.size),
        output_bpm=output_bpm(inside),
        expected_bpm=chart.tempo_bpm / float(np.median(np.diff(positions))) * take.tempo_factor,
    )


def _median(values: list[float]) -> float | None:
    return float(np.median(values)) if values else None


def missing_cells(units: list[Unit]) -> list[tuple[int, str, float, int]]:
    """List each onset mode, chart and factor with fewer than RUNS takes, with its count."""
    counts: dict[tuple[int, str, float], int] = defaultdict(int)
    for unit in units:
        counts[(unit.onset_mode, unit.chart, unit.factor)] += 1
    charts = sorted({unit.chart for unit in units})
    return [
        (mode, chart, factor, counts[(mode, chart, factor)])
        for mode in ONSET_MODES
        for chart in charts
        for factor in FACTORS
        if counts[(mode, chart, factor)] < RUNS
    ]


@dataclass(frozen=True)
class FactorResult:
    """Beat following of one tempo factor in one onset mode, across charts and runs."""

    onset_mode: int
    factor: float
    units: int
    complete: bool
    f_measure: Interval | None
    tempo_error: float | None
    chart_f_measure: tuple[tuple[str, float], ...] = ()

    @property
    def passed(self) -> bool:
        """Every run is present, every chart reaches the floor and the tempo is close."""
        if not self.complete or not self.chart_f_measure:
            return False
        return (
            all(score >= F_FLOOR for _, score in self.chart_f_measure)
            and self.tempo_error is not None
            and self.tempo_error <= TEMPO_TOLERANCE
        )


def summarise(units: list[Unit]) -> list[FactorResult]:
    """Score each onset mode and factor; one chart below the floor fails the factor.

    The interval resamples takes, not beats. The tempo error is the median over takes
    of |output tempo / expected tempo - 1|; takes with fewer than two detected beats
    have no tempo and are left out of it.
    """
    incomplete = {(mode, factor) for mode, _, factor, _ in missing_cells(units)}
    results: list[FactorResult] = []
    for mode in ONSET_MODES:
        for factor in FACTORS:
            group = [unit for unit in units if unit.onset_mode == mode and unit.factor == factor]
            errors = [
                abs(unit.output_bpm / unit.expected_bpm - 1)
                for unit in group
                if unit.output_bpm is not None
            ]
            scores = [unit.f_measure for unit in group]
            charts = tuple(
                (
                    chart,
                    float(np.mean([unit.f_measure for unit in group if unit.chart == chart])),
                )
                for chart in sorted({unit.chart for unit in group})
            )
            results.append(
                FactorResult(
                    onset_mode=mode,
                    factor=factor,
                    units=len(group),
                    complete=(mode, factor) not in incomplete,
                    f_measure=bootstrap_mean(scores) if scores else None,
                    tempo_error=_median(errors),
                    chart_f_measure=charts,
                )
            )
    return results


def passing_factors(results: list[FactorResult], onset_mode: int) -> list[float]:
    return [
        result.factor for result in results if result.onset_mode == onset_mode and result.passed
    ]


def tempo_range(passing: list[float]) -> tuple[float, float] | None:
    """Widest run of consecutive tested factors that all pass and contain 1.0, or None."""
    if 1.0 not in passing:
        return None
    middle = FACTORS.index(1.0)
    low = high = middle
    while low > 0 and FACTORS[low - 1] in passing:
        low -= 1
    while high < len(FACTORS) - 1 and FACTORS[high + 1] in passing:
        high += 1
    return FACTORS[low], FACTORS[high]


def _decimal(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.3f}"


def _percent(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def _interval(value: Interval | None) -> str:
    if value is None:
        return "n/a"
    mean, low, high = value
    return f"{mean:.3f} ({low:.3f} to {high:.3f})"


def _bpm(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1f}"


def _charts(value: tuple[tuple[str, float], ...]) -> str:
    if not value:
        return "n/a"
    at_floor = sum(score >= F_FLOOR for _, score in value)
    return f"{at_floor}/{len(value)}"


def render_report(
    units: list[Unit], results: list[FactorResult], shipping_mode: int, preset: str
) -> str:
    """Write the per-factor and per-chart tables and the shipped tempo range as Markdown."""
    factor_rows = [
        "| Onset mode | Factor | Takes | Beat F-measure (95% interval) | Charts at floor "
        "| Median tempo error | Complete | Verdict |",
        "| ---: | ---: | ---: | --- | ---: | ---: | --- | --- |",
    ]
    factor_rows.extend(
        f"| {result.onset_mode} | {result.factor:g} | {result.units} "
        f"| {_interval(result.f_measure)} | {_charts(result.chart_f_measure)} "
        f"| {_percent(result.tempo_error)} "
        f"| {'yes' if result.complete else 'no'} | {'pass' if result.passed else 'fail'} |"
        for result in results
    )
    chart_rows = [
        "| Chart | Onset mode | Factor | Takes | Expected tempo (bpm) "
        "| Median output tempo (bpm) | Mean beat F-measure |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    groups: dict[tuple[str, int, float], list[Unit]] = defaultdict(list)
    for unit in units:
        groups[(unit.chart, unit.onset_mode, unit.factor)].append(unit)
    for (chart, mode, factor), group in sorted(groups.items()):
        outputs = [unit.output_bpm for unit in group if unit.output_bpm is not None]
        chart_rows.append(
            f"| {chart} | {mode} | {factor:g} | {len(group)} | {_bpm(group[0].expected_bpm)} "
            f"| {_bpm(_median(outputs))} "
            f"| {_decimal(float(np.mean([unit.f_measure for unit in group])))} |"
        )
    passing = passing_factors(results, shipping_mode)
    shipped = tempo_range(passing)
    summary = [
        f"Preset: {preset}. Shipping onset mode: {shipping_mode}.",
        f"Passing factors: {', '.join(f'{factor:g}' for factor in passing) or 'none'}.",
    ]
    if shipped is None:
        summary.append("Tempo range: none, 1.0 does not pass.")
    else:
        narrower = shipped[0] > OFFERED_RANGE[0] or shipped[1] < OFFERED_RANGE[1]
        summary.append(
            f"Tempo range: {shipped[0]:g} to {shipped[1]:g}"
            + (", narrower than the 0.5 to 1.5 offered so far." if narrower else ".")
        )
    return "\n".join([*summary, "", *factor_rows, "", *chart_rows]) + "\n"


def write_csv(path: Path, units: list[Unit]) -> None:
    """Write one row per take."""
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(CSV_FIELDS)
        for unit in sorted(units, key=lambda u: (u.onset_mode, u.chart, u.factor, u.run)):
            writer.writerow(
                [
                    unit.chart,
                    unit.preset,
                    unit.onset_mode,
                    f"{unit.factor:g}",
                    unit.run,
                    f"{unit.f_measure:.6f}",
                    unit.reference_beats,
                    unit.detected_beats,
                    "" if unit.output_bpm is None else f"{unit.output_bpm:.3f}",
                    f"{unit.expected_bpm:.3f}",
                ]
            )


def write_claims(
    path: Path, results: list[FactorResult], shipping_mode: int
) -> tuple[float, float] | None:
    """Merge the shipping mode's passing factors and tempo range into the claims file.

    Other claims are kept. Returns the range, which is null in the file when 1.0 fails.
    """
    claims: dict[str, object] = (
        json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    )
    passing = passing_factors(results, shipping_mode)
    shipped = tempo_range(passing)
    claims["tempo_factors_pass"] = passing
    claims["tempo_range"] = list(shipped) if shipped is not None else None
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(claims, indent=2) + "\n", encoding="utf-8")
    return shipped


def main() -> int:
    """Score every take in a directory; nonzero if runs are missing or 1.0 does not pass."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "takes",
        type=Path,
        nargs="?",
        default=DEFAULT_TAKES,
        help="directory holding *.take.json sidecars",
    )
    parser.add_argument("--report", type=Path, default=REPORT_PATH)
    parser.add_argument("--csv", type=Path, default=CSV_PATH)
    parser.add_argument("--claims", type=Path, default=CLAIMS_PATH)
    parser.add_argument(
        "--ships",
        type=int,
        choices=ONSET_MODES,
        default=SHIPPED_ONSET_MODE,
        help="onset mode the app ships; its factors set the claims",
    )
    args = parser.parse_args()
    takes: Path = args.takes
    sidecars = sorted(takes.glob(f"*{TAKE_SUFFIX}"))
    if not sidecars:
        parser.error(f"No {TAKE_SUFFIX} files found in {takes}")
    preset = PRESETS[0].name
    references: dict[str, NDArray[np.float64]] = {}
    units: list[Unit] = []
    for path in sidecars:
        take = read_take(path)
        if take.preset != preset:
            parser.error(f"{path}: preset is {take.preset!r}, expected {preset!r}")
        name, chart = _read_chart(take.chart)
        if name not in references:
            render = RENDERS / f"{name}.wav"
            if not render.is_file():
                parser.error(f"{path}: no render at {render}; run make fixtures")
            references[name] = render_positions(chart, render)
        units.append(score_take(take, path.parent, references[name]))
    results = summarise(units)
    for path in (args.report, args.csv):
        path.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(render_report(units, results, args.ships, preset), encoding="utf-8")
    write_csv(args.csv, units)
    shipped = write_claims(args.claims, results, args.ships)
    missing = missing_cells(units)
    for mode, chart, factor, count in missing:
        sys.stderr.write(
            f"Incomplete: {chart} / onset_mode {mode} / factor {factor:g}: "
            f"{count} of {RUNS} takes\n"
        )
    if shipped is None:
        sys.stderr.write(f"Factor 1.0 fails in onset mode {args.ships}: no tempo range\n")
    return 1 if missing or shipped is None else 0


if __name__ == "__main__":
    sys.exit(main())
