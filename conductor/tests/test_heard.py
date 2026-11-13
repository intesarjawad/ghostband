"""The heard row names played harmony from tapped audio, independently of the chart."""

import base64
from pathlib import Path

import numpy as np
import pytest
from ghostband.chart import Chart
from ghostband.engine_client import SAMPLE_RATE, Command
from ghostband.evaluation.chord_detector import beat_window, majmin
from ghostband.fake_engine import STATUS_INTERVAL_S, FakeEngine
from ghostband.heard import BLOCK_SAMPLES, HeardEvent, HeardRow, decode_tap
from ghostband.importing import read_chart
from ghostband.session import Session
from numpy.typing import NDArray

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = sorted((ROOT / "fixtures").glob("*.wav"))
LISTEN_SECONDS = 8
C4, E4, G4 = 261.63, 329.63, 392.0
TRIADS = tuple(
    note + suffix
    for note in ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
    for suffix in ("", "m")
)


def tap_message(sample: int, pcm: NDArray[np.float32]) -> Command:
    """Encode a tap block the way the engine does: base64 little-endian float32."""
    encoded = base64.b64encode(np.asarray(pcm, dtype="<f4").tobytes()).decode("ascii")
    return {"event": "tap", "sample": sample, "pcm": encoded}


def sine_triad(frequencies: tuple[float, ...], seconds: float) -> NDArray[np.float32]:
    time = np.arange(round(seconds * SAMPLE_RATE), dtype=np.float64) / SAMPLE_RATE
    wave = np.zeros_like(time)
    for frequency in frequencies:
        wave += np.sin(2 * np.pi * frequency * time)
    return np.asarray(0.25 * wave / len(frequencies), dtype=np.float32)


def chart_symbol(chart: Chart, beat: int) -> str | None:
    """Return the chart chord sounding through most of beat k."""
    longest, symbol = 0.0, None
    for chord in chart.chords:
        overlap = min(chord.end_beat, beat + 1) - max(chord.start_beat, beat)
        if overlap > longest:
            longest, symbol = overlap, chord.symbol
    return symbol


def test_decode_tap_reads_little_endian_float32() -> None:
    pcm = np.zeros(BLOCK_SAMPLES, np.float32)
    pcm[0], pcm[1], pcm[-1] = 1.0, -0.5, 0.25
    sample, audio = decode_tap(tap_message(9600, pcm))
    assert sample == 9600
    assert audio.tolist()[:2] == [1.0, -0.5]
    assert audio[-1] == 0.25


@pytest.mark.parametrize(
    "message",
    [
        {"sample": 0, "pcm": base64.b64encode(bytes(BLOCK_SAMPLES * 4)).decode("ascii")},
        {"event": "tap", "sample": -4800, "pcm": "AAAAAAAA"},
        {"event": "tap", "sample": 0, "pcm": "not base64!"},
        {"event": "tap", "sample": 0, "pcm": base64.b64encode(bytes(8)).decode("ascii")},
        {
            "event": "tap",
            "sample": 0,
            "pcm": base64.b64encode(bytes([0, 0, 192, 127]) * BLOCK_SAMPLES).decode("ascii"),
        },
    ],
)
def test_decode_tap_rejects_malformed_events(message: Command) -> None:
    with pytest.raises(ValueError, match="Tap"):
        decode_tap(message)


def test_beat_window_names_the_best_triad() -> None:
    times = np.array([0.0, 0.25, 0.5, 1.5])
    chroma = np.zeros((12, times.size))
    chroma[0, 0] = 1.0
    chroma[4, 0] = 1.0
    chroma[7, 0] = 0.6
    chroma[[9, 0, 4], 1] = 1.0
    chroma[[0, 4, 7], 3] = 1.0
    # Cosine of a C major template with chroma 1, 1, 0.6 over its three tones.
    expected = 2.6 / (np.sqrt(2.36) * np.sqrt(3.0))
    guess = beat_window(times, chroma, 0.0, 0.25)
    assert guess is not None
    assert guess.symbol == "C"
    assert guess.confidence == pytest.approx(expected, abs=1e-9)
    minor = beat_window(times, chroma, 0.1, 0.4)
    assert minor is not None
    assert (minor.symbol, minor.confidence) == ("Am", 1.0)
    silent = beat_window(times, chroma, 0.4, 0.6)
    assert silent is not None
    assert (silent.symbol, silent.confidence) == ("N.C.", 0.0)
    assert beat_window(times, chroma, 3.0, 4.0) is None


