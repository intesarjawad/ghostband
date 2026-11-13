"""Convert the pinned decoded event schema and melody MIDI into chart beats."""

from bisect import bisect_right
from collections import defaultdict
from importlib import import_module
from itertools import pairwise
from math import ceil
from pathlib import Path
from statistics import median
from typing import Annotated, Protocol

from pydantic import BaseModel, ConfigDict, Field

from ghostband.chart import Chart, ChordEvent, NoteEvent, Section
from ghostband.chords import Chord

FiniteTime = Annotated[float, Field(ge=0, allow_inf_nan=False)]
Label = Annotated[str, Field(min_length=1)]


class Rhythm(BaseModel):
    meter: tuple[int, int] | None = None
    eighth_position: int | None = None


class Values(BaseModel):
    rhythm: Rhythm | None = None
    chord: Label | None = None
    key: Label | None = None
    structure: Label | None = None


class Event(BaseModel):
    time: FiniteTime
    values: Values


class DecodedEvents(BaseModel):
    events: list[Event]


class Timing(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    device: str
    sheetsage2_revision: str
    mert_revision: str
    audio_duration_s: Annotated[float, Field(gt=0)]
    load_s: FiniteTime
    prepare_s: FiniteTime
    inference_s: FiniteTime
    total_s: FiniteTime
    realtime_factor: FiniteTime
    exceeds_3x: bool


class _Message(Protocol):
    type: str
    time: float
    channel: int
    note: int
    velocity: int


def _melody_notes(path: Path, duration_s: float) -> list[tuple[float, float, int]]:
    mido = import_module("mido")
    messages: list[_Message] = list(mido.MidiFile(str(path)))
    active: dict[tuple[int, int], list[float]] = defaultdict(list)
    notes: list[tuple[float, float, int]] = []
    time_s = 0.0
    for message in messages:
        time_s += message.time
        if message.type == "note_on" and message.velocity > 0:
            active[message.channel, message.note].append(time_s)
        elif message.type in {"note_off", "note_on"}:
            starts = active[message.channel, message.note]
            if not starts:
                raise ValueError(f"Unmatched MIDI note release in {path}")
            start = starts.pop(0)
            end = min(time_s, duration_s)
            if start < end:
                notes.append((start, end, message.note))
    if any(active.values()):
        raise ValueError(f"Unterminated MIDI notes in {path}")
    return sorted(notes)


def _measure_eighths(meter: tuple[int, int]) -> int:
    numerator, denominator = meter
    if numerator <= 0 or denominator <= 0 or denominator & (denominator - 1):
        raise ValueError("Invalid decoded meter")
    eighths = numerator * 8 / denominator
    if not eighths.is_integer():
        raise ValueError("Invalid decoded meter")
    return int(eighths)


def _positions(events: list[Event]) -> list[tuple[float, int]]:
    return [
        (event.time, event.values.rhythm.eighth_position)
        for event in events
        if event.values.rhythm is not None and event.values.rhythm.eighth_position is not None
    ]


def _meter_and_pickup(events: list[Event]) -> tuple[tuple[int, int], float, float]:
    """Return the meter, the time of its first measure and the pickup length in eighths."""
    runs: list[tuple[float, tuple[int, int]]] = []
    for event in events:
        rhythm = event.values.rhythm
        if rhythm is None or rhythm.meter is None:
            continue
        _measure_eighths(rhythm.meter)
        if not runs or runs[-1][1] != rhythm.meter:
            runs.append((event.time, rhythm.meter))
    if not runs:
        raise ValueError("At least two decoded beats and a meter are required")
    if any(time < runs[0][0] for time, _ in _positions(events)):
        raise ValueError("Rhythm position precedes its meter")
    if len(runs) == 1:
        return runs[0][1], runs[0][0], 0
    (_, opening), (change_s, meter) = runs[-2], runs[-1]
    opening_positions = [position for time, position in _positions(events) if time < change_s]
    one_short_measure = (
        len(runs) == 2
        and _measure_eighths(opening) < _measure_eighths(meter)
        and all(b > a for a, b in pairwise(opening_positions))
        and all(0 <= position < _measure_eighths(opening) for position in opening_positions)
    )
    if not one_short_measure:
        raise ValueError(
            f"The meter changes from {opening[0]}/{opening[1]} to {meter[0]}/{meter[1]} "
            f"at {change_s:.2f} s; one chart holds one meter and an optional pickup"
        )
    return meter, change_s, _measure_eighths(opening)


def _beat_grid(
    events: list[Event], duration_s: float
) -> tuple[list[float], tuple[int, int], float]:
    """Return the time of every whole chart beat, the meter and the pickup length in beats.

    The model writes an anacrusis as a short opening measure with its own meter. Chart beat 0
    is the first decoded position, so a pickup puts the first downbeat on a fractional beat,
    as a hand-made chart with a pickup does.
    """
    meter, start_s, pickup_eighths = _meter_and_pickup(events)
    numerator, denominator = meter
    compound = denominator == 8 and numerator % 3 == 0
    pulses: list[float] = []
    for time, position in _positions(events):
        if time < start_s:
            continue
        denominator_position = position * denominator / 8
        if not denominator_position.is_integer() or not 0 <= denominator_position < numerator:
            raise ValueError("Rhythm position is outside the decoded meter grid")
        if denominator_position % (3 if compound else 1) == 0 and time < duration_s:
            pulses.append(time)
    if len(pulses) < 2:
        raise ValueError("At least two decoded beats and a meter are required")
    if any(b <= a for a, b in pairwise(pulses)):
        raise ValueError("Decoded beat times must be strictly increasing")
    if not pickup_eighths:
        return pulses, meter, 0.0
    pickup_beats = pickup_eighths / (3 if compound else 8 / denominator)
    # Anchors pair a time with its chart beat: the pickup start, then every decoded pulse.
    anchors = [
        (_positions(events)[0][0], 0.0),
        *((time, pickup_beats + index) for index, time in enumerate(pulses)),
    ]
    beats: list[float] = []
    for (t0, b0), (t1, b1) in pairwise(anchors):
        beats.extend(
            t0 + (whole - b0) * (t1 - t0) / (b1 - b0) for whole in range(ceil(b0), ceil(b1))
        )
    if anchors[-1][1].is_integer():
        beats.append(anchors[-1][0])
    return beats, meter, pickup_beats


def _beat_at(time_s: float, beats: list[float]) -> float:
    index = min(max(bisect_right(beats, time_s) - 1, 0), len(beats) - 2)
    return max(0.0, index + (time_s - beats[index]) / (beats[index + 1] - beats[index]))


def _symbol(label: str) -> str:
    if label in {"N", "X"}:
        return "N.C."
    root, separator, quality = label.partition(":")
    if not separator:
        return label
    quality, slash, bass = quality.partition("/")
    if slash and not bass:
        raise ValueError(f"Missing chord bass: {label}")
    suffix = {"maj": "", "min": "m", "min7": "m7", "hdim7": "m7b5"}.get(quality, quality)
    if slash and bass[0] not in "ABCDEFG":
        degree = {
            "1": 0,
            "b2": 1,
            "2": 2,
            "b3": 3,
            "3": 4,
            "4": 5,
            "b5": 6,
            "5": 7,
            "#5": 8,
            "b6": 8,
            "6": 9,
            "bb7": 9,
            "b7": 10,
            "7": 11,
        }
        if bass not in degree:
            raise ValueError(f"Unsupported chord bass degree: {label}")
        bass = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")[
            (Chord(root).root_pitch_class + degree[bass]) % 12
        ]
    return root + suffix + ("/" + bass if slash else "")


