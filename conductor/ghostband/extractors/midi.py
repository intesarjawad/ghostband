"""Read MIDI timing and separate harmony and melody parts into a portable chart.

MIDI stores integer ticks and microseconds, so beat positions use tick resolution
and BPM is rounded to three decimals. Source, rights, key and section labels are
not recoverable when absent: imports use unknown provenance and one section.
Chord attacks retain repeated symbols and sub-beat pickups; sampling only whole
beats would discard that information. An explicit Melody track name takes
precedence over register because a melody can lie below the harmony. Otherwise
melody is the highest mean-pitch part with at least 16 notes, falling back to
the highest part for shorter files.
"""

import argparse
from collections import defaultdict, deque
from dataclasses import dataclass
from importlib import import_module
from itertools import pairwise
from math import ceil
from pathlib import Path
from typing import Protocol

from ghostband.chart import Chart, ChordEvent, NoteEvent, Section
from ghostband.chords import Chord, ChordQuality

ROOTS = ("C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B")
DEFAULT_TEMPO = 500_000
MIN_MELODY_NOTES = 16
PERCUSSION_CHANNEL = 9
MICROSECONDS_PER_MINUTE = 60_000_000


class _Message(Protocol):
    type: str
    time: int
    channel: int
    note: int
    velocity: int
    tempo: int
    numerator: int
    denominator: int
    name: str
    key: str


class _MidiFile(Protocol):
    type: int
    ticks_per_beat: int
    tracks: list[list[_Message]]


@dataclass(frozen=True)
class _Note:
    start: int
    end: int
    pitch: int


def _read_notes(track: list[_Message]) -> dict[int, list[_Note]]:
    active: dict[tuple[int, int], deque[int]] = defaultdict(deque)
    parts: dict[int, list[_Note]] = defaultdict(list)
    tick = 0
    for message in track:
        tick += message.time
        if message.type not in {"note_on", "note_off"}:
            continue
        if message.channel == PERCUSSION_CHANNEL:
            continue
        identity = (message.channel, message.note)
        if message.type == "note_on" and message.velocity > 0:
            active[identity].append(tick)
        else:
            if not active[identity]:
                raise ValueError("Unmatched MIDI note release")
            start = active[identity].popleft()
            if tick <= start:
                raise ValueError("MIDI note duration must be positive")
            parts[message.channel].append(_Note(start, tick, message.note))
    if any(active.values()):
        raise ValueError("Unterminated MIDI notes")
    return parts


def _chord_symbol(pitches: list[int]) -> str:
    if not pitches:
        return "N.C."
    bass = min(pitches) % 12
    sounding = {pitch % 12 for pitch in pitches}
    # Prefer complete templates, then fewer unexplained tones, then a root bass.
    templates = [Chord(root, quality) for root in ROOTS for quality in ChordQuality]
    chord = max(
        templates,
        key=lambda chord: (
            len(sounding & set(chord.pitch_classes)) / len(sounding | set(chord.pitch_classes)),
            chord.root_pitch_class == bass,
        ),
    )
    return str(
        chord if bass == chord.root_pitch_class else Chord(chord.root, chord.quality, ROOTS[bass])
    )


def _chords(notes: list[_Note], end: int, ticks_per_beat: float) -> list[ChordEvent]:
    attacks = {note.start for note in notes}
    boundaries = sorted({0, end, *attacks, *(note.end for note in notes)})
    events: list[ChordEvent] = []
    for start, stop in pairwise(boundaries):
        symbol = _chord_symbol([note.pitch for note in notes if note.start <= start < note.end])
        if events and events[-1].symbol == symbol and start not in attacks:
            events[-1] = events[-1].model_copy(update={"end_beat": stop / ticks_per_beat})
        else:
            events.append(
                ChordEvent(
                    start_beat=start / ticks_per_beat, end_beat=stop / ticks_per_beat, symbol=symbol
                )
            )
    return events


