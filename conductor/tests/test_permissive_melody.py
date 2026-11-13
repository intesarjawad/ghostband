from __future__ import annotations

import sys
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from ghostband.chart import Chart

ROOT = Path(__file__).resolve().parents[2]
BEATS = np.arange(8) * 0.5  # 120 bpm, so one second is two beats


@pytest.fixture
def modules(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    monkeypatch.setattr(sys, "path", [str(ROOT / "extract/permissive"), *sys.path])
    return SimpleNamespace(melody=import_module("melody"), runtime=import_module("run"))


def test_beat_positions_extend_the_grid_at_the_median_period(modules: SimpleNamespace) -> None:
    beats = np.array([0.0, 0.5, 1.0, 1.5])
    actual = modules.melody.beat_positions(np.array([-0.25, 0.25, 0.75, 1.5, 2.0]), beats)
    assert actual.tolist() == pytest.approx([-0.5, 0.5, 1.5, 3.0, 4.0])
    with pytest.raises(ValueError, match="Two beats"):
        modules.melody.beat_positions(np.array([0.0]), np.array([0.0]))


def test_skyline_keeps_the_top_note_and_drops_what_a_chart_cannot_use(
    modules: SimpleNamespace,
) -> None:
    notes = [
        (0.0, 2.0, 48, 0.9),  # bass line under the tune
        (0.0, 2.0, 72, 0.8),
        (1.0, 1.5, 76, 0.7),  # interrupts the held note for one beat
        (2.1, 2.15, 80, 0.9),  # a tenth of a beat is flicker
        (0.5, 1.0, 98, 0.9),  # above the chart's pitch range
        (3.0, 3.5, 70, 0.1),  # below the activation threshold
        (3.0, 5.0, 74, 0.6),  # rings past the end of the audio
    ]
    actual = modules.melody.extract_melody(notes, BEATS, 4.0)
    assert actual == [
        {"start_beat": 0.0, "end_beat": 2.0, "pitch": 72},
        {"start_beat": 2.0, "end_beat": 3.0, "pitch": 76},
        {"start_beat": 3.0, "end_beat": 4.0, "pitch": 72},
        {"start_beat": 6.0, "end_beat": 8.0, "pitch": 74},
    ]


def test_repeated_notes_stay_separate_and_pickups_are_clamped(modules: SimpleNamespace) -> None:
    notes = [(0.0, 0.5, 60, 0.9), (0.5, 1.0, 60, 0.9), (-0.5, 0.25, 64, 0.9)]
    actual = modules.melody.extract_melody(notes, BEATS, 4.0)
    assert actual == [
        {"start_beat": 0.0, "end_beat": 0.5, "pitch": 64},
        {"start_beat": 0.5, "end_beat": 1.0, "pitch": 60},
        {"start_beat": 1.0, "end_beat": 2.0, "pitch": 60},
    ]


def test_no_notes_give_an_empty_melody(modules: SimpleNamespace) -> None:
    assert modules.melody.extract_melody([], BEATS, 4.0) == []


def test_melody_fits_the_chart_contract(modules: SimpleNamespace) -> None:
    notes = [(0.0, 2.0, 72, 0.8), (1.0, 1.5, 76, 0.7), (3.0, 5.0, 74, 0.6)]
    data = modules.runtime.build_chart(Path("song.wav"), (BEATS, BEATS[::4]), 4.0, ["C"] * 8, "C")
    data["melody"] = modules.melody.extract_melody(notes, BEATS, 4.0)
    chart = Chart.model_validate(data)
    assert [(n.start_beat, n.end_beat, n.pitch) for n in chart.melody] == [
        (0.0, 2.0, 72),
        (2.0, 3.0, 76),
        (3.0, 4.0, 72),
        (6.0, 8.0, 74),
    ]