def chart_from_output(output_dir: Path, *, title: str, source: str) -> Chart:
    """Read model artefacts without granting provenance rights to imported audio."""
    decoded = DecodedEvents.model_validate_json((output_dir / "events.json").read_text())
    timing = Timing.model_validate_json((output_dir / "timing.json").read_text())
    events = decoded.events
    if any(b.time < a.time for a, b in pairwise(events)):
        raise ValueError("Decoded events must be ordered by time")
    duration_s = timing.audio_duration_s
    beats, meter, pickup_beats = _beat_grid(events, duration_s)
    duration_beats = _beat_at(duration_s, beats)
    period_s = median(b - a for a, b in pairwise(beats))
    chords: dict[float, str] = {}
    structures: dict[float, str] = {0.0: "Song"}
    key: str | None = None
    for event in events:
        if event.time >= duration_s:
            continue
        # Chords and sections snap to the meter's pulses, which a pickup offsets from whole beats.
        pulse = pickup_beats + round(_beat_at(event.time, beats) - pickup_beats)
        beat = min(max(pulse, 0.0), duration_beats)
        if event.values.chord is not None and beat < duration_beats:
            chords[beat] = _symbol(event.values.chord)
        if event.values.structure is not None and beat < duration_beats:
            structures[beat] = event.values.structure
        if key is None and event.values.key is not None:
            tonic, separator, mode = event.values.key.partition(":")
            if not separator or mode not in {"maj", "min", "major", "minor"}:
                raise ValueError("Decoded key must specify a major or minor mode")
            key = tonic + ("m" if mode in {"min", "minor"} else "")
    if key is None:
        raise ValueError("No key was decoded")
    chord_rows = sorted(chords.items())
    section_rows = sorted(structures.items())
    melodies = [
        _melody_notes(output_dir / f"melody_{voice}.mid", duration_s)
        for voice in ("vocal", "instrumental")
    ]
    selected = max(melodies, key=len)
    return Chart(
        schema_version=1,
        title=title,
        source=source,
        rights="unknown",
        extractor="sheetsage2",
        tempo_bpm=60 / period_s,
        beat_times_s=beats,
        time_signature=meter,
        key=key,
        sections=[
            Section(name=name, start_beat=start, end_beat=end)
            for (start, name), (end, _) in pairwise([*section_rows, (duration_beats, "")])
        ],
        chords=[
            ChordEvent(symbol=symbol, start_beat=start, end_beat=end)
            for (start, symbol), (end, _) in pairwise([*chord_rows, (duration_beats, "")])
        ],
        melody=[
            NoteEvent(start_beat=_beat_at(start, beats), end_beat=_beat_at(end, beats), pitch=pitch)
            for start, end, pitch in selected
            if _beat_at(end, beats) > _beat_at(start, beats)
        ],
        duration_beats=duration_beats,
    )
