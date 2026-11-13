"""Score how much of the chart survives in takes made with and without the chart's notes.

Run it as `uv run python scripts/measure_identity.py TAKES`, from the repository root.

A take is a WAV recorded from the engine tap, plus a sidecar named `<take>.take.json`
with these fields:

    chart          chart path, relative to the repository root or absolute
    preset         preset name the take was played with
    condition      what the band was fed: chords_melody, chords or none
    slider         style position, 0 or 1
    run            1-based repeat number
    recording      WAV path, relative to the sidecar
    play_time_s    scheduler clock when `play` was sent
    beat_audible   [beat, audible_time_s] pairs from the scheduler's event log

Harmony is scored per counted beat as the Pearson correlation between the take's
beat-averaged chroma and the chart's pitch-class profile for that beat, chord tones plus
melody notes weighted by how long they sound. Raw cosine is not used: chroma is never
negative, so unrelated music still scores high by chance. The ceiling is the same score on
the chart's rendered fixture, and identity is (chords and melody - no notes) / (ceiling -
no notes). Melody is scored by transcribing the take with Basic Pitch and comparing onsets
with the chart melody, octave ignored, inside a 100 ms window.
"""

import argparse
import asyncio
import json
import sys
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from importlib import import_module
from math import ceil, floor
from pathlib import Path
from typing import Annotated, Literal

import numpy as np
from ghostband.chart import Chart
from ghostband.chords import SEMITONES_PER_OCTAVE, Chord, parse_chord
from ghostband.evaluation.provenance import TakeProvenance
from ghostband.extractors.permissive import Notes, run_permissive
from ghostband.importing import read_chart
from numpy.typing import NDArray
from pydantic import BaseModel, ConfigDict, Field

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.measure_sync import (
    ROOT,
    TAKE_SUFFIX,
    Timeline,
    audio_frames,
    bootstrap_mean,
)

CHORDS_MELODY = "chords_melody"
CHORDS = "chords"
NONE = "none"
CONDITIONS = (CHORDS_MELODY, CHORDS, NONE)
SLIDERS = (0, 1)
RUNS = 3
ONSET_WINDOW_S = 0.1
# Every output pitch class is folded into the octave above middle C for matching.
FOLD_BASE_HZ = 261.6256
# A preset is kept when identity reaches this share of the way from no notes to the ceiling
# at both slider ends, and the interval over charts stays above zero.
IDENTITY_FLOOR = 0.5
SPREAD_FLOOR = 1e-12
CLAIMS_PATH = ROOT / "docs" / "benchmarks" / "claims.json"
FIXTURES = ROOT / "fixtures"

Note = tuple[float, float, int]
Transcribe = Callable[[Path], list[Note]]
Interval = tuple[float, float, float]


class TakeFile(BaseModel):
    """The sidecar that ties one recording to its chart, condition and scheduler log."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    chart: Annotated[str, Field(min_length=1)]
    preset: Annotated[str, Field(min_length=1)]
    condition: Literal["chords_melody", "chords", "none"]
    slider: Literal[0, 1]
    run: Annotated[int, Field(ge=1, strict=True)]
    recording: Annotated[str, Field(min_length=1)]
    play_time_s: float
    beat_audible: Annotated[list[tuple[float, float]], Field(min_length=2)]
    # Absent from takes recorded before the engine's count was kept.
    dropped_frames: Annotated[int, Field(ge=0, strict=True)] | None = None
    mode: Literal["live", "offline", "fake"] = "live"
    provenance: TakeProvenance | None = None


@dataclass(frozen=True)
class Unit:
    """One take's harmony score and, where the chart has a melody, its melody F1."""

    chart: str
    preset: str
    condition: str
    slider: int
    run: int
    harmony: float | None
    melody_f1: float | None


def beat_profile(chart: Chart, beat: int) -> NDArray[np.float64]:
    """Weight each pitch class by the beats chord tones and melody notes sound within one beat."""
    profile = np.zeros(SEMITONES_PER_OCTAVE, dtype=np.float64)
    for chord in chart.chords:
        overlap = min(chord.end_beat, beat + 1) - max(chord.start_beat, beat)
        parsed = parse_chord(chord.symbol)
        if overlap > 0 and isinstance(parsed, Chord):
            profile[list(parsed.pitch_classes)] += overlap
    for note in chart.melody:
        overlap = min(note.end_beat, beat + 1) - max(note.start_beat, beat)
        if overlap > 0:
            profile[note.pitch % SEMITONES_PER_OCTAVE] += overlap
    return profile


