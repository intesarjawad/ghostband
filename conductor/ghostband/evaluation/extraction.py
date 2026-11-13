"""Score extracted charts against the fixture charts and against GuitarSet and AAM.

Run it as `uv run python -m ghostband.evaluation.extraction`, from the repository root. With
no options it scores the fixture charts in seconds, so beat grids may differ. With
`--output-dir` it scores the datasets fetched by `scripts.fetch_guitarset` and
`scripts.fetch_aam` and writes `extraction.md` and CSV files there.

Melody is scored as onset F1 with octave ignored. Chords are scored as the share of
reference time in which the extracted chord reduces to the same major or minor triad,
and with mir_eval's root and sevenths accuracy. Sections are scored as boundary F1,
counting a boundary found when it lies within three seconds of a reference one and,
for AAM, also within half a second; the song's start and end are not boundaries.
Beats and downbeats are scored as the mir_eval F-measure within 70 ms and keys with the
mir_eval weighted key score.

Dataset references are charts whose beats are seconds (60 bpm, no recorded beat times), so
the scores above apply to them unchanged. An excerpt is one track; every interval is a
percentile bootstrap over tracks, seeded so a rerun reproduces it.
"""

import argparse
import asyncio
import bisect
import csv
import io
import os
import re
import struct
import sys
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from importlib import import_module
from itertools import pairwise
from pathlib import Path

import librosa
import numpy as np
from numpy.typing import NDArray
from pydantic import BaseModel, ConfigDict, TypeAdapter

from ghostband.chart import Chart
from ghostband.chords import SEMITONES_PER_OCTAVE, ChordQuality, NoChord, parse_chord
from ghostband.evaluation import chord_detector
from ghostband.evaluation.chord_detector import majmin
from ghostband.extractors.permissive import run_permissive
from ghostband.extractors.sheetsage2 import run_sheetsage2
from ghostband.importing import ROOT

ONSET_WINDOW_S = 0.05
BOUNDARY_WINDOW_0P5_S = 0.5
BOUNDARY_WINDOW_S = 3.0
MELODY_F1_FLOOR = 0.50
MAJMIN_FLOOR = 0.60
# Every pitch class is folded into the octave above middle C for matching.
FOLD_BASE_HZ = 261.6256
LONG_EXCERPT_S = 30.0
BOOTSTRAP_RESAMPLES = 10_000
BOOTSTRAP_SEED = 20261107
CONFIDENCE = 0.95
CACHE_ROOT = Path.home() / "Library/Caches/ghostband"
MIDI_NOTE_LIMIT = 127
MICROSECONDS_PER_SECOND = 1_000_000
DEFAULT_TEMPO_US = 500_000
# Repeated bars differ by float noise, far below the 14 ms or more between real beats.
OVERLAP_S = 1e-3

sf = import_module("soundfile")
Seconds = NDArray[np.float64]
Extractor = Callable[[Path], Awaitable[Chart]]


@dataclass(frozen=True)
class Scores:
    """One extracted chart's agreement with its reference, each between 0 and 1."""

    melody_f1: float
    majmin: float
    segment_f: float


def beat_seconds(chart: Chart, beats: list[float]) -> Seconds:
    """Place chart beats on the clock, using the recording's beat times when it has them."""
    positions = np.array(beats, dtype=np.float64)
    times = np.array(chart.beat_times_s, dtype=np.float64)
    if len(times) < 2:
        return positions * 60 / chart.tempo_bpm
    period_s = float(np.median(np.diff(times)))
    last = len(times) - 1
    inside = np.interp(positions, np.arange(len(times), dtype=np.float64), times)
    return np.where(
        positions < 0,
        times[0] + positions * period_s,
        np.where(positions > last, times[-1] + (positions - last) * period_s, inside),
    )


def _f1(matched: int, reference: int, estimate: int) -> float:
    return 2 * matched / (reference + estimate) if reference + estimate else 0.0


def _notes(chart: Chart) -> tuple[Seconds, Seconds]:
    """Return onset-offset intervals in seconds and each note's pitch class as a frequency."""
    onsets = beat_seconds(chart, [note.start_beat for note in chart.melody])
    offsets = beat_seconds(chart, [note.end_beat for note in chart.melody])
    hertz = np.array(
        [
            FOLD_BASE_HZ * 2 ** ((note.pitch % SEMITONES_PER_OCTAVE) / SEMITONES_PER_OCTAVE)
            for note in chart.melody
        ]
    )
    return np.stack([onsets, offsets], axis=1), hertz


def melody_onset_f1(reference: Chart, estimate: Chart) -> float:
    """Match note onsets within 50 ms and the same pitch class."""
    if not reference.melody or not estimate.melody:
        return 0.0
    reference_intervals, reference_hertz = _notes(reference)
    estimate_intervals, estimate_hertz = _notes(estimate)
    transcription = import_module("mir_eval.transcription")
    _, _, f1, _ = transcription.precision_recall_f1_overlap(
        reference_intervals,
        reference_hertz,
        estimate_intervals,
        estimate_hertz,
        onset_tolerance=ONSET_WINDOW_S,
        offset_ratio=None,
    )
    return float(f1)


def _triads(chart: Chart) -> list[tuple[float, float, str]]:
    starts = beat_seconds(chart, [chord.start_beat for chord in chart.chords])
    ends = beat_seconds(chart, [chord.end_beat for chord in chart.chords])
    return [
        (float(start), float(end), majmin(chord.symbol))
        for start, end, chord in zip(starts, ends, chart.chords, strict=True)
    ]


def majmin_agreement(reference: Chart, estimate: Chart) -> float:
    """Share of reference chord time covered by an extracted chord with the same triad."""
    reference_triads = _triads(reference)
    total = sum(end - start for start, end, _ in reference_triads)
    if total <= 0:
        return 0.0
    estimate_triads = _triads(estimate)
    agreed = sum(
        max(0.0, min(end, other_end) - max(start, other_start))
        for start, end, triad in reference_triads
        for other_start, other_end, other in estimate_triads
        if triad == other
    )
    return agreed / total


