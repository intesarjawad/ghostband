"""Portable charts use musician-counted beats for every musical interval."""

import logging
from itertools import pairwise
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ghostband.api import ChartGeometry

Beat = Annotated[float, Field(ge=0, allow_inf_nan=False)]
PositiveNumber = Annotated[float, Field(gt=0, allow_inf_nan=False)]
PositiveInteger = Annotated[int, Field(gt=0, strict=True)]
Text = Annotated[str, Field(min_length=1)]

IMPORT_PITCH_MIN = 36
IMPORT_PITCH_MAX = 96


class _Interval(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    start_beat: Beat
    end_beat: Beat

    @model_validator(mode="after")
    def validate_interval(self) -> Self:
        if self.end_beat <= self.start_beat:
            raise ValueError("end_beat must be greater than start_beat")
        return self


class Section(_Interval):
    """A named span of the chart, ending at an exclusive beat boundary."""

    name: Text


class ChordEvent(_Interval):
    """A chord symbol held over a half-open beat interval, including N.C."""

    symbol: Text


class NoteEvent(_Interval):
    """A MIDI pitch sounding over a half-open beat interval."""

    pitch: Annotated[int, Field(ge=0, le=127, strict=True)]


class Chart(BaseModel):
    """Versioned musical events with provenance and optional recording beat times."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    schema_version: Literal[1]
    title: Text
    source: Text
    rights: Literal["public-domain", "own-work", "unknown"]
    extractor: Literal["sheetsage2", "permissive", "midi", "manual"]
    tempo_bpm: PositiveNumber
    beat_times_s: list[Beat]
    time_signature: tuple[PositiveInteger, PositiveInteger]
    key: Text
    sections: Annotated[list[Section], Field(min_length=1)]
    chords: list[ChordEvent]
    melody: list[NoteEvent]
    duration_beats: PositiveNumber

    @property
    def beats_per_bar(self) -> int:
        """Count dotted quarters in compound eighth-note meters, denominator notes otherwise."""
        numerator, denominator = self.time_signature
        if denominator == 8 and numerator % 3 == 0:
            return numerator // 3
        return numerator

    @property
    def beat_phase(self) -> float:
        """Chart position of every counted beat, modulo one.

        A first section shorter than a bar is a pickup. The music counts from the bar line
        that ends it, so after a half-beat pickup the counted beats fall at n + 0.5.
        """
        return self.bar_origin % 1

    @property
    def bar_origin(self) -> float:
        pickup = self.sections[0].end_beat
        return pickup if pickup < self.beats_per_bar else 0.0

    def geometry(self) -> ChartGeometry:
        """The shared bar and pulse grouping for the session API and local controls."""
        numerator, denominator = self.time_signature
        return ChartGeometry(
            beats_per_bar=self.beats_per_bar,
            pulses_per_beat=3 if denominator == 8 and numerator % 3 == 0 else 1,
            beat_phase=self.beat_phase,
            bar_origin=self.bar_origin,
        )

    def symbol_at(self, beat: float) -> str | None:
        """Chord held at a chart position, including fractional pickup boundaries."""
        return next(
            (chord.symbol for chord in self.chords if chord.start_beat <= beat < chord.end_beat),
            None,
        )

    @field_validator("time_signature")
    @classmethod
    def validate_time_signature(cls, signature: tuple[int, int]) -> tuple[int, int]:
        denominator = signature[1]
        if denominator & (denominator - 1):
            raise ValueError("time_signature denominator must be a power of two")
        return signature

    @field_validator("beat_times_s")
    @classmethod
    def validate_beat_times(cls, times: list[float]) -> list[float]:
        if any(current <= previous for previous, current in pairwise(times)):
            raise ValueError("beat_times_s must be strictly increasing")
        return times

    @model_validator(mode="after")
    def validate_timeline(self) -> Self:
        if self.sections[0].start_beat != 0:
            raise ValueError("sections must start at beat 0")
        for previous, current in pairwise(self.sections):
            if current.start_beat != previous.end_beat:
                raise ValueError("sections must be contiguous and ordered")
        if self.sections[-1].end_beat != self.duration_beats:
            raise ValueError("sections must end at duration_beats")
        for previous, current in pairwise(self.chords):
            if current.start_beat < previous.start_beat:
                raise ValueError("chords must be sorted by start_beat")
            if current.start_beat < previous.end_beat:
                raise ValueError("chords must not overlap")
        if any(chord.end_beat > self.duration_beats for chord in self.chords):
            raise ValueError("chords must end within duration_beats")
        if any(note.end_beat > self.duration_beats for note in self.melody):
            raise ValueError("melody must end within duration_beats")
        return self

    @classmethod
    def from_import(cls, data: dict[str, object]) -> Self:
        """Validate imported data, reset rights and clamp melody pitches with one warning."""
        chart = cls.model_validate(data)
        melody = [
            note.model_copy(
                update={"pitch": max(IMPORT_PITCH_MIN, min(IMPORT_PITCH_MAX, note.pitch))}
            )
            for note in chart.melody
        ]
        changed = sum(
            original.pitch != imported.pitch
            for original, imported in zip(chart.melody, melody, strict=True)
        )
        if changed:
            logging.getLogger(__name__).warning(
                "Clamped %d melody pitches to MIDI %d..%d on import",
                changed,
                IMPORT_PITCH_MIN,
                IMPORT_PITCH_MAX,
            )
        return chart.model_copy(update={"rights": "unknown", "melody": melody})