def pearson(left: NDArray[np.float64], right: NDArray[np.float64]) -> float | None:
    """Correlate two vectors, or None when either has no spread to correlate."""
    centred_left, centred_right = left - left.mean(), right - right.mean()
    norms = float(np.linalg.norm(centred_left)), float(np.linalg.norm(centred_right))
    if min(norms) < SPREAD_FLOOR:
        return None
    return float(centred_left @ centred_right / (norms[0] * norms[1]))


def harmony_score(
    times: NDArray[np.float64],
    chroma: NDArray[np.float64],
    timeline: Timeline,
    chart: Chart,
) -> float | None:
    """Average the per-beat correlation of heard chroma with the chart's profile.

    Beats the timeline does not cover, beats the recording ends inside and beats where
    either side has no spread, such as N.C. with no melody, are left out.
    """
    scores: list[float] = []
    first, last = ceil(float(timeline.beats[0])), floor(float(timeline.beats[-1]))
    for beat in range(first, last):
        start_s, end_s = timeline.at(beat), timeline.at(beat + 1)
        if start_s is None or end_s is None or end_s > times[-1]:
            continue
        selected = (times >= start_s) & (times < end_s)
        if not bool(selected.any()):
            continue
        score = pearson(np.mean(chroma[:, selected], axis=1), beat_profile(chart, beat))
        if score is not None:
            scores.append(score)
    return float(np.mean(scores)) if scores else None


def _pitch_class_hz(pitch: int) -> float:
    return FOLD_BASE_HZ * 2 ** ((pitch % SEMITONES_PER_OCTAVE) / SEMITONES_PER_OCTAVE)


def melody_f1(heard: list[Note], chart: Chart, timeline: Timeline) -> float | None:
    """Onset F1 of heard notes against the chart melody, or None when it has no scored notes."""
    reference: list[Note] = []
    for note in chart.melody:
        start_s, end_s = timeline.at(note.start_beat), timeline.at(note.end_beat)
        if start_s is not None and end_s is not None:
            reference.append((start_s, end_s, note.pitch))
    if not reference:
        return None
    if not heard:
        return 0.0
    transcription = import_module("mir_eval.transcription")
    _, _, f1, _ = transcription.precision_recall_f1_overlap(
        np.array([[start, end] for start, end, _ in reference]),
        np.array([_pitch_class_hz(pitch) for _, _, pitch in reference]),
        np.array([[start, end] for start, end, _ in heard]),
        np.array([_pitch_class_hz(pitch) for _, _, pitch in heard]),
        onset_tolerance=ONSET_WINDOW_S,
        offset_ratio=None,
    )
    return float(f1)


def transcribe_notes(recording: Path) -> list[Note]:
    """Transcribe a WAV with Basic Pitch through the isolated extractor."""
    notes = Notes.model_validate(asyncio.run(run_permissive("notes", recording))).notes
    return [(start, end, pitch) for start, end, pitch, _ in notes]


def read_take(path: Path) -> TakeFile:
    return TakeFile.model_validate_json(path.read_text(encoding="utf-8"))


def _read_chart(reference: str) -> tuple[str, Chart]:
    path = Path(reference)
    chart = read_chart(path if path.is_absolute() else ROOT / path)
    return path.name.removesuffix(".chart.json"), chart


def ceiling_score(reference: str, fixtures: Path) -> float | None:
    """Score the chart's fixture render, whose beat 0 is its first sample at the chart tempo."""
    name, chart = _read_chart(reference)
    duration_s = chart.duration_beats * 60 / chart.tempo_bpm
    timeline = Timeline.from_log([(0.0, 0.0), (chart.duration_beats, duration_s)], 0.0)
    times, chroma = audio_frames(fixtures / f"{name}.wav")
    return harmony_score(times, chroma, timeline, chart)


def score_take(take: TakeFile, folder: Path, transcribe: Transcribe = transcribe_notes) -> Unit:
    """Score one take and the recording beside its sidecar; only fed melodies are transcribed."""
    name, chart = _read_chart(take.chart)
    timeline = Timeline.from_log(take.beat_audible, take.play_time_s)
    recording = folder / take.recording
    times, chroma = audio_frames(recording)
    f1 = None
    if take.condition != NONE and chart.melody:
        f1 = melody_f1(transcribe(recording), chart, timeline)
    return Unit(
        chart=name,
        preset=take.preset,
        condition=take.condition,
        slider=take.slider,
        run=take.run,
        harmony=harmony_score(times, chroma, timeline, chart),
        melody_f1=f1,
    )