_HARTE_QUALITIES = {
    ChordQuality.MAJOR: "maj",
    ChordQuality.MINOR: "min",
    ChordQuality.DOMINANT_SEVENTH: "7",
    ChordQuality.MAJOR_SEVENTH: "maj7",
    ChordQuality.MINOR_SEVENTH: "min7",
    ChordQuality.DIMINISHED: "dim",
    ChordQuality.DIMINISHED_SEVENTH: "dim7",
    ChordQuality.HALF_DIMINISHED_SEVENTH: "hdim7",
    ChordQuality.AUGMENTED: "aug",
    ChordQuality.SUSPENDED_SECOND: "sus2",
    ChordQuality.SUSPENDED_FOURTH: "sus4",
    ChordQuality.SUSPENDED_SEVENTH: "7sus4",
}


def _harte_label(symbol: str) -> str:
    """Write a chart symbol the way mir_eval's chord metrics read it."""
    chord = parse_chord(symbol)
    if isinstance(chord, NoChord):
        return "N"
    return f"{chord.root}:{_HARTE_QUALITIES[chord.quality]}"


def _harte_events(chart: Chart) -> tuple[Seconds, list[str]]:
    starts = beat_seconds(chart, [chord.start_beat for chord in chart.chords])
    ends = beat_seconds(chart, [chord.end_beat for chord in chart.chords])
    return np.stack([starts, ends], axis=1), [_harte_label(chord.symbol) for chord in chart.chords]


def _mir_eval_chords(reference: "Reference", estimate: Chart, metric: str) -> float | None:
    if not reference.chart.chords:
        return None
    if not estimate.chords:
        return 0.0
    if reference.chord_labels:
        reference_intervals = np.array(
            [[start, end] for start, end, _ in reference.chord_labels], dtype=np.float64
        )
        reference_labels = [label for _, _, label in reference.chord_labels]
    else:
        reference_intervals, reference_labels = _harte_events(reference.chart)
    estimate_intervals, estimate_labels = _harte_events(estimate)
    scores = import_module("mir_eval.chord").evaluate(
        reference_intervals, reference_labels, estimate_intervals, estimate_labels
    )
    return float(scores[metric])


def _root(reference: "Reference", estimate: Chart) -> float | None:
    return _mir_eval_chords(reference, estimate, "root")


def _sevenths(reference: "Reference", estimate: Chart) -> float | None:
    return _mir_eval_chords(reference, estimate, "sevenths")


def segment_f_at(reference: Chart, estimate: Chart, window_s: float) -> float:
    """Boundary F1 within a window; no boundaries on either side is full agreement."""
    boundaries = [
        beat_seconds(chart, [section.start_beat for section in chart.sections[1:]])
        for chart in (reference, estimate)
    ]
    if not len(boundaries[0]) and not len(boundaries[1]):
        return 1.0
    if not len(boundaries[0]) or not len(boundaries[1]):
        return 0.0
    util = import_module("mir_eval.util")
    pairs = util.match_events(boundaries[0], boundaries[1], window_s)
    return _f1(len(pairs), len(boundaries[0]), len(boundaries[1]))


def segment_f(reference: Chart, estimate: Chart) -> float:
    """Boundary F1 within three seconds."""
    return segment_f_at(reference, estimate, BOUNDARY_WINDOW_S)


def segment_f_0p5(reference: Chart, estimate: Chart) -> float:
    """Boundary F1 within half a second."""
    return segment_f_at(reference, estimate, BOUNDARY_WINDOW_0P5_S)


def score(reference: Chart, estimate: Chart) -> Scores:
    """Score one extracted chart against its fixture chart."""
    return Scores(
        melody_f1=melody_onset_f1(reference, estimate),
        majmin=majmin_agreement(reference, estimate),
        segment_f=segment_f(reference, estimate),
    )


async def evaluate(fixtures: Path) -> tuple[str, bool]:
    """Extract every fixture WAV and report each score, their means and the acceptance verdict."""
    wavs = sorted(fixtures.glob("*.wav"))
    if not wavs:
        raise ValueError("No fixture WAVs found")
    rows = [
        "| Fixture | Melody onset F1 | Chord majmin | Segment F@3s |",
        "| --- | ---: | ---: | ---: |",
    ]
    results: list[Scores] = []
    for wav in wavs:
        reference = Chart.model_validate_json(wav.with_suffix(".golden.json").read_text())
        estimate = Chart.model_validate(await run_permissive("chart", wav))
        results.append(score(reference, estimate))
        rows.append(
            f"| {wav.name} | {results[-1].melody_f1:.3f} | {results[-1].majmin:.3f} "
            f"| {results[-1].segment_f:.3f} |"
        )
    mean = Scores(
        melody_f1=float(np.mean([result.melody_f1 for result in results])),
        majmin=float(np.mean([result.majmin for result in results])),
        segment_f=float(np.mean([result.segment_f for result in results])),
    )
    rows.append(f"| mean | {mean.melody_f1:.3f} | {mean.majmin:.3f} | {mean.segment_f:.3f} |")
    passed = mean.melody_f1 >= MELODY_F1_FLOOR and mean.majmin >= MAJMIN_FLOOR
    rows.extend(
        [
            "",
            f"Melody onset F1 >= {MELODY_F1_FLOOR:.2f}: {mean.melody_f1 >= MELODY_F1_FLOOR}",
            f"Chord majmin >= {MAJMIN_FLOOR:.2f}: {mean.majmin >= MAJMIN_FLOOR}",
            "",
        ]
    )
    return "\n".join(rows), passed


