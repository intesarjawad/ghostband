"""Score recorded takes against the chart's chord changes and the heard row's agreement.

A take is a WAV recorded from the engine tap, starting in the same controller
tick as `play` so recording sample 0 is `played_samples` 0, plus a sidecar named
`<take>.take.json` with these fields:

    chart          chart path, relative to the repository root or absolute
    preset         preset name the take was played with
    run            1-based repeat number
    recording      WAV path, relative to the sidecar
    play_time_s    scheduler clock when `play` was sent
    beat_audible   [beat, audible_time_s] pairs from the scheduler's event log

Chart beats map to recording seconds through that log. For each chart change from
chord X to chord Y the detector looks for the first frame that starts a 100 ms
stretch in which Y correlates better than X. A change with no such frame is a
miss and counts against the rate. The same detector run on changes shifted by half
a bar is the null: a real rate that does not beat it proves nothing about sync.
"""

import argparse
import json
import sys
from collections import defaultdict
from dataclasses import dataclass
from importlib import import_module
from itertools import pairwise
from math import ceil, floor
from pathlib import Path
from typing import Annotated, Literal, Self

import librosa
import numpy as np
from ghostband.chart import Chart
from ghostband.evaluation import chord_detector
from ghostband.evaluation.provenance import TakeProvenance
from ghostband.importing import read_chart
from ghostband.presets import PRESETS
from ghostband.scheduler import BEAT_EPSILON
from numpy.typing import NDArray
from pydantic import BaseModel, ConfigDict, Field

sf = import_module("soundfile")
ROOT = Path(__file__).resolve().parents[1]
TARGET_S = 0.06
TOLERANCE_S = 0.12
RATE_FLOOR = 0.9
BOOTSTRAP_RESAMPLES = 10_000
BOOTSTRAP_SEED = 20261107
CONFIDENCE = 0.95
TAKE_SUFFIX = ".take.json"


