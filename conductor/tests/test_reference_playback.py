"""Reference playback follows the chart clock and matches the band's loudness."""

import math
from importlib import import_module
from pathlib import Path

import numpy as np
import pytest
from ghostband.chart import Chart, ChordEvent, Section
from ghostband.engine_client import SAMPLE_RATE
from ghostband.errors import GhostbandError
from ghostband.fake_engine import FakeEngine
from ghostband.importing import read_chart
from ghostband.session import Session

ROOT = Path(__file__).resolve().parents[2]
sf = import_module("soundfile")


def reference_wav(path: Path, *, amplitude: float = 0.1, seconds: float = 4.0) -> Path:
    """A short stereo float take at the engine's rate; no audio device is opened."""
    time = np.arange(round(seconds * SAMPLE_RATE), dtype=np.float64) / SAMPLE_RATE
    wave = amplitude * np.sin(2 * np.pi * 997 * time)
    sf.write(str(path), np.column_stack((wave, wave)), SAMPLE_RATE, subtype="FLOAT")
    return path


def short_chart() -> Chart:
    return Chart(
        schema_version=1,
        title="Reference test",
        source="test",
        rights="own-work",
        extractor="manual",
        tempo_bpm=120,
        beat_times_s=[],
        time_signature=(4, 4),
        key="C",
        sections=[Section(name="A", start_beat=0, end_beat=8)],
        chords=[ChordEvent(start_beat=0, end_beat=8, symbol="C")],
        melody=[],
        duration_beats=8,
    )


def commands(engine: FakeEngine, name: str) -> list[dict[str, object]]:
    return [row.command for row in engine.commands if row.command["cmd"] == name]


def integer(command: dict[str, object], key: str) -> int:
    value = command[key]
    assert type(value) is int
    return value


def number(command: dict[str, object], key: str) -> float:
    value = command[key]
    assert type(value) is float
    return value


def test_import_measures_the_take_for_loudness_matching(tmp_path: Path) -> None:
    session = Session(fake_engine=True)
    session.reference_path = reference_wav(tmp_path / "reference_48k.wav", amplitude=0.1)
    # A 997 Hz stereo sine at amplitude 0.1 reads 20 log10(0.1) LUFS.
    assert session.reference_loudness == pytest.approx(-20.0, abs=0.1)
    session.close()


def test_toggling_reference_never_stops_the_transport(tmp_path: Path) -> None:
    now = [0.0]
    session = Session(fake_engine=True, clock=lambda: now[0])
    session.load_chart(short_chart())
    session.reference_path = reference_wav(tmp_path / "reference_48k.wav")
    session.control({"type": "toggle", "name": "reference", "enabled": True}, record_path=tmp_path)
    session.play()
    for step in range(1, 31):
        now[0] = step * 0.05
        session.tick()
    engine = session.engine
    assert isinstance(engine, FakeEngine)
    assert engine.playing is True
    after_play = len(engine.commands)
    session.control({"type": "toggle", "name": "reference", "enabled": False}, record_path=tmp_path)
    assert engine.playing is True
    assert "stop" not in [row.command["cmd"] for row in engine.commands[after_play:]]
    assert commands(engine, "ref_mix")[-1] == {"cmd": "ref_mix", "band_gain": 1.0, "ref_gain": 0.0}
    session.close()


def test_enabling_mid_playback_seeks_to_the_current_chart_beat(tmp_path: Path) -> None:
    now = [0.0]
    session = Session(fake_engine=True, clock=lambda: now[0])
    session.load_chart(short_chart())
    session.reference_path = reference_wav(tmp_path / "reference_48k.wav")
    session.play()
    for step in range(1, 13):
        now[0] = step * 0.05
        session.tick()
    session.control({"type": "toggle", "name": "reference", "enabled": True}, record_path=tmp_path)
    engine = session.engine
    assert isinstance(engine, FakeEngine)
    seek = commands(engine, "ref_play")[-1]
    at_sample = integer(seek, "at_sample")
    from_sample = integer(seek, "from_sample")
    assert session.scheduler is not None
    beat = session.scheduler.audible_beat(at_sample / SAMPLE_RATE)
    assert from_sample / SAMPLE_RATE == pytest.approx(beat * 60 / 120, abs=0.02)
    session.close()