@dataclass(frozen=True)
class Reference:
    """One excerpt's ground truth, with beats and chart events in seconds."""

    name: str
    audio: Path
    duration_s: float
    chart: Chart
    beats_s: tuple[float, ...]
    downbeats_s: tuple[float, ...]
    key: str | None = None
    # Original Harte labels keep the performed sevenths that `chart` reduces to a triad.
    chord_labels: tuple[tuple[float, float, str], ...] = ()


def seconds_chart(
    name: str,
    duration_s: float,
    *,
    chords: Sequence[tuple[float, float, str]] = (),
    melody: Sequence[tuple[float, float, int]] = (),
    sections: Sequence[tuple[str, float, float]] = (),
) -> Chart:
    """Build a chart whose beats are seconds: 60 bpm with no recorded beat times."""
    notes = [
        {"pitch": pitch, "start_beat": start, "end_beat": min(end, duration_s)}
        for start, end, pitch in melody
        if 0 <= pitch <= MIDI_NOTE_LIMIT and 0 <= start < min(end, duration_s)
    ]
    return Chart.model_validate(
        {
            "schema_version": 1,
            "title": name,
            "source": "dataset annotation",
            "rights": "unknown",
            "extractor": "manual",
            "tempo_bpm": 60,
            "beat_times_s": [],
            "time_signature": [4, 4],
            "key": "C",
            "sections": [
                {"name": label, "start_beat": start, "end_beat": end}
                for label, start, end in sections or [("excerpt", 0.0, duration_s)]
            ],
            "chords": [
                {"symbol": symbol, "start_beat": start, "end_beat": end}
                for start, end, symbol in chords
            ],
            "melody": notes,
            "duration_beats": duration_s,
        }
    )


def _contiguous(
    starts: Sequence[float], symbols: Sequence[str], last_end_s: float
) -> list[tuple[float, float, str]]:
    """Join labelled starts into back-to-back events, merging equal neighbours."""
    ends = [*starts[1:], last_end_s]
    events: list[tuple[float, float, str]] = []
    for start, end, symbol in zip(starts, ends, symbols, strict=True):
        if end <= start:
            continue
        if events and events[-1][2] == symbol and events[-1][1] == start:
            events[-1] = (events[-1][0], end, symbol)
        else:
            events.append((start, end, symbol))
    return events


class _Position(BaseModel):
    model_config = ConfigDict(extra="ignore")
    position: int


class _BeatPoint(BaseModel):
    model_config = ConfigDict(extra="ignore")
    time: float
    value: _Position


class _Span(BaseModel):
    model_config = ConfigDict(extra="ignore")
    time: float
    duration: float | None
    value: str


class _NoteSpan(BaseModel):
    model_config = ConfigDict(extra="ignore")
    time: float
    duration: float
    value: float


class _Annotation(BaseModel):
    model_config = ConfigDict(extra="ignore")
    namespace: str
    annotation_metadata: dict[str, object]
    data: object


class _Jams(BaseModel):
    model_config = ConfigDict(extra="ignore")
    annotations: list[_Annotation]
    file_metadata: dict[str, object]


_CHORD_LABEL = re.compile(r"(?P<root>[A-G][#b]?)(?::(?P<quality>[^/]*))?(?:/.*)?")


def jams_symbol(label: str) -> str:
    """Reduce a JAMS chord label to the major or minor triad symbol the scores compare.

    A label is minor when its shorthand starts with min, dim or hdim, or when it lists a flat
    third among explicit scale degrees; every other quality, including suspensions, counts as
    major, which is how `majmin` reads the same chord in a chart.
    """
    if label in {"N", "X"}:
        return "N.C."
    match = _CHORD_LABEL.fullmatch(label)
    if match is None:
        raise ValueError(f"Unsupported chord label: {label!r}")
    shorthand, _, extension = (match["quality"] or "").partition("(")
    if shorthand:
        minor = shorthand.startswith(("min", "dim", "hdim"))
    else:
        degrees = extension.rstrip(")").split(",")
        minor = "b3" in degrees and "3" not in degrees
    return match["root"] + ("m" if minor else "")


def guitarset_reference(jams: Path, audio: Path) -> Reference:
    """Read a GuitarSet excerpt: performed chords, beats and every string's notes."""
    data = _Jams.model_validate_json(jams.read_text(encoding="utf-8"))
    duration_s = float(str(data.file_metadata["duration"]))

    # The instructed chords carry no source; the performed ones were transcribed from the audio.
    performed = [
        annotation
        for annotation in data.annotations
        if annotation.namespace == "chord" and annotation.annotation_metadata.get("data_source")
    ]
    if len(performed) != 1:
        raise ValueError(f"{jams.name}: expected one performed chord annotation")
    spans = sorted(
        TypeAdapter(list[_Span]).validate_python(performed[0].data), key=lambda span: span.time
    )
    if not spans:
        raise ValueError(f"{jams.name}: the performed chord annotation is empty")
    ends = [
        min(span.time + (span.duration or 0.0), following.time)
        for span, following in zip(spans, [*spans[1:], spans[-1]], strict=True)
    ]
    ends[-1] = min(spans[-1].time + (spans[-1].duration or 0.0), duration_s)
    chords = [
        (span.time, end, jams_symbol(span.value))
        for span, end in zip(spans, ends, strict=True)
        if end > span.time
    ]
    beat_points = sorted(
        (
            point
            for annotation in data.annotations
            if annotation.namespace == "beat_position"
            for point in TypeAdapter(list[_BeatPoint]).validate_python(annotation.data)
        ),
        key=lambda point: point.time,
    )
    melody = sorted(
        (note.time, note.time + note.duration, round(note.value))
        for annotation in data.annotations
        if annotation.namespace == "note_midi"
        for note in TypeAdapter(list[_NoteSpan]).validate_python(annotation.data)
    )
    return Reference(
        name=jams.stem,
        audio=audio,
        duration_s=duration_s,
        chart=seconds_chart(jams.stem, duration_s, chords=chords, melody=melody),
        beats_s=tuple(point.time for point in beat_points),
        downbeats_s=tuple(point.time for point in beat_points if point.value.position == 1),
        chord_labels=tuple(
            (span.time, end, span.value)
            for span, end in zip(spans, ends, strict=True)
            if end > span.time
        ),
    )


