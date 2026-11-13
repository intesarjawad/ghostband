"""Deterministic chart exports with separate harmony and melody channels."""

import re
from collections import Counter
from datetime import datetime
from fractions import Fraction
from importlib import import_module, metadata
from io import BytesIO
from itertools import pairwise
from math import ceil
from pathlib import Path
from typing import Annotated, Protocol

from pydantic import BaseModel, ConfigDict, Field

from ghostband.chart import Chart
from ghostband.chords import parse_chord, voice_chord

Text = Annotated[str, Field(min_length=1)]


class _MidiFile(Protocol):
    tracks: list[list[object]]

    def save(self, *, file: BytesIO) -> None: ...


# Mido has no type declarations; keep its untyped objects at the encoding boundary.
mido = import_module("mido")

TICKS_PER_QUARTER = 960
MICROSECONDS_PER_MINUTE = 60_000_000
SHAREABLE_RIGHTS = frozenset({"public-domain", "own-work"})
ABC_NOTE_NAMES = ("C", "^C", "D", "^D", "E", "F", "^F", "G", "^G", "A", "^A", "B")
_UNSAFE_FILENAME = re.compile(r"[^a-z0-9]+")


def package_version() -> str:
    """Name the installed build so a take records the version that made it."""
    try:
        return metadata.version("ghostband")
    except metadata.PackageNotFoundError:
        return "unknown"


class TakeMetadata(BaseModel):
    """The model and generation settings saved beside a recorded take."""

    model_config = ConfigDict(extra="forbid")

    model: Text
    preset: Text
    seed_rotation: list[int]
    version: Text = Field(default_factory=package_version)


def _note_track(
    name: str, channel: int, intervals: list[tuple[int, int, int]], end_tick: int
) -> list[object]:
    track: list[object] = mido.MidiTrack()
    track.append(mido.MetaMessage("track_name", name=name))
    track.append(mido.Message("program_change", channel=channel, program=0))
    events = sorted(
        event for start, end, pitch in intervals for event in ((start, 1, pitch), (end, -1, pitch))
    )
    sounding: Counter[int] = Counter()
    previous_tick = 0
    for tick, change, pitch in events:
        sounding[pitch] += change
        if (change == 1 and sounding[pitch] == 1) or (change == -1 and sounding[pitch] == 0):
            track.append(
                mido.Message(
                    "note_on" if change == 1 else "note_off",
                    channel=channel,
                    note=pitch,
                    velocity=80 if change == 1 else 0,
                    time=tick - previous_tick,
                )
            )
            previous_tick = tick
    track.append(mido.MetaMessage("end_of_track", time=end_tick - previous_tick))
    return track


def chart_to_midi(chart: Chart) -> bytes:
    """Encode chart beats at the notated tempo, with voice-led piano harmony."""
    return _chart_to_midi(chart, with_chords=True, with_melody=True)


def chart_to_chords_midi(chart: Chart) -> bytes:
    """Encode only the harmony so the part can be read on its own."""
    return _chart_to_midi(chart, with_chords=True, with_melody=False)


def chart_to_melody_midi(chart: Chart) -> bytes:
    """Encode only the melody so the part can be read on its own."""
    return _chart_to_midi(chart, with_chords=False, with_melody=True)


def _chart_to_midi(chart: Chart, *, with_chords: bool, with_melody: bool) -> bytes:
    numerator, denominator = chart.time_signature
    denominator_notes_per_beat = 3 if denominator == 8 and numerator % 3 == 0 else 1
    quarters_per_beat = 4 * denominator_notes_per_beat / denominator
    ticks_per_beat = TICKS_PER_QUARTER * quarters_per_beat
    tempo = round(MICROSECONDS_PER_MINUTE / chart.tempo_bpm / quarters_per_beat)
    if not 1 <= tempo <= 0xFFFFFF:
        raise ValueError("Tempo is outside the MIDI range; choose a representable tempo")
    end_tick = round(chart.duration_beats * ticks_per_beat)
    midi: _MidiFile = mido.MidiFile(type=1, ticks_per_beat=TICKS_PER_QUARTER, charset="utf8")
    metadata_track: list[object] = mido.MidiTrack()
    metadata_track.extend(
        [
            mido.MetaMessage("track_name", name=chart.title),
            mido.MetaMessage("set_tempo", tempo=tempo),
            mido.MetaMessage(
                "time_signature",
                numerator=numerator,
                denominator=denominator,
                clocks_per_click=round(24 * quarters_per_beat),
            ),
            mido.MetaMessage("end_of_track", time=end_tick),
        ]
    )
    midi.tracks.append(metadata_track)
    harmony: list[tuple[int, int, int]] = []
    previous: tuple[int, ...] = ()
    for event in chart.chords:
        pitches = voice_chord(parse_chord(event.symbol), previous)
        start, end = (
            round(event.start_beat * ticks_per_beat),
            round(event.end_beat * ticks_per_beat),
        )
        if start == end:
            raise ValueError("Chord duration is below MIDI resolution; lengthen the chord")
        harmony.extend((start, end, pitch) for pitch in pitches)
        previous = pitches
    melody: list[tuple[int, int, int]] = []
    for note in chart.melody:
        start, end = round(note.start_beat * ticks_per_beat), round(note.end_beat * ticks_per_beat)
        if start == end:
            raise ValueError("Note duration is below MIDI resolution; lengthen the note")
        melody.append((start, end, note.pitch))
    parts: list[list[object]] = []
    if with_chords:
        parts.append(_note_track("Harmony", 0, harmony, end_tick))
    if with_melody:
        parts.append(_note_track("Melody", 1, melody, end_tick))
    midi.tracks.extend(parts)
    output = BytesIO()
    midi.save(file=output)
    return output.getvalue()