def test_loop_wrap_returns_the_take_to_the_loop_start_on_time(tmp_path: Path) -> None:
    now = [0.0]
    session = Session(fake_engine=True, clock=lambda: now[0])
    session.load_chart(short_chart())
    session.reference_path = reference_wav(tmp_path / "reference_48k.wav")
    session.control({"type": "loop", "start": 2, "end": 4}, record_path=tmp_path)
    session.control({"type": "toggle", "name": "reference", "enabled": True}, record_path=tmp_path)
    session.play()
    for step in range(1, 27):
        now[0] = step * 0.05
        session.tick()
    engine = session.engine
    assert isinstance(engine, FakeEngine)
    seeks = commands(engine, "ref_play")
    # A 0.2 s pre-roll puts beat 2 at 9600 samples; the wrap at 1.2 s sends it back.
    assert [(seek["at_sample"], seek["from_sample"]) for seek in seeks] == [
        (9600, 48000),
        (57600, 48000),
    ]
    assert session.scheduler is not None
    wrap_sample = 57600 / SAMPLE_RATE
    assert integer(seeks[-1], "from_sample") / SAMPLE_RATE == pytest.approx(
        session.scheduler.audible_beat(wrap_sample) * 60 / 120, abs=0.02
    )
    assert engine.playing is True
    session.close()


def test_reference_is_disabled_away_from_full_tempo(tmp_path: Path) -> None:
    now = [0.0]
    session = Session(fake_engine=True, clock=lambda: now[0], tempo_range=(0.5, 1.5))
    session.load_chart(short_chart())
    session.reference_path = reference_wav(tmp_path / "reference_48k.wav")
    session.control({"type": "toggle", "name": "reference", "enabled": True}, record_path=tmp_path)
    session.play()
    now[0] = 0.1
    session.control({"type": "tempo_factor", "value": 1.5}, record_path=tmp_path)
    assert session.toggles["reference"] is False
    engine = session.engine
    assert isinstance(engine, FakeEngine)
    assert engine.playing is True
    assert commands(engine, "ref_mix")[-1] == {
        "cmd": "ref_mix",
        "band_gain": 1.0,
        "ref_gain": 0.0,
    }
    with pytest.raises(GhostbandError, match="Reference plays at 100 percent tempo"):
        session.control(
            {"type": "toggle", "name": "reference", "enabled": True}, record_path=tmp_path
        )
    session.control({"type": "tempo_factor", "value": 1.0}, record_path=tmp_path)
    session.control({"type": "toggle", "name": "reference", "enabled": True}, record_path=tmp_path)
    assert session.toggles["reference"] is True
    session.close()


def test_reference_gain_matches_the_band_from_the_tap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = Session(fake_engine=True)
    session.load_chart(short_chart())
    session.reference_path = reference_wav(tmp_path / "reference_48k.wav")
    session.reference_loudness = -20.0
    monkeypatch.setattr(session.tap_loudness, "loudness", lambda: -26.0)
    session.control({"type": "toggle", "name": "reference", "enabled": True}, record_path=tmp_path)
    engine = session.engine
    assert isinstance(engine, FakeEngine)
    mix = commands(engine, "ref_mix")[-1]
    assert mix["band_gain"] == 0.0
    gain = number(mix, "ref_gain")
    assert gain == pytest.approx(10 ** (-6 / 20))
    # The take after gain and the unmodified band sit at the same loudness.
    assert -20.0 + 20 * math.log10(gain) == pytest.approx(-26.0, abs=0.5)
    session.close()


def test_loading_a_new_chart_clears_the_reference(tmp_path: Path) -> None:
    session = Session(fake_engine=True)
    chart = read_chart(ROOT / "charts/greensleeves.chart.json")
    session.load_chart(chart)
    session.reference_path = reference_wav(tmp_path / "reference_48k.wav")
    session.control({"type": "toggle", "name": "reference", "enabled": True}, record_path=tmp_path)
    assert session.toggles["reference"] is True
    session.load_chart(chart)
    assert session.reference_path is None
    assert session.reference_loudness is None
    assert session.toggles["reference"] is False
    session.close()