def guitarset_references(root: Path) -> dict[str, list[Reference]]:
    """Every fetched GuitarSet excerpt, split into comping and solo takes by file name."""
    jams_files = sorted((root / "annotation").glob("*.jams"))
    if not jams_files:
        raise FileNotFoundError(f"No GuitarSet annotations in {root}; run scripts.fetch_guitarset")
    groups: dict[str, list[Reference]] = {"comp": [], "solo": []}
    for jams in jams_files:
        kind = jams.stem.rsplit("_", 1)[-1]
        if kind in groups:
            audio = root / "audio" / f"{jams.stem}_mic.wav"
            groups[kind].append(guitarset_reference(jams, audio))
    return groups


@dataclass(frozen=True)
class BeatRow:
    """One AAM beat: when it starts, its bar and quarter count, and its chord label."""

    time_s: float
    bar: int
    quarter: int
    chord: str


@dataclass(frozen=True)
class Segment:
    """One AAM segment: its span and key, with instruments and generators listed in step."""

    start_s: float
    end_s: float
    mark: str
    key: str
    instruments: tuple[str, ...]
    generators: tuple[str, ...]


def _arff_rows(text: str) -> list[list[str]]:
    """Data rows of an ARFF file; beat files omit the @DATA line, so skip headers instead."""
    lines = [line for line in text.splitlines() if line.strip() and line[0] not in "@%"]
    return list(csv.reader(lines, quotechar="'"))


def aam_chord_label(name: str) -> str:
    """Name a chord the way mir_eval does: `Amin` is `A:min` and N.C. is `N`."""
    if name == "N.C.":
        return "N"
    match = re.fullmatch(r"([A-G]#?)(maj|min)", name)
    if match is None:
        raise ValueError(f"Unsupported AAM chord name: {name!r}")
    return f"{match[1]}:{match[2]}"


def aam_symbol(label: str) -> str:
    """Turn a mir_eval chord label into the chart symbol: `A:min` is `Am`."""
    if label == "N":
        return "N.C."
    root, _, quality = label.partition(":")
    return root + ("m" if quality == "min" else "")


def aam_key(name: str) -> str:
    """Turn an AAM key such as `Gmin` into a chart key such as `Gm`."""
    match = re.fullmatch(r"([A-G]#?)(maj|min)", name)
    if match is None:
        raise ValueError(f"Unsupported AAM key: {name!r}")
    return match[1] + ("m" if match[2] == "min" else "")


def parse_beatinfo(text: str) -> list[BeatRow]:
    """Parse `_beatinfo.arff`: time, bar count, quarter count and chord name per beat.

    The generator can write a segment's last bar again as the first bar of the next segment,
    so beat times step back or repeat. The later bar wins and the earlier copy is dropped,
    which leaves strictly increasing beats.
    """
    rows: list[BeatRow] = []
    for time, bar, quarter, chord in _arff_rows(text):
        row = BeatRow(float(time), int(bar), int(quarter), aam_chord_label(chord))
        while rows and rows[-1].time_s >= row.time_s - OVERLAP_S:
            rows.pop()
        rows.append(row)
    return rows


def _bracketed(value: str) -> tuple[str, ...]:
    return tuple(part for part in value.strip("[]").split(",") if part)


def parse_segments(text: str) -> tuple[list[Segment], float]:
    """Parse `_segments.arff` into segments and the track's end time, given by the `end` row."""
    rows = _arff_rows(text)
    if not rows or rows[-1][1] != "end":
        raise ValueError("Segment file has no end row")
    starts = [float(row[0]) for row in rows]
    segments = [
        Segment(
            start,
            end,
            row[1],
            row[3],
            _bracketed(row[4]),
            _bracketed(row[5]),
        )
        for row, start, end in zip(rows[:-1], starts[:-1], starts[1:], strict=True)
    ]
    return segments, starts[-1]


def dominant_key(segments: Sequence[Segment]) -> str:
    """The key sounding longest, the earliest when two tie; AAM may change key per segment."""
    totals: dict[str, float] = defaultdict(float)
    for segment in segments:
        totals[segment.key] += segment.end_s - segment.start_s
    return max(totals, key=lambda key: totals[key])


def _varlen(data: bytes, position: int) -> tuple[int, int]:
    value = 0
    while True:
        byte = data[position]
        position += 1
        value = value << 7 | byte & 0x7F
        if not byte & 0x80:
            return value, position


def _read_track(
    chunk: bytes, tempos: list[tuple[int, int]], events: list[tuple[int, int, int, bool]]
) -> None:
    position = tick = status = 0
    while position < len(chunk):
        delta, position = _varlen(chunk, position)
        tick += delta
        first = chunk[position]
        if first == 0xFF:
            kind = chunk[position + 1]
            length, position = _varlen(chunk, position + 2)
            if kind == 0x51 and length == 3:
                tempos.append((tick, int.from_bytes(chunk[position : position + 3])))
            position += length
        elif first in {0xF0, 0xF7}:
            length, position = _varlen(chunk, position + 1)
            position += length
        else:
            if first & 0x80:
                status = first
                position += 1
            command = status & 0xF0
            size = 1 if command in {0xC0, 0xD0} else 2
            body = chunk[position : position + size]
            position += size
            if command in {0x80, 0x90}:
                events.append((tick, status & 0x0F, body[0], command == 0x90 and body[1] > 0))


