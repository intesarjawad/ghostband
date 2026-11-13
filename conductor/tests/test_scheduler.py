from collections import Counter
from math import ceil
from pathlib import Path

import pytest
from ghostband.chart import Chart, ChordEvent, NoteEvent, Section
from ghostband.chords import parse_chord, voice_chord
from ghostband.engine_client import EngineStatus
from ghostband.fake_engine import FakeEngine
from ghostband.scheduler import Scheduler

ROOT = Path(__file__).resolve().parents[2]


def short_chart() -> Chart:
    return Chart(
        schema_version=1,
        title="Transport test",
        source="test",
        rights="own-work",
        extractor="manual",
        tempo_bpm=120,
        beat_times_s=[],
        time_signature=(4, 4),
        key="C",
        sections=[Section(name="A", start_beat=0, end_beat=4)],
        chords=[ChordEvent(start_beat=0, end_beat=4, symbol="C")],
        melody=[
            NoteEvent(start_beat=0, end_beat=1, pitch=72),
            NoteEvent(start_beat=1, end_beat=3, pitch=74),
        ],
        duration_beats=4,
    )


def test_greensleeves_order_lookahead_and_sounding_pitches_at_full_tempo() -> None:
    chart = Chart.model_validate_json((ROOT / "charts/greensleeves.chart.json").read_text())
    now = [10.0]
    engine = FakeEngine(lambda: now[0], latency_samples=4800)
    lookahead_s = EngineStatus.from_message(engine.status()).lookahead_s()
    scheduler = Scheduler(chart, engine.send, lookahead_s=lookahead_s)
    scheduler.play(now[0])
    intervals: list[tuple[float, float, tuple[int, ...]]] = [
        (note.start_beat, note.end_beat, (note.pitch,)) for note in chart.melody
    ]
    previous: tuple[int, ...] = ()
    for chord in chart.chords:
        previous = voice_chord(parse_chord(chord.symbol), previous)
        intervals.append((chord.start_beat, chord.end_beat, previous))
    ticks = ceil(chart.duration_beats * 60 / chart.tempo_bpm * 1000)
    for tick in range(ticks + 1):
        now[0] = 10 + tick / 1000
        due = scheduler.step(now[0])
        if due:
            beat = due[-1].beat
            expected = {
                pitch
                for start, end, pitches in intervals
                if start <= beat < end
                for pitch in pitches
            }
            assert engine.active_notes == expected
            assert scheduler.step(now[0]) == []
    assert not scheduler.running
    assert not engine.active_notes
    assert scheduler.events
    deadlines = [event.audible_time for event in scheduler.events]
    assert deadlines == sorted(deadlines)
    errors = [abs(event.audible_time - event.emit_time - lookahead_s) for event in scheduler.events]
    assert max(errors) <= 0.001
    assert scheduler.events[0].emit_time == 10
    assert scheduler.events[0].audible_time == pytest.approx(10 + lookahead_s)
    assert scheduler.audible_beat(10) == 0


def test_loop_clips_held_notes_and_resends_chord_at_every_wrap() -> None:
    now = [0.0]
    engine = FakeEngine(lambda: now[0])
    scheduler = Scheduler(short_chart(), engine.send, lookahead_s=0.2)
    scheduler.set_loop((0.5, 1.5), now[0])
    scheduler.play(now[0])
    for tick in range(2001):
        now[0] = tick / 1000
        scheduler.step(now[0])
    wraps = [event for event in scheduler.events if event.audible_time == pytest.approx(0.7)]
    releases = [event for event in wraps if event.command["cmd"] == "noteoff"]
    attacks = [event for event in wraps if event.command["cmd"] == "noteon"]
    assert {event.command["pitch"] for event in releases} == {36, 55, 60, 64, 74}
    assert {event.command["pitch"] for event in attacks} == {36, 55, 60, 64, 72}
    assert wraps == releases + attacks
    assert all(event.beat == 1.5 for event in releases)
    assert all(event.beat == 0.5 for event in attacks)
    scheduler.stop(now[0])
    assert not engine.active_notes