def test_majmin_reduces_chart_symbols() -> None:
    assert majmin("Am7") == "Am"
    assert majmin("Cm") == "Cm"
    assert majmin("Gmaj7") == "G"
    assert majmin("B7") == "B"
    assert majmin("Cdim") == "Cm"
    assert majmin("Eb") == "D#"
    assert majmin("Bb7") == "A#"
    assert majmin("N.C.") == "N.C."


def test_heard_row_emits_one_chord_per_completed_beat() -> None:
    audio = sine_triad((C4, E4, G4), 4.0)
    row = HeardRow(beat_seconds=1.0)
    events: list[HeardEvent] = []
    for start in range(0, audio.size - BLOCK_SAMPLES + 1, BLOCK_SAMPLES):
        events.extend(row.push(start, audio[start : start + BLOCK_SAMPLES]))
        if start + BLOCK_SAMPLES < SAMPLE_RATE:
            assert not events
    assert [event.beat for event in events] == [0, 1, 2, 3]
    assert {event.symbol for event in events} == {"C"}


def test_heard_row_waits_for_the_whole_beat_span() -> None:
    beat_seconds = 0.75
    audio = sine_triad((C4, E4, G4), 3.0)
    row = HeardRow(beat_seconds=beat_seconds)
    arrivals: dict[int, float] = {}
    for start in range(0, audio.size - BLOCK_SAMPLES + 1, BLOCK_SAMPLES):
        for event in row.push(start, audio[start : start + BLOCK_SAMPLES]):
            arrivals[event.beat] = (start + BLOCK_SAMPLES) / SAMPLE_RATE
    assert arrivals == {
        0: pytest.approx(0.8),
        1: pytest.approx(1.5),
        2: pytest.approx(2.3),
        3: pytest.approx(3.0),
    }
    for beat, arrival in arrivals.items():
        assert arrival - (beat + 1) * beat_seconds <= STATUS_INTERVAL_S


@pytest.mark.parametrize("path", FIXTURES, ids=lambda path: path.stem)
def test_fixture_replay_names_the_chart_for_half_the_beats(path: Path) -> None:
    chart = read_chart(ROOT / "charts" / path.with_suffix(".chart.json").name)
    now = [0.0]
    engine = FakeEngine(lambda: now[0], tap_fixture=path)
    engine.send({"cmd": "tap", "on": True})
    engine.send({"cmd": "play"})
    row = HeardRow(beat_seconds=60 / chart.tempo_bpm)
    events: list[HeardEvent] = []
    for step in range(1, round(LISTEN_SECONDS / STATUS_INTERVAL_S) + 1):
        now[0] = step * STATUS_INTERVAL_S
        for message in engine.poll_taps():
            sample, pcm = decode_tap(message)
            events.extend(row.push(sample, pcm))
    beats = [event.beat for event in events]
    assert beats == list(range(beats[0], beats[0] + len(beats)))
    assert {event.symbol for event in events} <= {*TRIADS, "N.C."}
    assert all(0.0 <= event.confidence <= 1.0 for event in events)
    compared = agreed = 0
    for event in events:
        symbol = chart_symbol(chart, event.beat)
        if symbol is None or majmin(symbol) == "N.C.":
            continue
        compared += 1
        agreed += majmin(symbol) == event.symbol
    # These charts change chord on fractions of a beat, so a beat-long window
    # legitimately straddles two chords; half the beats is the honest bar.
    assert 2 * agreed >= compared


def test_session_sends_tap_on_play_and_publishes_heard_rows() -> None:
    now = [10.0]
    session = Session(fake_engine=True, clock=lambda: now[0])
    session.load_chart(read_chart(ROOT / "charts/greensleeves.chart.json"))
    session.play()
    engine = session.engine
    assert isinstance(engine, FakeEngine)
    commands = [row.command for row in engine.commands]
    assert {"cmd": "tap", "on": True} in commands
    assert commands.index({"cmd": "tap", "on": True}) < commands.index({"cmd": "play"})
    updates: list[dict[str, object]] = []
    for step in range(1, 121):
        now[0] = 10.0 + step * 0.05
        session.tick()
        updates.extend(session.drain_heard())
    session.close()
    assert [update["beat"] for update in updates] == [float(beat) for beat in range(7)]
    assert all(update["event"] == "heard" for update in updates)
    assert {update["symbol"] for update in updates} <= {*TRIADS, "N.C."}
    assert {update["expected"] for update in updates} == {"Em", "D"}
    assert any(update["matches"] is True for update in updates)
    assert any(update["matches"] is False for update in updates)
    assert session.drain_heard() == []