def read_midi_notes(data: bytes) -> list[tuple[float, float, int]]:
    """Read (onset s, offset s, pitch) notes from a MIDI file, following its tempo changes.

    A small reader instead of mido: roughly one AAM file in ten carries a key signature mido
    refuses to decode, and nothing here needs more than notes and tempo.
    """
    if data[:4] != b"MThd":
        raise ValueError("Not a MIDI file")
    _, track_count, division = struct.unpack(">HHH", data[8:14])
    if division & 0x8000:
        raise ValueError("SMPTE time division is not supported")
    tempos: list[tuple[int, int]] = []
    events: list[tuple[int, int, int, bool]] = []
    position = 8 + struct.unpack(">I", data[4:8])[0]
    try:
        for _ in range(track_count):
            if data[position : position + 4] != b"MTrk":
                raise ValueError("Missing MIDI track chunk")
            (length,) = struct.unpack(">I", data[position + 4 : position + 8])
            _read_track(data[position + 8 : position + 8 + length], tempos, events)
            position += 8 + length
    except (IndexError, struct.error) as error:
        raise ValueError("Truncated MIDI file") from error
    marks = sorted(dict(sorted(tempos, key=lambda mark: mark[0])).items())
    if not marks or marks[0][0] != 0:
        marks.insert(0, (0, DEFAULT_TEMPO_US))
    ticks = [tick for tick, _ in marks]
    elapsed = [0.0]
    for (tick, tempo), (following, _) in pairwise(marks):
        elapsed.append(
            elapsed[-1] + (following - tick) * tempo / division / MICROSECONDS_PER_SECOND
        )

    def seconds(tick: int) -> float:
        index = bisect.bisect_right(ticks, tick) - 1
        return elapsed[index] + (tick - ticks[index]) * marks[index][1] / (
            division * MICROSECONDS_PER_SECOND
        )

    pending: dict[tuple[int, int], deque[int]] = defaultdict(deque)
    notes: list[tuple[float, float, int]] = []
    for tick, channel, pitch, sounding in sorted(events, key=lambda event: (event[0], event[3])):
        if sounding:
            pending[(channel, pitch)].append(tick)
        elif pending[(channel, pitch)]:
            start = pending[(channel, pitch)].popleft()
            if tick > start:
                notes.append((seconds(start), seconds(tick), pitch))
    return sorted(notes)


def aam_reference(track: str, root: Path) -> Reference:
    """Read an AAM track: beats and chords, downbeats, segments, key and melody notes."""
    segments, duration_s = parse_segments(
        (root / "annotations" / f"{track}_segments.arff").read_text()
    )
    # Beats the generator wrote past the track's end have no audio to be scored against.
    rows = [
        row
        for row in parse_beatinfo((root / "annotations" / f"{track}_beatinfo.arff").read_text())
        if row.time_s < duration_s
    ]
    parsed: dict[str, list[tuple[float, float, int]]] = {}
    melody: list[tuple[float, float, int]] = []
    for segment in segments:
        for instrument, generator in zip(segment.instruments, segment.generators, strict=True):
            if not generator.startswith("Melody"):
                continue
            if instrument not in parsed:
                path = root / "midi" / f"{track}_{instrument}.mid"
                parsed[instrument] = read_midi_notes(path.read_bytes())
            melody.extend(
                note for note in parsed[instrument] if segment.start_s <= note[0] < segment.end_s
            )
    chords = _contiguous(
        [row.time_s for row in rows], [aam_symbol(row.chord) for row in rows], duration_s
    )
    return Reference(
        name=track,
        audio=root / "mixes" / f"{track}_mix.flac",
        duration_s=duration_s,
        chart=seconds_chart(
            track,
            duration_s,
            chords=chords,
            melody=sorted(melody),
            sections=[(s.mark, s.start_s, s.end_s) for s in segments],
        ),
        beats_s=tuple(row.time_s for row in rows),
        downbeats_s=tuple(row.time_s for row in rows if row.quarter == 1),
        key=aam_key(dominant_key(segments)),
    )


def aam_references(root: Path) -> list[Reference]:
    """Every fetched AAM track that has its mix, annotations and MIDI."""
    mixes = sorted((root / "mixes").glob("*_mix.flac"))
    if not mixes:
        raise FileNotFoundError(f"No AAM mixes in {root}; run scripts.fetch_aam")
    return [aam_reference(mix.name.split("_", 1)[0], root) for mix in mixes]


def bootstrap_mean(values: Sequence[float]) -> tuple[float, float, float] | None:
    """Return the mean and its 95 percent percentile interval over resampled tracks."""
    if not values:
        return None
    sample = np.array(values, dtype=np.float64)
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    draws = rng.choice(sample, size=(BOOTSTRAP_RESAMPLES, sample.size)).mean(axis=1)
    tail = (1 - CONFIDENCE) / 2 * 100
    low, high = np.percentile(draws, [tail, 100 - tail])
    return float(sample.mean()), float(low), float(high)


def _beat_f(reference: Reference, estimate: Chart) -> float | None:
    if not reference.beats_s or not estimate.beat_times_s:
        return None
    beat = import_module("mir_eval.beat")
    return float(beat.f_measure(np.array(reference.beats_s), np.array(estimate.beat_times_s)))


def _downbeat_f(reference: Reference, estimate: Chart) -> float | None:
    if not reference.downbeats_s or not estimate.beat_times_s:
        return None
    beat = import_module("mir_eval.beat")
    per_bar = max(1, estimate.time_signature[0])
    return float(
        beat.f_measure(np.array(reference.downbeats_s), np.array(estimate.beat_times_s[::per_bar]))
    )


def mirex_key(key: str) -> str:
    """Spell a chart key such as `Gm` as mir_eval's `G minor`."""
    match = re.fullmatch(r"([A-G][#b]?)(m?)", key)
    if match is None:
        raise ValueError(f"Unsupported key: {key!r}")
    return f"{match[1]} {'minor' if match[2] else 'major'}"


def _key_score(reference: Reference, estimate: Chart) -> float | None:
    if reference.key is None:
        return None
    try:
        key = import_module("mir_eval.key")
        return float(key.weighted_score(mirex_key(reference.key), mirex_key(estimate.key)))
    except ValueError:
        return None