class TakeFile(BaseModel):
    """The sidecar that ties one recording to its chart and scheduler log."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    chart: Annotated[str, Field(min_length=1)]
    preset: Annotated[str, Field(min_length=1)]
    run: Annotated[int, Field(ge=1, strict=True)]
    recording: Annotated[str, Field(min_length=1)]
    play_time_s: float
    beat_audible: Annotated[list[tuple[float, float]], Field(min_length=2)]
    # Absent from takes recorded before the engine's count was kept.
    dropped_frames: Annotated[int, Field(ge=0, strict=True)] | None = None
    mode: Literal["live", "offline", "fake"] = "live"
    provenance: TakeProvenance | None = None


def validate_protocol(takes: list[TakeFile]) -> None:
    """Require all chart/preset/repeat cells and explicit zero-drop evidence before publishing."""
    expected = {
        (chart.name, preset.name, run)
        for chart in (ROOT / "charts").glob("*.chart.json")
        for preset in PRESETS
        for run in (1, 2, 3)
    }
    observed: set[tuple[str, str, int]] = set()
    for take in takes:
        if take.mode != "live":
            raise ValueError(f"{take.recording}: {take.mode} take cannot establish live acceptance")
        if take.dropped_frames != 0:
            raise ValueError(
                f"{take.recording}: dropped_frames must be explicitly 0, got "
                f"{take.dropped_frames}; record a new take"
            )
        key = (Path(take.chart).name, take.preset, take.run)
        if key in observed:
            raise ValueError(f"duplicate sync take: {key}")
        observed.add(key)
    if observed != expected:
        raise ValueError(
            f"incomplete sync protocol: {len(expected - observed)} missing and "
            f"{len(observed - expected)} unexpected chart/preset/run cells"
        )


@dataclass(frozen=True)
class Timeline:
    """Chart beats against recording seconds, linear between logged events."""

    beats: NDArray[np.float64]
    seconds: NDArray[np.float64]

    @classmethod
    def from_log(cls, log: list[tuple[float, float]], play_time_s: float) -> Self:
        by_beat: dict[float, float] = {}
        for beat, audible_s in sorted(log):
            by_beat.setdefault(beat, audible_s - play_time_s)
        beats = np.array(list(by_beat), dtype=np.float64)
        seconds = np.array(list(by_beat.values()), dtype=np.float64)
        if beats.size < 2 or bool((np.diff(seconds) <= 0).any()):
            raise ValueError("beat_audible must cover two beats and move forward in time")
        return cls(beats, seconds)

    def at(self, beat: float) -> float | None:
        """Recording second of a chart beat, or None outside the logged span."""
        if beat < self.beats[0] or beat > self.beats[-1]:
            return None
        return float(np.interp(beat, self.beats, self.seconds))


@dataclass(frozen=True)
class Change:
    """A chart chord change between two different triads at a chart beat."""

    beat: float
    before: str
    after: str


@dataclass(frozen=True)
class Changes:
    items: tuple[Change, ...]
    shared: int
    no_chord: int


def chord_changes(chart: Chart) -> Changes:
    """List chart changes the detector can tell apart.

    Changes between chords that reduce to the same triad, such as C to Cmaj7, are
    skipped and counted, as are changes into or out of N.C.
    """
    items: list[Change] = []
    shared = no_chord = 0
    for previous, current in pairwise(chart.chords):
        before, after = (
            chord_detector.majmin(previous.symbol),
            chord_detector.majmin(current.symbol),
        )
        if chord_detector.NO_CHORD in (before, after):
            no_chord += 1
        elif before == after:
            shared += 1
        else:
            items.append(Change(current.start_beat, before, after))
    return Changes(tuple(items), shared, no_chord)


@dataclass(frozen=True)
class Score:
    """Signed errors in seconds for detected changes, with everything else counted."""

    errors_s: tuple[float, ...]
    misses: int
    outside: int

    @property
    def eligible(self) -> int:
        return len(self.errors_s) + self.misses

    def within(self, limit_s: float) -> float | None:
        """Share of eligible changes detected inside the limit; misses count against it."""
        if not self.eligible:
            return None
        return sum(abs(error) <= limit_s for error in self.errors_s) / self.eligible


def score_changes(
    times: NDArray[np.float64],
    chroma: NDArray[np.float64],
    timeline: Timeline,
    changes: tuple[Change, ...],
    *,
    shift_beats: float = 0.0,
) -> Score:
    """Detect each change near its chart time, optionally with the chart moved by a shift."""
    duration_s = float(times[-1])
    errors: list[float] = []
    misses = outside = 0
    for change in changes:
        chart_s = timeline.at(change.beat + shift_beats)
        reach_s = chord_detector.CHANGE_AFTER_S + chord_detector.CHANGE_HOLD_S
        if chart_s is None or chart_s + reach_s > duration_s:
            outside += 1
            continue
        found = chord_detector.change_time(times, chroma, change.before, change.after, chart_s)
        if found is None:
            misses += 1
        else:
            errors.append(found - chart_s)
    return Score(tuple(errors), misses, outside)


def counted_beats(timeline: Timeline, chart: Chart) -> list[float]:
    """Start of every whole counted beat the log covers, on the chart's own beat grid.

    After a pickup shorter than a bar the bar lines fall off the integers, and a beat
    counted from an integer would straddle the chord change on the bar line.
    """
    phase = chart.beat_phase
    first = ceil(float(timeline.beats[0]) - phase - BEAT_EPSILON)
    last = floor(float(timeline.beats[-1]) - phase + BEAT_EPSILON)
    return [phase + beat for beat in range(first, last)]


def chart_symbol(chart: Chart, beat: float) -> str | None:
    """Return the chart chord sounding through most of one counted beat."""
    longest, symbol = 0.0, None
    for chord in chart.chords:
        overlap = min(chord.end_beat, beat + 1) - max(chord.start_beat, beat)
        if overlap > longest:
            longest, symbol = overlap, chord.symbol
    return symbol


def heard_agreement(
    times: NDArray[np.float64],
    chroma: NDArray[np.float64],
    timeline: Timeline,
    chart: Chart,
) -> tuple[int, int]:
    """Count beats whose heard triad matches the chart, as (agreed, compared).

    Beats the log does not cover, beats the recording ends inside and beats whose
    chart chord is N.C. are left out.
    """
    agreed = compared = 0
    for beat in counted_beats(timeline, chart):
        start_s, end_s = timeline.at(beat), timeline.at(beat + 1)
        symbol = chart_symbol(chart, beat)
        if start_s is None or end_s is None or end_s > times[-1] or symbol is None:
            continue
        expected = chord_detector.majmin(symbol)
        guess = chord_detector.beat_window(times, chroma, start_s, end_s)
        if expected == chord_detector.NO_CHORD or guess is None:
            continue
        compared += 1
        agreed += guess.symbol == expected
    return agreed, compared


@dataclass(frozen=True)
class Unit:
    """One (chart, preset, run) take scored against the chart and the null."""

    chart: str
    preset: str
    run: int
    real: Score
    null: Score
    shared: int
    no_chord: int
    heard_agreed: int
    heard_compared: int


def bootstrap_mean(values: list[float]) -> tuple[float, float, float]:
    """Return the mean and its 95 percent percentile interval over resampled units."""
    if not values:
        raise ValueError("Bootstrap needs at least one unit")
    sample = np.array(values, dtype=np.float64)
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    draws = rng.choice(sample, size=(BOOTSTRAP_RESAMPLES, sample.size)).mean(axis=1)
    tail = (1 - CONFIDENCE) / 2 * 100
    low, high = np.percentile(draws, [tail, 100 - tail])
    return float(sample.mean()), float(low), float(high)


def audio_frames(recording: Path) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """Read a take, mix it to mono and return its centred chroma frames."""
    audio, rate = sf.read(str(recording), dtype="float32", always_2d=True)
    mono = np.asarray(audio, dtype=np.float32).mean(axis=1)
    resampled = np.asarray(
        librosa.resample(
            mono, orig_sr=rate, target_sr=chord_detector.ANALYSIS_RATE, res_type="soxr_hq"
        ),
        dtype=np.float32,
    )
    return chord_detector.frames(resampled)


def score_take(path: Path) -> Unit:
    """Score one sidecar and the recording beside it."""
    take = TakeFile.model_validate_json(path.read_text(encoding="utf-8"))
    if take.dropped_frames:
        raise ValueError(
            f"{path.name}: engine dropped {take.dropped_frames} frames; record a new take "
            "with no dropped frames before scoring sync"
        )
    chart_path = Path(take.chart)
    chart = read_chart(chart_path if chart_path.is_absolute() else ROOT / chart_path)
    timeline = Timeline.from_log(take.beat_audible, take.play_time_s)
    times, chroma = audio_frames(path.parent / take.recording)
    changes = chord_changes(chart)
    half_bar = chart.beats_per_bar / 2
    agreed, compared = heard_agreement(times, chroma, timeline, chart)
    return Unit(
        chart=chart_path.name.removesuffix(".chart.json"),
        preset=take.preset,
        run=take.run,
        real=score_changes(times, chroma, timeline, changes.items),
        null=score_changes(times, chroma, timeline, changes.items, shift_beats=half_bar),
        shared=changes.shared,
        no_chord=changes.no_chord,
        heard_agreed=agreed,
        heard_compared=compared,
    )


def pooled(scores: list[Score], limit_s: float) -> float | None:
    eligible = sum(score.eligible for score in scores)
    if not eligible:
        return None
    hits = sum(abs(error) <= limit_s for score in scores for error in score.errors_s)
    return hits / eligible


def median_error_ms(scores: list[Score]) -> float | None:
    errors = [abs(error) for score in scores for error in score.errors_s]
    return float(np.median(errors)) * 1000 if errors else None


def detector_ceiling(fixtures: Path = ROOT / "fixtures") -> list[Score]:
    """Run the same change detector on the perfectly timed FluidSynth chart renders."""
    scores: list[Score] = []
    for path in sorted((ROOT / "charts").glob("*.chart.json")):
        chart = read_chart(path)
        timeline = Timeline.from_log(
            [(0.0, 0.0), (chart.duration_beats, chart.duration_beats * 60 / chart.tempo_bpm)],
            0.0,
        )
        times, chroma = audio_frames(fixtures / path.name.replace(".chart.json", ".wav"))
        scores.append(score_changes(times, chroma, timeline, chord_changes(chart).items))
    return scores


def _percent(rate: float | None) -> str:
    return "n/a" if rate is None else f"{rate * 100:.1f}%"


def _millis(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.0f}"


@dataclass(frozen=True)
class PresetResult:
    """Sync verdict and heard agreement for every take of one preset."""

    preset: str
    units: int
    real_within_target: float | None
    real_within_tolerance: float | None
    null_within_tolerance: float | None
    median_ms: float | None
    difference: tuple[float, float, float] | None
    heard: tuple[float, float, float] | None

    @property
    def passed(self) -> bool:
        """The rates hold the floor and the paired interval sits above zero."""
        return (
            self.real_within_tolerance is not None
            and self.real_within_tolerance >= RATE_FLOOR
            and self.median_ms is not None
            and self.median_ms < TARGET_S * 1000
            and self.difference is not None
            and self.difference[1] > 0
        )


def summarise(units: list[Unit]) -> list[PresetResult]:
    """Pool each preset's units; the interval resamples (chart, run) units, not changes."""
    by_preset: dict[str, list[Unit]] = defaultdict(list)
    for unit in units:
        by_preset[unit.preset].append(unit)
    results: list[PresetResult] = []
    for preset, group in sorted(by_preset.items()):
        real, null = [unit.real for unit in group], [unit.null for unit in group]
        differences = [
            real_rate - null_rate
            for unit in group
            if (real_rate := unit.real.within(TOLERANCE_S)) is not None
            and (null_rate := unit.null.within(TOLERANCE_S)) is not None
        ]
        agreement = [
            unit.heard_agreed / unit.heard_compared for unit in group if unit.heard_compared
        ]
        results.append(
            PresetResult(
                preset=preset,
                units=len(group),
                real_within_target=pooled(real, TARGET_S),
                real_within_tolerance=pooled(real, TOLERANCE_S),
                null_within_tolerance=pooled(null, TOLERANCE_S),
                median_ms=median_error_ms(real),
                difference=bootstrap_mean(differences) if differences else None,
                heard=bootstrap_mean(agreement) if agreement else None,
            )
        )
    return results