def _beat_times(
    tempos: dict[int, int], end: int, ticks_per_beat: float, ticks_per_quarter: int
) -> list[float]:
    duration = end / ticks_per_beat
    # Integrate every tempo event, including changes between beat boundaries.
    changes = sorted(tempos.items())
    beat_times: list[float] = []
    seconds = 0.0
    previous_tick = 0.0
    tempo = DEFAULT_TEMPO
    index = 0
    for beat in range(ceil(duration) + 1):
        tick = min(beat * ticks_per_beat, end)
        while index < len(changes) and changes[index][0] <= tick:
            change_tick, next_tempo = changes[index]
            seconds += (change_tick - previous_tick) * tempo / ticks_per_quarter / 1_000_000
            previous_tick, tempo = change_tick, next_tempo
            index += 1
        seconds += (tick - previous_tick) * tempo / ticks_per_quarter / 1_000_000
        previous_tick = tick
        beat_times.append(seconds)
    return beat_times


def _metadata(
    metadata: list[tuple[int, _Message]], fallback_title: str
) -> tuple[tuple[int, int], str, str, dict[int, int]]:
    metadata.sort(key=lambda event: event[0])
    meter = (4, 4)
    title = next(
        (
            message.name
            for tick, message in metadata
            if tick == 0 and message.type == "track_name" and message.name
        ),
        fallback_title,
    )
    key = "unknown"
    tempos = {0: DEFAULT_TEMPO}
    for tick, message in metadata:
        if message.type == "time_signature":
            signature = (message.numerator, message.denominator)
            if signature[0] <= 0 or signature[1] <= 0:
                raise ValueError("MIDI meter must be positive")
            if tick and signature != meter:
                raise ValueError("Changing meters cannot be represented in one chart")
            meter = signature
        elif message.type == "set_tempo":
            if message.tempo <= 0:
                raise ValueError("MIDI tempo must be positive")
            tempos[tick] = message.tempo
        elif message.type == "key_signature" and tick == 0:
            key = message.key
    return meter, title, key, tempos


def extract_midi(path: Path) -> Chart:
    """Read synchronous MIDI without accessing adjacent charts or recordings."""
    mido = import_module("mido")
    midi: _MidiFile = mido.MidiFile(str(path), charset="utf8")
    if midi.type == 2 or midi.ticks_per_beat <= 0:
        raise ValueError("Use synchronous MIDI with positive ticks per quarter note")
    metadata: list[tuple[int, _Message]] = []
    parts: list[list[_Note]] = []
    named_melody: list[list[_Note]] = []
    end = 0
    for track in midi.tracks:
        tick = 0
        for message in track:
            tick += message.time
            metadata.append((tick, message))
        end = max(end, tick)
        track_parts = list(_read_notes(track).values())
        parts.extend(track_parts)
        if any(
            message.type == "track_name" and message.name.casefold() == "melody"
            for message in track
        ):
            named_melody.extend(track_parts)
    if not parts or end <= 0:
        raise ValueError("MIDI must contain pitched notes with positive duration")
    meter, title, key, tempos = _metadata(metadata, path.stem)
    numerator, denominator = meter
    quarters_per_beat = 4 / denominator * (3 if denominator == 8 and numerator % 3 == 0 else 1)
    ticks_per_beat = midi.ticks_per_beat * quarters_per_beat
    eligible = [part for part in parts if len(part) >= MIN_MELODY_NOTES]
    melody = max(
        named_melody or eligible or parts,
        key=lambda part: sum(note.pitch for note in part) / len(part),
    )
    harmony = [note for part in parts if part is not melody for note in part]
    duration = end / ticks_per_beat
    return Chart(
        schema_version=1,
        title=title,
        source=path.name,
        rights="unknown",
        extractor="midi",
        tempo_bpm=round(MICROSECONDS_PER_MINUTE / tempos[0] / quarters_per_beat, 3),
        beat_times_s=_beat_times(tempos, end, ticks_per_beat, midi.ticks_per_beat),
        time_signature=meter,
        key=key,
        sections=[Section(name="A", start_beat=0, end_beat=duration)],
        chords=_chords(harmony, end, ticks_per_beat),
        melody=[
            NoteEvent(
                start_beat=note.start / ticks_per_beat,
                end_beat=note.end / ticks_per_beat,
                pitch=note.pitch,
            )
            for note in sorted(melody, key=lambda note: (note.start, note.end, note.pitch))
        ],
        duration_beats=duration,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("midi", type=Path)
    args = parser.parse_args()
    print(extract_midi(args.midi).model_dump_json())


if __name__ == "__main__":
    main()