def _melody(reference: Reference, estimate: Chart) -> float | None:
    return melody_onset_f1(reference.chart, estimate) if reference.chart.melody else None


def _chords(reference: Reference, estimate: Chart) -> float | None:
    return majmin_agreement(reference.chart, estimate) if reference.chart.chords else None


SCORERS: dict[str, Callable[[Reference, Chart], float | None]] = {
    "root": _root,
    "majmin": _chords,
    "sevenths": _sevenths,
    "beat_f": _beat_f,
    "downbeat_f": _downbeat_f,
    "segment_f_0p5": lambda reference, estimate: segment_f_0p5(reference.chart, estimate),
    "segment_f": lambda reference, estimate: segment_f(reference.chart, estimate),
    "key": _key_score,
    "melody_f1": _melody,
}
GROUPS: dict[str, tuple[str, tuple[str, ...]]] = {
    "guitarset-comp": (
        "GuitarSet comping excerpts",
        ("root", "majmin", "sevenths", "beat_f"),
    ),
    "guitarset-solo": ("GuitarSet solo excerpts", ("melody_f1",)),
    "aam": (
        "AAM tracks",
        (
            "root",
            "majmin",
            "beat_f",
            "downbeat_f",
            "segment_f_0p5",
            "segment_f",
            "key",
            "melody_f1",
        ),
    ),
}
DETECTOR_GROUP = "aam-detector"
DETECTOR_NAME = "chord_detector"
DETECTOR_METRICS = ("majmin_accuracy", "change_hit_rate", "change_abs_error_s")
SUBSETS = (("all", 0.0), ("30 s and longer", LONG_EXCERPT_S))
EXTRACTION_ERRORS = (OSError, RuntimeError, ValueError)


@dataclass(frozen=True)
class TrackResult:
    """One extractor's scores on one excerpt; a failed extraction has no values."""

    group: str
    name: str
    duration_s: float
    extractor: str
    values: dict[str, float | None]
    failed: bool = False


def prepare_audio(audio: Path, work_dir: Path) -> Path:
    """Give extractors a WAV: AAM mixes are FLAC, so decode them once into the work folder."""
    if audio.suffix.lower() == ".wav":
        return audio
    wav = work_dir / "wav" / f"{audio.stem}.wav"
    if not wav.is_file():
        wav.parent.mkdir(parents=True, exist_ok=True)
        samples, rate = sf.read(str(audio), dtype="float32", always_2d=True)
        sf.write(str(wav), samples, rate, subtype="FLOAT")
    return wav


async def score_group(
    group: str,
    references: Sequence[Reference],
    extractors: Mapping[str, Extractor],
    work_dir: Path,
    progress: Callable[[str], None] | None = None,
) -> list[TrackResult]:
    """Extract every excerpt with every extractor and score the metrics the group reports."""
    metrics = GROUPS[group][1]
    results: list[TrackResult] = []
    for extractor, extract in extractors.items():
        for index, reference in enumerate(references, start=1):
            if progress:
                progress(f"{group} {extractor} {index}/{len(references)} {reference.name}")
            try:
                estimate = await extract(prepare_audio(reference.audio, work_dir))
            except EXTRACTION_ERRORS:
                results.append(
                    TrackResult(group, reference.name, reference.duration_s, extractor, {}, True)
                )
                continue
            values = {metric: SCORERS[metric](reference, estimate) for metric in metrics}
            results.append(
                TrackResult(group, reference.name, reference.duration_s, extractor, values)
            )
    return results


def read_audio_22k(path: Path) -> NDArray[np.float32]:
    """Read an audio file as mono at the detector's analysis rate."""
    samples, rate = sf.read(str(path), dtype="float32", always_2d=True)
    mono = np.asarray(samples.mean(axis=1), dtype=np.float32)
    if rate != chord_detector.ANALYSIS_RATE:
        mono = np.asarray(
            librosa.resample(mono, orig_sr=rate, target_sr=chord_detector.ANALYSIS_RATE),
            dtype=np.float32,
        )
    return mono


@dataclass(frozen=True)
class DetectorScore:
    """The chord detector on one track: per-beat triad agreement and change timing."""

    name: str
    duration_s: float
    windows: int
    correct: int
    changes: int
    abs_errors_s: tuple[float, ...]

    @property
    def accuracy(self) -> float | None:
        return self.correct / self.windows if self.windows else None

    @property
    def hit_rate(self) -> float | None:
        return len(self.abs_errors_s) / self.changes if self.changes else None

    @property
    def mean_abs_error_s(self) -> float | None:
        return float(np.mean(self.abs_errors_s)) if self.abs_errors_s else None


def score_detector(
    reference: Reference, times: Seconds, chroma: NDArray[np.float64]
) -> DetectorScore:
    """Compare the detector with the beat-by-beat chord truth of one track.

    Each beat is a half-open window ending at the next beat, the last at the track's end. A
    change is a beat whose triad differs from the previous beat's, neither being N.C., and is
    found when the detector's own change search lands within its window around that beat.
    """
    if not reference.chart.chords:
        raise ValueError(f"{reference.name}: no chord truth to score the detector against")
    starts = [event.start_beat for event in reference.chart.chords]
    triads = [majmin(event.symbol) for event in reference.chart.chords]

    def triad_at(time_s: float) -> str:
        return triads[max(bisect.bisect_right(starts, time_s) - 1, 0)]

    beats = reference.beats_s
    truth = [triad_at(beat) for beat in beats]
    windows = correct = changes = 0
    errors: list[float] = []
    for index, (beat, triad) in enumerate(zip(beats, truth, strict=True)):
        end = beats[index + 1] if index + 1 < len(beats) else reference.duration_s
        guess = chord_detector.beat_window(times, chroma, beat, end)
        if guess is not None:
            windows += 1
            correct += guess.symbol == triad
        before = truth[index - 1] if index else triad
        if chord_detector.NO_CHORD in (before, triad) or before == triad:
            continue
        changes += 1
        found = chord_detector.change_time(times, chroma, before, triad, beat)
        if found is not None:
            errors.append(abs(found - beat))
    return DetectorScore(
        reference.name, reference.duration_s, windows, correct, changes, tuple(errors)
    )