def share_safe(chart: Chart) -> Chart:
    """Remove melody that the chart's rights do not allow redistributing."""
    if chart.rights in SHAREABLE_RIGHTS:
        return chart
    return chart.model_copy(update={"melody": []})


def _abc_pitch(pitch: int) -> str:
    """Spell a MIDI pitch in ABC octaves around middle C, accidentals written out."""
    name = ABC_NOTE_NAMES[pitch % 12]
    octave = pitch // 12 - 1
    if octave >= 5:
        return name.lower() + "'" * (octave - 5)
    if octave == 4:
        return name
    return name + "," * (4 - octave)


def _abc_length(eighths: float) -> str:
    value = Fraction(eighths).limit_denominator(64)
    if value.denominator == 1:
        return "" if value.numerator == 1 else str(value.numerator)
    return f"{value.numerator}/{value.denominator}"


def chart_to_abc(chart: Chart) -> str:
    """Encode the chart as ABC, marking sections and chord changes in place."""
    numerator, denominator = chart.time_signature
    compound = denominator == 8 and numerator % 3 == 0
    eighths_per_beat = 8 * (3 if compound else 1) / denominator
    beats_per_bar = chart.beats_per_bar
    lines = [
        "X:1",
        f"T:{chart.title}",
        f"M:{numerator}/{denominator}",
        "L:1/8",
        f"Q:1/4={chart.tempo_bpm:g}",
        f"K:{chart.key}",
    ]
    for index in range(ceil(chart.duration_beats / beats_per_bar)):
        start = index * beats_per_bar
        end = min(start + beats_per_bar, chart.duration_beats)
        lines.extend(
            f"% Section: {section.name}"
            for section in chart.sections
            if start <= section.start_beat < end
        )
        cuts = {start, end}
        cuts.update(chord.start_beat for chord in chart.chords if start <= chord.start_beat < end)
        for note in chart.melody:
            if start <= note.start_beat < end:
                cuts.add(note.start_beat)
            if start < note.end_beat < end:
                cuts.add(note.end_beat)
        ordered = sorted(cuts)
        tokens: list[str] = []
        for left, right in pairwise(ordered):
            symbol = next(
                (chord.symbol for chord in chart.chords if chord.start_beat == left), None
            )
            note = next(
                (item for item in chart.melody if item.start_beat <= left < item.end_beat), None
            )
            length = _abc_length((right - left) * eighths_per_beat)
            if note is None:
                token = "z" + length
            else:
                token = _abc_pitch(note.pitch) + length
                if right < note.end_beat:
                    token += "-"
            if symbol is not None:
                token = f'"{symbol}"' + token
            tokens.append(token)
        lines.append(" ".join(tokens) + " |")
    return "\n".join(lines) + "\n"


def _file_stem(title: str) -> str:
    """Keep a title usable as one file name in an export folder."""
    stem = _UNSAFE_FILENAME.sub("-", title.casefold()).strip("-")
    return stem or "chart"


def export_bundle(chart: Chart, directory: Path, *, share: bool = False) -> list[Path]:
    """Write a chart's portable JSON, split MIDI parts and ABC into one folder."""
    exported = share_safe(chart) if share else chart
    stem = _file_stem(exported.title)
    directory.mkdir(parents=True, exist_ok=True)
    text_files = {
        directory / f"{stem}.chart.json": exported.model_dump_json(indent=2) + "\n",
        directory / f"{stem}.abc": chart_to_abc(exported),
    }
    binary_files = {
        directory / "chords.mid": chart_to_chords_midi(exported),
        directory / "melody.mid": chart_to_melody_midi(exported),
    }
    for path, text in text_files.items():
        path.write_text(text, encoding="utf-8")
    for path, data in binary_files.items():
        path.write_bytes(data)
    return sorted([*text_files, *binary_files])


def take_path(directory: Path, song: str, preset: str, started: datetime) -> Path:
    """Name a take by song, preset and start time inside the export folder."""
    stamp = started.strftime("%Y%m%d-%H%M%S")
    return directory / f"{_file_stem(song)}-{_file_stem(preset)}-{stamp}.wav"


def write_take_sidecar(take: Path, settings: TakeMetadata) -> Path:
    """Write a take's model and settings as JSON beside its WAV."""
    take.parent.mkdir(parents=True, exist_ok=True)
    sidecar = take.with_name(take.name + ".json")
    sidecar.write_text(settings.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return sidecar