def _pitch_events(scheduler: Scheduler, pitch: int) -> list[tuple[float, object]]:
    return [
        (event.beat, event.command["cmd"])
        for event in scheduler.events
        if event.command.get("pitch") == pitch
    ]


def test_pulse_strikes_the_bass_on_every_beat_and_holds_the_upper_voicing() -> None:
    chart = short_chart().model_copy(
        update={
            "chords": [
                ChordEvent(start_beat=0, end_beat=2.5, symbol="C"),
                ChordEvent(start_beat=2.5, end_beat=4, symbol="G"),
            ],
            "melody": [],
        }
    )
    engine = FakeEngine(lambda: 0)
    scheduler = Scheduler(chart, engine.send, lookahead_s=0.2, pulse=True)
    scheduler.play(0)
    scheduler.step(10)
    assert _pitch_events(scheduler, 36) == [
        (0, "noteon"),
        (0.6, "noteoff"),
        (1, "noteon"),
        (1.6, "noteoff"),
        (2, "noteon"),
        (2.5, "noteoff"),
    ]
    assert _pitch_events(scheduler, 43) == [
        (2.5, "noteon"),
        (3, "noteoff"),
        (3, "noteon"),
        (3.6, "noteoff"),
    ]
    assert _pitch_events(scheduler, 60) == [(0, "noteon"), (2.5, "noteoff")]
    assert not engine.active_notes


def test_without_pulse_the_bass_sounds_once_per_chord() -> None:
    engine = FakeEngine(lambda: 0)
    scheduler = Scheduler(short_chart(), engine.send, lookahead_s=0.2)
    scheduler.play(0)
    scheduler.step(10)
    assert _pitch_events(scheduler, 36) == [(0, "noteon"), (4, "noteoff")]


def test_tempo_rebases_from_next_beat_without_moving_earlier_events() -> None:
    now = [0.0]
    engine = FakeEngine(lambda: now[0])
    scheduler = Scheduler(short_chart(), engine.send, lookahead_s=0.2)
    scheduler.play(0)
    scheduler.step(0)
    now[0] = 0.1
    assert scheduler.set_tempo(2.0, now[0]) == 1
    for tick in range(101, 1501):
        now[0] = tick / 1000
        scheduler.step(now[0])
    times = {
        (event.beat, event.command["cmd"], event.command["pitch"]): event.audible_time
        for event in scheduler.events
    }
    assert times[0, "noteon", 72] == pytest.approx(0.2)
    assert times[1, "noteon", 74] == pytest.approx(0.7)
    assert times[3, "noteoff", 74] == pytest.approx(1.2)
    assert times[4, "noteoff", 36] == pytest.approx(1.45)
    assert scheduler.audible_beat(0.7) == pytest.approx(1)
    assert scheduler.audible_beat(0.95) == pytest.approx(2)


def test_tempo_edit_preserves_already_queued_next_beat() -> None:
    engine = FakeEngine(lambda: 0)
    scheduler = Scheduler(short_chart(), engine.send, lookahead_s=0.8)
    scheduler.play(0)
    scheduler.step(0.5)
    emitted = list(scheduler.events)
    assert scheduler.set_tempo(0.5, 0.6) == 2
    assert scheduler.set_tempo(2, 0.6) == 2
    scheduler.step(2)
    assert scheduler.events[: len(emitted)] == emitted
    release = next(
        event for event in scheduler.events if event.command == {"cmd": "noteoff", "pitch": 74}
    )
    assert release.audible_time == pytest.approx(2.05)


def test_overlapping_parts_do_not_release_each_others_pitch() -> None:
    chart = short_chart().model_copy(
        update={
            "melody": [
                NoteEvent(start_beat=0, end_beat=1, pitch=60),
                NoteEvent(start_beat=0.5, end_beat=2, pitch=60),
            ]
        }
    )
    engine = FakeEngine(lambda: 0)
    scheduler = Scheduler(chart, engine.send, lookahead_s=0.2)
    scheduler.play(0)
    scheduler.step(1.5)
    assert 60 in engine.active_notes
    scheduler.step(2)
    assert not engine.active_notes
    pitches = [event.command["cmd"] for event in scheduler.events if event.command["pitch"] == 60]
    assert pitches == ["noteon", "noteoff"]