def detector_scores(
    references: Sequence[Reference], progress: Callable[[str], None] | None = None
) -> list[DetectorScore]:
    """Run the chord detector on every reference's audio."""
    scores: list[DetectorScore] = []
    for index, reference in enumerate(references, start=1):
        if progress:
            progress(f"{DETECTOR_GROUP} {index}/{len(references)} {reference.name}")
        times, chroma = chord_detector.frames(read_audio_22k(reference.audio))
        scores.append(score_detector(reference, times, chroma))
    return scores


@dataclass(frozen=True)
class Summary:
    """One metric over one subset of tracks: the mean and its bootstrap interval."""

    group: str
    subset: str
    extractor: str
    metric: str
    tracks: int
    failed: int
    interval: tuple[float, float, float] | None


DETECTOR_GETTERS: dict[str, Callable[[DetectorScore], float | None]] = {
    "majmin_accuracy": lambda score: score.accuracy,
    "change_hit_rate": lambda score: score.hit_rate,
    "change_abs_error_s": lambda score: score.mean_abs_error_s,
}


def _extractor_summaries(
    results: Sequence[TrackResult], group: str, subset: str, minimum_s: float
) -> list[Summary]:
    rows: list[Summary] = []
    for extractor in dict.fromkeys(r.extractor for r in results if r.group == group):
        chosen = [
            r
            for r in results
            if (r.group, r.extractor) == (group, extractor) and r.duration_s >= minimum_s
        ]
        failed = sum(r.failed for r in chosen)
        for metric in GROUPS[group][1]:
            values = [
                value
                for r in chosen
                if not r.failed and (value := r.values.get(metric)) is not None
            ]
            interval = bootstrap_mean(values)
            rows.append(Summary(group, subset, extractor, metric, len(values), failed, interval))
    return rows


def summarise(results: Sequence[TrackResult], detector: Sequence[DetectorScore]) -> list[Summary]:
    """Summarise every group, subset, extractor and metric, then the detector's rows."""
    rows: list[Summary] = []
    for subset, minimum_s in SUBSETS:
        for group in GROUPS:
            rows.extend(_extractor_summaries(results, group, subset, minimum_s))
        chosen = [score for score in detector if score.duration_s >= minimum_s]
        for metric, getter in DETECTOR_GETTERS.items():
            values = [value for score in chosen if (value := getter(score)) is not None]
            interval = bootstrap_mean(values)
            rows.append(
                Summary(DETECTOR_GROUP, subset, DETECTOR_NAME, metric, len(values), 0, interval)
            )
    return rows


LIMITS = (
    "GuitarSet is solo acoustic guitar played by six players. Its comping excerpts are scored "
    "for chord root, major or minor quality and sevenths against the performed-chord annotation, "
    "and for beats; its solo excerpts are scored for melody against the string-by-string "
    "`note_midi` notes. GuitarSet has no section annotations, so no segment metric is reported "
    "for it.",
    "AAM is artificial: its audio is rendered from instrument samples by algorithmic "
    "composition, so scores on it do not predict accuracy on recorded music. The 200 tracks are "
    "drawn with a fixed seed from the first 1000 and listed in `docs/benchmarks/aam-subset.json`. "
    "Its chords are major, minor or none only, its sections are generator segments and may "
    "repeat, its downbeats are the beat rows whose quarter count is 1, its key can change "
    "between segments and is taken as the key sounding longest, and some tracks have no melody "
    "and are left out of the melody row.",
    "The chord detector row runs the triad detector used by every sync, identity and heard "
    "measurement on AAM mixes, against the same per-beat chord truth. Accuracy is the share of "
    "beat windows whose detected triad equals the truth; the change rows use only changes "
    "between two different triads, and the error is measured only where the detector finds one.",
    "Beat F-measure is not trimmed at the start of the track. A row counts only the tracks for "
    "which the metric exists: a failed extraction is counted under Failed and left out, and an "
    "extractor that returns no beat times has no beat score.",
)
METRIC_NOTES = (
    ("root", "mir_eval chord root accuracy"),
    ("majmin", "share of reference chord time covered by the same major or minor triad"),
    ("sevenths", "mir_eval chord sevenths accuracy"),
    ("beat_f", "beat F-measure within 70 ms"),
    ("downbeat_f", "downbeat F-measure within 70 ms"),
    ("segment_f_0p5", "section boundary F1 within 0.5 s"),
    ("segment_f", "section boundary F1 within 3 s"),
    ("key", "mir_eval weighted key score"),
    ("melody_f1", "melody onset F1 within 50 ms, octave ignored"),
    ("majmin_accuracy", "share of beat windows with the right triad"),
    ("change_hit_rate", "share of triad changes the detector finds"),
    ("change_abs_error_s", "mean absolute error of found changes, in seconds"),
)
SUMMARY_HEADER = (
    "group",
    "subset",
    "extractor",
    "metric",
    "tracks",
    "failed",
    "mean",
    "low",
    "high",
)


def _cells(summary: Summary) -> list[str]:
    interval = summary.interval
    numbers = [f"{value:.3f}" for value in interval] if interval else ["", "", ""]
    return [
        summary.group,
        summary.subset,
        summary.extractor,
        summary.metric,
        str(summary.tracks),
        str(summary.failed),
        *numbers,
    ]


