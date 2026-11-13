"""Schedule counted chart beats before their audible deadlines.

Recording beat timestamps belong to reference playback. Band timing uses chart
BPM and a tempo factor. A start reserves one lookahead interval for pre-roll.
Tempo edits take effect at the next whole transport beat beyond the scheduling
horizon: commands already sent to the engine cannot be recalled. Seeking or
changing a loop clears notes and starts a fresh pre-roll at the selected beat.
"""

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from math import ceil, floor, isfinite

from ghostband.chart import Chart
from ghostband.chords import parse_chord, voice_chord
from ghostband.engine_client import Command

# The pulsed bass releases this far into each beat, so the next beat strikes it again.
PULSE_HOLD_BEATS = 0.6
# Chart positions closer than this are the same instant; pickups of a third of a beat are inexact.
BEAT_EPSILON = 1e-9


@dataclass(frozen=True)
class TimedEvent:
    """A transmitted pitch command with its chart position and audible deadline."""

    beat: float
    audible_time: float
    emit_time: float
    command: Command


@dataclass(frozen=True)
class _Change:
    beat: float
    pitches: tuple[int, ...]
    on: bool


@dataclass(frozen=True)
class _Tempo:
    beat: float
    time_s: float
    factor: float


def _counted_beats(phase: float, first: float, last: float) -> list[float]:
    """Counted beats from `first` to `last` inclusive, on a grid offset by `phase`."""
    lowest = ceil(first - phase - BEAT_EPSILON)
    highest = floor(last - phase + BEAT_EPSILON)
    return [phase + beat for beat in range(lowest, highest + 1)]


def _strikes(
    phase: float, first: float, last: float, pitch: int
) -> list[tuple[float, float, tuple[int, ...]]]:
    """One short note at `first` and at every counted beat after it, inside the chord."""
    later = _counted_beats(phase, first, last)
    onsets = [first, *(beat for beat in later if first + BEAT_EPSILON < beat < last - BEAT_EPSILON)]
    ends = [*onsets[1:], last]
    return [
        (onset, min(onset + PULSE_HOLD_BEATS, end), (pitch,))
        for onset, end in zip(onsets, ends, strict=True)
    ]


def _changes(chart: Chart, start: float, end: float, *, pulse: bool) -> list[_Change]:
    intervals: list[tuple[float, float, tuple[int, ...]]] = []
    previous: tuple[int, ...] = ()
    for chord in chart.chords:
        previous = voice_chord(parse_chord(chord.symbol), previous)
        if pulse and previous:
            intervals.extend(
                _strikes(chart.beat_phase, chord.start_beat, chord.end_beat, previous[0])
            )
            intervals.append((chord.start_beat, chord.end_beat, previous[1:]))
        else:
            intervals.append((chord.start_beat, chord.end_beat, previous))
    intervals.extend((note.start_beat, note.end_beat, (note.pitch,)) for note in chart.melody)
    changes: list[_Change] = []
    for first, last, pitches in intervals:
        if first < end and last > start:
            changes.append(_Change(max(first, start) - start, pitches, True))
            changes.append(_Change(min(last, end) - start, pitches, False))
    # Release before attacking at the same instant, preserving repeated notes.
    return sorted(changes, key=lambda change: (change.beat, change.on, change.pitches))