def test_seek_restores_mid_interval_notes_and_stop_clears_lookahead() -> None:
    now = [0.0]
    engine = FakeEngine(lambda: now[0])
    scheduler = Scheduler(short_chart(), engine.send, lookahead_s=0.2)
    scheduler.play(0)
    scheduler.step(0)
    now[0] = 0.1
    scheduler.seek(2, now[0])
    assert not engine.active_notes
    scheduler.step(now[0])
    assert engine.active_notes == {36, 55, 60, 64, 74}
    assert scheduler.audible_beat(now[0]) == 2
    scheduler.stop(now[0])
    assert not engine.active_notes
    assert scheduler.step(10) == []


def test_empty_chart_late_tick_and_repeated_notes() -> None:
    chart = short_chart().model_copy(
        update={
            "chords": [],
            "melody": [
                NoteEvent(start_beat=0, end_beat=1, pitch=72),
                NoteEvent(start_beat=1, end_beat=2, pitch=72),
            ],
        }
    )
    engine = FakeEngine(lambda: 0)
    scheduler = Scheduler(chart, engine.send, lookahead_s=0.2)
    scheduler.play(0)
    scheduler.step(10)
    assert [event.command["cmd"] for event in scheduler.events] == [
        "noteon",
        "noteoff",
        "noteon",
        "noteoff",
    ]
    assert not scheduler.running
    assert not engine.active_notes
    silent = Scheduler(chart.model_copy(update={"melody": []}), engine.send, lookahead_s=0)
    silent.play(10)
    assert silent.step(12) == []
    assert not silent.running


@pytest.mark.parametrize("value", [-1.0, float("nan"), float("inf")])
def test_invalid_lookahead_is_rejected(value: float) -> None:
    with pytest.raises(ValueError, match="Lookahead"):
        Scheduler(short_chart(), lambda _: None, lookahead_s=value)


def test_invalid_transport_edits_and_backwards_clock_are_rejected() -> None:
    scheduler = Scheduler(short_chart(), lambda _: None, lookahead_s=0.2)
    for loop in [(1, 1), (-1, 1), (0, 5), (0, float("inf"))]:
        with pytest.raises(ValueError, match="Loop"):
            scheduler.set_loop(loop, 0)
    for factor in [0, -1, float("nan")]:
        with pytest.raises(ValueError, match="Tempo"):
            scheduler.set_tempo(factor, 0)
    with pytest.raises(ValueError, match="Playback"):
        scheduler.play(0, 4)
    scheduler.play(1)
    with pytest.raises(ValueError, match="clock"):
        scheduler.step(0)
    scheduler.step(1)
    counts = Counter(event.command["cmd"] for event in scheduler.events)
    assert counts["noteon"] == 5


def _after_half_beat_pickup() -> Chart:
    return short_chart().model_copy(
        update={
            "sections": [
                Section(name="Pickup", start_beat=0, end_beat=0.5),
                Section(name="A", start_beat=0.5, end_beat=4),
            ],
            "melody": [],
        }
    )


def test_after_a_pickup_the_beat_log_counts_from_the_bar_line() -> None:
    chart = _after_half_beat_pickup()
    assert chart.beat_phase == 0.5
    scheduler = Scheduler(chart, FakeEngine(lambda: 0).send, lookahead_s=0.2)
    scheduler.play(0)
    assert [beat for beat, _ in scheduler.beat_log()] == [0.5, 1.5, 2.5, 3.5]


def test_after_a_pickup_the_pulse_strikes_on_the_counted_beats() -> None:
    engine = FakeEngine(lambda: 0)
    scheduler = Scheduler(_after_half_beat_pickup(), engine.send, lookahead_s=0.2, pulse=True)
    scheduler.play(0)
    scheduler.step(10)
    strikes = [beat for beat, command in _pitch_events(scheduler, 36) if command == "noteon"]
    assert strikes == [0, 0.5, 1.5, 2.5, 3.5]