def _csv_text(header: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(header)
    writer.writerows(rows)
    return buffer.getvalue()


def render_report(summaries: Sequence[Summary]) -> str:
    """Write the Markdown report: limits, metric definitions and one table per group."""
    lines = [
        "# Extraction on GuitarSet and AAM",
        "",
        f"Each excerpt is one track. Intervals are {CONFIDENCE:.0%} percentile bootstrap "
        f"intervals over tracks ({BOOTSTRAP_RESAMPLES} resamples, seed {BOOTSTRAP_SEED}). "
        "Every row is also given for the tracks 30 s and longer.",
        "",
        "## Dataset limits",
        "",
        *[f"- {limit}" for limit in LIMITS],
        "",
        "## Metrics",
        "",
        *[f"- `{metric}`: {text}" for metric, text in METRIC_NOTES],
    ]
    titles = {group: title for group, (title, _) in GROUPS.items()}
    titles[DETECTOR_GROUP] = "Chord detector on AAM"
    for group, title in titles.items():
        rows = [summary for summary in summaries if summary.group == group]
        if not rows:
            continue
        lines.extend(
            [
                "",
                f"## {title}",
                "",
                "| Subset | Extractor | Metric | Tracks | Failed | Mean | Low | High |",
                "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: |",
            ]
        )
        lines.extend("| " + " | ".join(_cells(row)[1:]) + " |" for row in rows)
    return "\n".join(lines) + "\n"


def write_outputs(
    output_dir: Path,
    results: Sequence[TrackResult],
    detector: Sequence[DetectorScore],
    summaries: Sequence[Summary],
) -> list[Path]:
    """Write the report and its CSV files, returning every path written."""
    output_dir.mkdir(parents=True, exist_ok=True)

    def number(value: float | None) -> str:
        return "" if value is None else repr(value)

    track_rows = [
        [
            result.group,
            result.name,
            f"{result.duration_s:.3f}",
            result.extractor,
            metric,
            number(result.values.get(metric)),
            str(result.failed).lower(),
        ]
        for result in results
        for metric in GROUPS[result.group][1]
    ]
    detector_rows = [
        [
            score.name,
            f"{score.duration_s:.3f}",
            str(score.windows),
            str(score.correct),
            str(score.changes),
            str(len(score.abs_errors_s)),
            number(score.accuracy),
            number(score.hit_rate),
            number(score.mean_abs_error_s),
        ]
        for score in detector
    ]
    files = {
        "extraction.md": render_report(summaries),
        "extraction.csv": _csv_text(SUMMARY_HEADER, [_cells(row) for row in summaries]),
        "extraction_tracks.csv": _csv_text(
            ("group", "track", "duration_s", "extractor", "metric", "value", "failed"), track_rows
        ),
        "detector_aam.csv": _csv_text(
            (
                "track",
                "duration_s",
                "windows",
                "correct",
                "changes",
                "found",
                "accuracy",
                "hit_rate",
                "mean_abs_error_s",
            ),
            detector_rows,
        ),
    }
    paths: list[Path] = []
    for name, text in files.items():
        paths.append(output_dir / name)
        paths[-1].write_text(text, encoding="utf-8")
    return paths


def permissive_extractor() -> Extractor:
    async def extract(wav: Path) -> Chart:
        return Chart.model_validate(await run_permissive("chart", wav))

    return extract


def sheetsage2_extractor(cache: Path, work_dir: Path) -> Extractor:
    async def extract(wav: Path) -> Chart:
        return await run_sheetsage2(
            wav, output_dir=work_dir / "sheetsage2" / wav.stem, cache_dir=cache
        )

    return extract


async def evaluate_datasets(
    guitarset: Path,
    aam: Path,
    extractors: Mapping[str, Extractor],
    work_dir: Path,
    progress: Callable[[str], None] | None = None,
) -> tuple[list[TrackResult], list[DetectorScore]]:
    """Score every extractor on GuitarSet and AAM, and the chord detector on AAM."""
    excerpts = guitarset_references(guitarset)
    tracks = aam_references(aam)
    results: list[TrackResult] = []
    for group, references in (
        ("guitarset-comp", excerpts["comp"]),
        ("guitarset-solo", excerpts["solo"]),
        ("aam", tracks),
    ):
        results.extend(await score_group(group, references, extractors, work_dir, progress))
    return results, await asyncio.to_thread(detector_scores, tracks, progress)


def _log(message: str) -> None:
    sys.stderr.write(message + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixtures", type=Path, default=Path("fixtures"))
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="score GuitarSet and AAM instead of the fixtures and write the report here",
    )
    parser.add_argument("--guitarset", type=Path, default=CACHE_ROOT / "guitarset")
    parser.add_argument("--aam", type=Path, default=CACHE_ROOT / "aam")
    parser.add_argument("--work-dir", type=Path, default=CACHE_ROOT / "evaluation")
    parser.add_argument(
        "--extractor",
        action="append",
        choices=("permissive", "sheetsage2"),
        help="extractor to score, repeatable; both by default",
    )
    args = parser.parse_args()
    if args.output_dir is None:
        report, passed = asyncio.run(evaluate(args.fixtures))
        print(report)
        if not passed:
            parser.exit(1, "Extraction is below the melody or chord acceptance floor\n")
        return
    chosen: list[str] = args.extractor or ["permissive", "sheetsage2"]
    extractors: dict[str, Extractor] = {}
    if "permissive" in chosen:
        extractors["permissive"] = permissive_extractor()
    if "sheetsage2" in chosen:
        cache = Path(os.environ.get("GHOSTBAND_SHEETSAGE2_CACHE", str(ROOT / "models/sheetsage2")))
        if not cache.is_dir():
            parser.error(
                f"{cache} is missing; run ghostband setup --sheetsage2 or pass --extractor"
            )
        extractors["sheetsage2"] = sheetsage2_extractor(cache, args.work_dir)
    results, detector = asyncio.run(
        evaluate_datasets(
            args.guitarset,
            args.aam,
            extractors,
            args.work_dir,
            _log,
        )
    )
    for path in write_outputs(args.output_dir, results, detector, summarise(results, detector)):
        print(path)


if __name__ == "__main__":
    main()