def normalised_identity(chords_melody: float, none: float, ceiling: float) -> float | None:
    """Share of the gap between no notes and the ceiling that the fed notes close."""
    gap = ceiling - none
    return (chords_melody - none) / gap if gap > 0 else None


def _mean(values: list[float]) -> float | None:
    return float(np.mean(values)) if values else None


def cell_mean(units: list[Unit], chart: str, condition: str) -> float | None:
    """Mean harmony over the runs of one chart and condition."""
    return _mean(
        [
            unit.harmony
            for unit in units
            if unit.chart == chart and unit.condition == condition and unit.harmony is not None
        ]
    )


@dataclass(frozen=True)
class IdentityResult:
    """Identity of one preset at one slider end, pooled over charts."""

    preset: str
    slider: int
    charts: int
    skipped: int
    chords_melody: float | None
    chords: float | None
    none: float | None
    ceiling: float | None
    identity: Interval | None

    @property
    def passed(self) -> bool:
        return (
            self.identity is not None
            and self.identity[0] >= IDENTITY_FLOOR
            and self.identity[1] > 0
        )


def summarise_identity(
    units: list[Unit], ceilings: dict[str, float | None]
) -> list[IdentityResult]:
    """Normalise per chart, then take the mean and interval over charts.

    Charts without a usable ceiling, a no-notes score or a gap above zero are skipped
    and counted, not scored as zero.
    """
    groups: dict[tuple[str, int], list[Unit]] = defaultdict(list)
    for unit in units:
        groups[(unit.preset, unit.slider)].append(unit)
    results: list[IdentityResult] = []
    for (preset, slider), group in sorted(groups.items()):
        identities: list[float] = []
        rows: list[tuple[float, float | None, float, float]] = []
        names = sorted({unit.chart for unit in group})
        for name in names:
            both = cell_mean(group, name, CHORDS_MELODY)
            none = cell_mean(group, name, NONE)
            ceiling = ceilings.get(name)
            if both is None or none is None or ceiling is None:
                continue
            identity = normalised_identity(both, none, ceiling)
            if identity is not None:
                identities.append(identity)
                rows.append((both, cell_mean(group, name, CHORDS), none, ceiling))
        results.append(
            IdentityResult(
                preset=preset,
                slider=slider,
                charts=len(identities),
                skipped=len(names) - len(identities),
                chords_melody=_mean([row[0] for row in rows]),
                chords=_mean([row[1] for row in rows if row[1] is not None]),
                none=_mean([row[2] for row in rows]),
                ceiling=_mean([row[3] for row in rows]),
                identity=bootstrap_mean(identities) if identities else None,
            )
        )
    return results


def failing_presets(results: list[IdentityResult]) -> list[str]:
    """Presets that miss the criteria at either slider end, or lack one of them."""
    by_preset: dict[str, dict[int, IdentityResult]] = defaultdict(dict)
    for result in results:
        by_preset[result.preset][result.slider] = result
    return sorted(
        preset
        for preset, found in by_preset.items()
        if any(slider not in found or not found[slider].passed for slider in SLIDERS)
    )


def incomplete_cells(units: list[Unit]) -> list[str]:
    """Name each chart, preset, condition and slider end with fewer than RUNS takes."""
    counts: dict[tuple[str, str, str, int], int] = defaultdict(int)
    for unit in units:
        counts[(unit.chart, unit.preset, unit.condition, unit.slider)] += 1
    expected = {(unit.chart, unit.preset) for unit in units}
    return [
        f"{chart} / {preset} / {condition} / slider={slider}: "
        f"{counts[(chart, preset, condition, slider)]} of {RUNS} takes"
        for chart, preset in sorted(expected)
        for condition in CONDITIONS
        for slider in SLIDERS
        if counts[(chart, preset, condition, slider)] < RUNS
    ]


@dataclass(frozen=True)
class MelodyResult:
    """Paired melody F1, fed melody minus chords only, over takes made with the same seed slot."""

    pairs: int
    difference: Interval | None

    @property
    def survives(self) -> bool:
        """The paired interval sits above zero: the fed melody is heard in the output."""
        return self.difference is not None and self.difference[1] > 0