class Scheduler:
    """Pure clock-driven transport emitting engine commands through an injected sink."""

    def __init__(
        self,
        chart: Chart,
        send: Callable[[Command], None],
        *,
        lookahead_s: float,
        pulse: bool = False,
    ) -> None:
        """`pulse` strikes the bass on every counted beat, the tempo cue for onset mode 1."""
        if not isfinite(lookahead_s) or lookahead_s < 0:
            raise ValueError("Lookahead must be finite and nonnegative")
        self.chart = chart
        self.send = send
        self.lookahead_s = lookahead_s
        self.pulse = pulse
        self.running = False
        self.events: list[TimedEvent] = []
        self._active: Counter[int] = Counter()
        self._loop: tuple[float, float] | None = None
        self._factor = 1.0
        self._tempos: list[_Tempo] = []
        self._changes: list[_Change] = []
        self._cursor = 0
        self._cycle_beat = 0.0
        self._start = 0.0
        self._end = chart.duration_beats
        self._last_now_s = float("-inf")

    def _clock(self, now_s: float) -> None:
        if not isfinite(now_s) or now_s < self._last_now_s:
            raise ValueError("Scheduler clock must be finite and monotonic")
        self._last_now_s = now_s

    def play(self, now_s: float, start_beat: float | None = None) -> None:
        """Start with enough pre-roll to emit the first notes at their deadline."""
        self._clock(now_s)
        start = start_beat if start_beat is not None else self._loop[0] if self._loop else 0.0
        end = self._loop[1] if self._loop else self.chart.duration_beats
        lower = self._loop[0] if self._loop else 0.0
        if not isfinite(start) or not lower <= start < end:
            raise ValueError("Playback start must be inside the selected chart interval")
        if self.running:
            self.stop(now_s)
        self._start, self._end = start, end
        self._cycle_beat = start
        self._changes = _changes(self.chart, start, end, pulse=self.pulse)
        self._cursor = 0
        self._tempos = [_Tempo(start, now_s + self.lookahead_s, self._factor)]
        self.running = True
        self.send({"cmd": "play"})

    def stop(self, now_s: float) -> None:
        """Release every held pitch, including notes emitted ahead of the playhead."""
        self._clock(now_s)
        self._flush(self.audible_beat(now_s), now_s, now_s)
        self.send({"cmd": "stop"})
        self.running = False

    def seek(self, beat: float, now_s: float) -> None:
        """Restart at a beat, restoring intervals already sounding at that beat."""
        self.play(now_s, beat)

    def set_loop(self, loop: tuple[float, float] | None, now_s: float) -> None:
        """Select a half-open loop; an active transport restarts at its beginning."""
        self._clock(now_s)
        if loop is not None:
            start, end = loop
            if (
                not isfinite(start)
                or not isfinite(end)
                or not 0 <= start < end <= self.chart.duration_beats
            ):
                raise ValueError("Loop must be a positive interval inside the chart")
        self._loop = loop
        if self.running:
            self.play(now_s)

    def set_tempo(self, factor: float, now_s: float) -> float:
        """Return the continuous beat where the new positive tempo factor begins."""
        self._clock(now_s)
        if not isfinite(factor) or factor <= 0:
            raise ValueError("Tempo factor must be finite and positive")
        self._factor = factor
        if not self.running:
            return 0.0
        beat = float(floor(self._beat_at(now_s + self.lookahead_s) + 1e-12) + 1)
        time_s = self._time_at(beat)
        self._tempos = [tempo for tempo in self._tempos if tempo.beat < beat]
        self._tempos.append(_Tempo(beat, time_s, factor))
        return beat

    def _time_at(self, beat: float) -> float:
        tempo = next(tempo for tempo in reversed(self._tempos) if tempo.beat <= beat)
        return tempo.time_s + (beat - tempo.beat) * 60 / self.chart.tempo_bpm / tempo.factor

    def _beat_at(self, time_s: float) -> float:
        tempo = next(
            (tempo for tempo in reversed(self._tempos) if tempo.time_s <= time_s), self._tempos[0]
        )
        return tempo.beat + (time_s - tempo.time_s) * self.chart.tempo_bpm * tempo.factor / 60

    def audible_beat(self, now_s: float) -> float:
        """Map audible time to chart position, independently of the emitted cursor."""
        if not self._tempos:
            return 0.0
        beat = max(self._tempos[0].beat, self._beat_at(now_s))
        if self._loop and beat >= self._end:
            start, end = self._loop
            return start + (beat - self._end) % (end - start)
        return min(beat, self.chart.duration_beats)

    def beat_time(self, beat: float) -> float:
        """Wall time of a chart beat in the cycle currently being scheduled.

        Unlike a raw tempo lookup this includes the start pre-roll, so the first
        click is queued for when the engine will actually sound it.
        """
        if not self._tempos:
            raise ValueError("Playback has not started")
        return self._time_at(self._cycle_beat + (beat - self._start))

    def beat_log(self) -> list[tuple[float, float]]:
        """Every counted beat in the scheduled interval with its audible time.

        Takes are scored between the beats a listener heard, not from the engine's
        sample counter, so the map comes from the same tempo clock the scheduler used.
        After a pickup the counted beats sit off the integer chart positions.
        """
        if not self._tempos:
            return []
        beats = _counted_beats(self.chart.beat_phase, self._start, self._end)
        return [(beat, self.beat_time(beat)) for beat in beats]

    def _emit(self, pitch: int, on: bool, beat: float, audible_s: float, now_s: float) -> None:
        command: Command = {"cmd": "noteon" if on else "noteoff", "pitch": pitch}
        self.send(command)
        self.events.append(TimedEvent(beat, audible_s, now_s, command))

    def _flush(self, beat: float, audible_s: float, now_s: float) -> None:
        for pitch in sorted(self._active):
            self._emit(pitch, False, beat, audible_s, now_s)
        self._active.clear()

    def step(self, now_s: float) -> list[TimedEvent]:
        """Emit all due changes once; a late clock tick catches up in musical order."""
        self._clock(now_s)
        first_event = len(self.events)
        horizon_s = now_s + self.lookahead_s
        while self.running:
            if self._cursor == len(self._changes):
                boundary = self._cycle_beat + self._end - self._start
                audible_s = self._time_at(boundary)
                if audible_s > horizon_s + 1e-12:
                    break
                self._flush(self._end, audible_s, now_s)
                if self._loop is None:
                    self.running = False
                    break
                self._cycle_beat = boundary
                self._start, self._end = self._loop
                self._changes = _changes(self.chart, self._start, self._end, pulse=self.pulse)
                self._cursor = 0
                continue
            change = self._changes[self._cursor]
            audible_s = self._time_at(self._cycle_beat + change.beat)
            if audible_s > horizon_s + 1e-12:
                break
            beat = self._start + change.beat
            for pitch in change.pitches:
                if change.on:
                    if not self._active[pitch]:
                        self._emit(pitch, True, beat, audible_s, now_s)
                    self._active[pitch] += 1
                else:
                    self._active[pitch] -= 1
                    if not self._active[pitch]:
                        del self._active[pitch]
                        self._emit(pitch, False, beat, audible_s, now_s)
            self._cursor += 1
        return self.events[first_event:]