def render_report(
    units: list[Unit], results: list[PresetResult], ceiling: list[Score] | None = None
) -> str:
    """Write the per-chart and per-preset result tables as Markdown."""
    chart_rows = [
        "| Chart | Preset | Takes | Changes | Skipped (shared / N.C. / outside) | Misses "
        "| Median error (ms) | Within 60 ms | Within 120 ms | Null within 120 ms |",
        "| --- | --- | ---: | ---: | --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    groups: dict[tuple[str, str], list[Unit]] = defaultdict(list)
    for unit in units:
        groups[(unit.chart, unit.preset)].append(unit)
    for (chart, preset), group in sorted(groups.items()):
        real, null = [unit.real for unit in group], [unit.null for unit in group]
        skipped = (
            f"{sum(u.shared for u in group)} / {sum(u.no_chord for u in group)} / "
            f"{sum(s.outside for s in real)}"
        )
        chart_rows.append(
            f"| {chart} | {preset} | {len(group)} | {sum(s.eligible for s in real)} | {skipped} "
            f"| {sum(s.misses for s in real)} | {_millis(median_error_ms(real))} "
            f"| {_percent(pooled(real, TARGET_S))} | {_percent(pooled(real, TOLERANCE_S))} "
            f"| {_percent(pooled(null, TOLERANCE_S))} |"
        )
    preset_rows = [
        "| Preset | Takes | Median error (ms) | Within 60 ms | Within 120 ms | Null within 120 ms "
        "| Real minus null (95% interval) | Heard agreement (95% interval) | Verdict |",
        "| --- | ---: | ---: | ---: | ---: | ---: | --- | --- | --- |",
    ]
    preset_rows.extend(
        f"| {result.preset} | {result.units} | {_millis(result.median_ms)} "
        f"| {_percent(result.real_within_target)} | {_percent(result.real_within_tolerance)} "
        f"| {_percent(result.null_within_tolerance)} | {_interval(result.difference)} "
        f"| {_interval(result.heard)} | {'pass' if result.passed else 'fail'} |"
        for result in results
    )
    ceiling_rows: list[str] = []
    if ceiling is not None:
        ceiling_rows = [
            "",
            "Detector check only; FluidSynth fixtures are not generated band output.",
            "",
            "| Reference | Changes | Misses | Median error (ms) | Within 120 ms |",
            "| --- | ---: | ---: | ---: | ---: |",
            f"| Detector ceiling (FluidSynth) | {sum(score.eligible for score in ceiling)} "
            f"| {sum(score.misses for score in ceiling)} | {_millis(median_error_ms(ceiling))} "
            f"| {_percent(pooled(ceiling, TOLERANCE_S))} |",
        ]
    return "\n".join([*chart_rows, "", *preset_rows, *ceiling_rows]) + "\n"


def _interval(value: tuple[float, float, float] | None) -> str:
    if value is None:
        return "n/a"
    mean, low, high = value
    return f"{mean * 100:.1f}% ({low * 100:.1f}% to {high * 100:.1f}%)"


def write_claims(path: Path, results: list[PresetResult]) -> None:
    """Publish sync and heard agreement from a validated protocol, keeping other claims."""
    claims: dict[str, object] = (
        json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    )
    claims["heard_agreement"] = {
        result.preset: {
            "rate": result.heard[0],
            "low": result.heard[1],
            "high": result.heard[2],
            "takes": result.units,
        }
        for result in results
        if result.heard is not None
    }
    claims["sync"] = {
        result.preset: {
            "evidence": "zero_drop_corpus",
            "passed": result.passed,
            "within_120_ms": result.real_within_tolerance,
            "median_error_ms": result.median_ms,
            "null_within_120_ms": result.null_within_tolerance,
            "difference": result.difference,
            "takes": result.units,
        }
        for result in results
    }
    path.write_text(json.dumps(claims, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    """Score every take in a directory and return nonzero if any preset fails."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("takes", type=Path, help="directory holding *.take.json sidecars")
    parser.add_argument("--report", type=Path, help="write the Markdown tables here")
    parser.add_argument("--claims", type=Path, default=ROOT / "docs" / "benchmarks" / "claims.json")
    parser.add_argument("--fixtures", type=Path, default=ROOT / "fixtures")
    parser.add_argument(
        "--diagnostic",
        action="store_true",
        help="score partial or legacy takes without publishing claims or establishing acceptance",
    )
    args = parser.parse_args()
    takes: Path = args.takes
    sidecars = sorted(takes.glob(f"*{TAKE_SUFFIX}"))
    if not sidecars:
        parser.error(f"No {TAKE_SUFFIX} files found in {takes}")
    try:
        if not args.diagnostic:
            validate_protocol(
                [
                    TakeFile.model_validate_json(path.read_text(encoding="utf-8"))
                    for path in sidecars
                ]
            )
        units = [score_take(path) for path in sidecars]
    except ValueError as error:
        parser.error(str(error))
    results = summarise(units)
    report = render_report(units, results, detector_ceiling(args.fixtures))
    if args.diagnostic:
        report = (
            "Diagnostic only: corpus completeness and zero dropped frames are not verified. "
            "The table verdicts compare numeric thresholds only; they are not acceptance. "
            "No claims were published.\n\n" + report
        )
    report_path: Path | None = args.report
    if report_path is not None:
        report_path.write_text(report, encoding="utf-8")
    else:
        sys.stdout.write(report)
    if not args.diagnostic:
        write_claims(args.claims, results)
    failed = [result.preset for result in results if not result.passed]
    if failed:
        label = "Diagnostic thresholds" if args.diagnostic else "Sync acceptance"
        sys.stderr.write(f"{label} failed for: {', '.join(failed)}\n")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