def summarise_melody(units: list[Unit]) -> MelodyResult:
    """Pair takes by chart, preset, slider and run; the interval resamples the pairs."""
    scores: dict[tuple[str, str, int, int], dict[str, float]] = defaultdict(dict)
    for unit in units:
        if unit.melody_f1 is not None:
            scores[(unit.chart, unit.preset, unit.slider, unit.run)][unit.condition] = (
                unit.melody_f1
            )
    differences = [
        found[CHORDS_MELODY] - found[CHORDS]
        for found in scores.values()
        if CHORDS_MELODY in found and CHORDS in found
    ]
    return MelodyResult(len(differences), bootstrap_mean(differences) if differences else None)


def _decimal(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.3f}"


def _interval(value: Interval | None) -> str:
    if value is None:
        return "n/a"
    mean, low, high = value
    return f"{mean:.3f} ({low:.3f} to {high:.3f})"


def render_report(results: list[IdentityResult], melody: MelodyResult) -> str:
    """Write the identity and melody tables as Markdown."""
    rows = [
        "| Preset | Slider | Charts | Skipped | Chords and melody | Chords only | No notes "
        "| Ceiling | Normalised identity (95% interval) | Verdict |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |",
    ]
    rows.extend(
        f"| {result.preset} | {result.slider} | {result.charts} | {result.skipped} "
        f"| {_decimal(result.chords_melody)} | {_decimal(result.chords)} | {_decimal(result.none)} "
        f"| {_decimal(result.ceiling)} | {_interval(result.identity)} "
        f"| {'pass' if result.passed else 'fail'} |"
        for result in results
    )
    melody_rows = [
        "| Pairs | Melody F1, chords and melody minus chords only (95% interval) "
        "| Melody survives |",
        "| ---: | --- | --- |",
        f"| {melody.pairs} | {_interval(melody.difference)} | {str(melody.survives).lower()} |",
    ]
    return "\n".join([*rows, "", *melody_rows]) + "\n"


def write_claims(path: Path, melody: MelodyResult) -> bool:
    """Merge the melody verdict into the claims file, keeping other claims.

    Nothing is written when no pair of takes could be scored.
    """
    if melody.difference is None:
        return False
    claims: dict[str, object] = (
        json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    )
    mean, low, high = melody.difference
    claims["melody_survives"] = melody.survives
    claims["melody_difference"] = {"mean": mean, "low": low, "high": high, "pairs": melody.pairs}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(claims, indent=2) + "\n", encoding="utf-8")
    return True


def main() -> int:
    """Score every take in a directory and return nonzero if any preset fails or data is missing."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("takes", type=Path, help="directory holding *.take.json sidecars")
    parser.add_argument("--report", type=Path, help="write the Markdown tables here")
    parser.add_argument("--claims", type=Path, default=CLAIMS_PATH)
    parser.add_argument("--fixtures", type=Path, default=FIXTURES)
    parser.add_argument(
        "--diagnostic", action="store_true", help="score offline renders without publishing claims"
    )
    args = parser.parse_args()
    takes: Path = args.takes
    sidecars = sorted(takes.glob(f"*{TAKE_SUFFIX}"))
    if not sidecars:
        parser.error(f"No {TAKE_SUFFIX} files found in {takes}")
    units: list[Unit] = []
    ceilings: dict[str, float | None] = {}
    for path in sidecars:
        take = read_take(path)
        if take.mode != "live" and not args.diagnostic:
            parser.error(
                "offline renders require --diagnostic; they cannot establish live acceptance"
            )
        unit = score_take(take, path.parent)
        units.append(unit)
        if unit.chart not in ceilings:
            ceilings[unit.chart] = ceiling_score(take.chart, args.fixtures)
    results = summarise_identity(units, ceilings)
    melody = summarise_melody(units)
    report = render_report(results, melody)
    if args.diagnostic:
        report = "Diagnostic only: offline identity comparison; no claims published.\n\n" + report
    report_path: Path | None = args.report
    if report_path is not None:
        report_path.write_text(report, encoding="utf-8")
    else:
        sys.stdout.write(report)
    if not args.diagnostic:
        write_claims(args.claims, melody)
    missing = incomplete_cells(units)
    for cell in missing:
        sys.stderr.write(f"Incomplete: {cell}\n")
    failed = failing_presets(results)
    if failed:
        sys.stderr.write(f"Identity acceptance failed for: {', '.join(failed)}\n")
    return 1 if failed or missing else 0


if __name__ == "__main__":
    sys.exit(main())
