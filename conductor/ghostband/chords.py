"""Chord symbols and deterministic MIDI voicings, independent of style."""

import re
from dataclasses import dataclass
from enum import StrEnum
from itertools import combinations, product
from types import MappingProxyType

SEMITONES_PER_OCTAVE = 12
BASS_MIDI_MIN = 36
BASS_MIDI_MAX = 47
UPPER_MIDI_MIN = 55
UPPER_MIDI_MAX = 70

_NOTE_PATTERN = r"[A-G][#b]?"
_SYMBOL_PATTERN = re.compile(
    rf"(?P<root>{_NOTE_PATTERN})(?P<quality>[^/]*)(?:/(?P<bass>{_NOTE_PATTERN}))?"
)
_NATURAL_PITCH_CLASSES = MappingProxyType({"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11})


class ChordQuality(StrEnum):
    """Supported triads and sevenths, using canonical symbol suffixes."""

    MAJOR = ""
    MINOR = "m"
    DOMINANT_SEVENTH = "7"
    MAJOR_SEVENTH = "maj7"
    MINOR_SEVENTH = "m7"
    DIMINISHED = "dim"
    DIMINISHED_SEVENTH = "dim7"
    HALF_DIMINISHED_SEVENTH = "m7b5"
    AUGMENTED = "aug"
    SUSPENDED_SECOND = "sus2"
    SUSPENDED_FOURTH = "sus4"
    SUSPENDED_SEVENTH = "7sus4"


_INTERVALS = MappingProxyType(
    {
        ChordQuality.MAJOR: (0, 4, 7),
        ChordQuality.MINOR: (0, 3, 7),
        ChordQuality.DOMINANT_SEVENTH: (0, 4, 7, 10),
        ChordQuality.MAJOR_SEVENTH: (0, 4, 7, 11),
        ChordQuality.MINOR_SEVENTH: (0, 3, 7, 10),
        ChordQuality.DIMINISHED: (0, 3, 6),
        ChordQuality.DIMINISHED_SEVENTH: (0, 3, 6, 9),
        ChordQuality.HALF_DIMINISHED_SEVENTH: (0, 3, 6, 10),
        ChordQuality.AUGMENTED: (0, 4, 8),
        ChordQuality.SUSPENDED_SECOND: (0, 2, 7),
        ChordQuality.SUSPENDED_FOURTH: (0, 5, 7),
        ChordQuality.SUSPENDED_SEVENTH: (0, 5, 7, 10),
    }
)
_QUALITY_ALIASES = MappingProxyType({"maj": "", "min": "m", "min7": "m7", "sus": "sus4"})


def _pitch_class(note: str) -> int:
    if re.fullmatch(_NOTE_PATTERN, note) is None:
        raise ValueError(f"Invalid note name: {note!r}; use A..G with an optional # or b")
    accidental = 1 if note.endswith("#") else -1 if note.endswith("b") else 0
    return (_NATURAL_PITCH_CLASSES[note[0]] + accidental) % SEMITONES_PER_OCTAVE


@dataclass(frozen=True)
class Chord:
    """A spelled root and quality with an optional independent slash bass."""

    root: str
    quality: ChordQuality = ChordQuality.MAJOR
    bass: str | None = None

    def __post_init__(self) -> None:
        _pitch_class(self.root)
        if self.bass is not None:
            _pitch_class(self.bass)

    @property
    def root_pitch_class(self) -> int:
        """Return the root modulo an octave, with C as zero."""
        return _pitch_class(self.root)

    @property
    def bass_pitch_class(self) -> int:
        """Use the slash bass when present, otherwise the root."""
        return _pitch_class(self.bass) if self.bass is not None else self.root_pitch_class

    @property
    def pitch_classes(self) -> tuple[int, ...]:
        """Return each chord tone once in root-relative order."""
        return tuple(
            (self.root_pitch_class + interval) % SEMITONES_PER_OCTAVE
            for interval in _INTERVALS[self.quality]
        )

    def __str__(self) -> str:
        bass = f"/{self.bass}" if self.bass is not None else ""
        return f"{self.root}{self.quality}{bass}"


@dataclass(frozen=True)
class NoChord:
    """Explicit silence in the chord part, leaving melody independent."""

    def __str__(self) -> str:
        return "N.C."


def parse_chord(symbol: str) -> Chord | NoChord:
    """Parse a triad, seventh, slash chord or N.C.; reject unsupported harmony."""
    if symbol == "N.C.":
        return NoChord()
    match = _SYMBOL_PATTERN.fullmatch(symbol)
    if match is not None:
        suffix = match["quality"]
        try:
            quality = ChordQuality(_QUALITY_ALIASES.get(suffix, suffix))
        except ValueError:
            quality = None
        if quality is not None:
            return Chord(match["root"], quality, match["bass"])
    raise ValueError(
        f"Unsupported chord symbol: {symbol!r}; use a triad, seventh, slash chord or N.C."
    )


def _upper_voicings(chord: Chord) -> tuple[tuple[int, ...], ...]:
    choices = (
        tuple(
            pitch
            for pitch in range(UPPER_MIDI_MIN, UPPER_MIDI_MAX + 1)
            if pitch % SEMITONES_PER_OCTAVE == pitch_class
        )
        for pitch_class in chord.pitch_classes
    )
    return tuple(sorted(tuple(sorted(pitches)) for pitches in product(*choices)))


def _movement_cost(previous: tuple[int, ...], candidate: tuple[int, ...]) -> int:
    if len(previous) == len(candidate):
        return sum(abs(before - after) for before, after in zip(previous, candidate, strict=True))
    smaller, larger = sorted((previous, candidate), key=len)
    # In one pitch dimension, crossing matched voices cannot reduce their total movement.
    return min(
        sum(abs(before - after) for before, after in zip(smaller, matched, strict=True))
        + sum(min(abs(note - other) for other in smaller) for note in larger if note not in matched)
        for matched in combinations(larger, len(smaller))
    )


def voice_chord(chord: Chord | NoChord, previous: tuple[int, ...] = ()) -> tuple[int, ...]:
    """Return bass followed by sorted upper tones, minimizing movement from previous.

    Each upper chord tone occurs once; the bass is a separate register. With no
    previous harmony, prefer the lowest bottom note, then the lowest top note.
    Otherwise match upper tones at minimum cost, charging unmatched tones their
    distance to the nearest tone in the other voicing. Ties prefer the lowest
    top note, then the lowest bottom note. Bass movement is fixed by the symbol.
    """
    if isinstance(chord, NoChord):
        return ()
    bass = BASS_MIDI_MIN + chord.bass_pitch_class
    candidates = _upper_voicings(chord)
    if not previous:
        upper = min(candidates, key=lambda candidate: (candidate[0], candidate[-1]))
        return (bass, *upper)
    upper = min(
        candidates,
        key=lambda candidate: (
            _movement_cost(previous[1:], candidate),
            candidate[-1],
            candidate[0],
        ),
    )
    return (bass, *upper)
